"""
Execution engine for adaptation plans (ADR-0004, ADR-0018, ADR-0019).

Steps:
  1. deploy_vdb   Σ′_S is deployed as a NEW VERSION next to the version that is serving.
                  If it is FAILED, the old version is untouched and the execution stops.
  2. validate     ℳ′ and 𝒯′ are written through the agent, then `ontop validate` runs against
                  the new VDB version. If it fails, the artifacts are restored and the new VDB
                  is removed.
  3. switch       Connections move to the new version (connection type ANY).
  4. reload_ontop Ontop is reloaded so that it reads ℳ′ and 𝒯′.
  5. verify       The answers of the OBDF are checked with graph fingerprints, according to the
                  adaptation pattern.
  6. rollback     If verification fails: connections are moved back, the artifacts restored,
                  Ontop reloaded, and the execution marked rolled_back.

The `bluegreen` strategy (ADR-0022, default) applies blue-green to the OBDA layer as well:
  1. deploy_vdb   as above; version N keeps serving.
  2. validate     ℳ′ and 𝒯′ are written to the slot of the STANDBY Ontop instance, which is
                  bound to version N+1, and `ontop validate` runs with that instance's volume.
  3. start_ontop  the standby instance is started; the active instance keeps serving.
  4. verify       the graph fingerprint is taken DIRECTLY from the standby instance, before any
                  traffic is switched, and compared with the fingerprint of the active instance.
  5. switch       the proxy is switched to the standby instance, its artifacts are promoted, the
                  old instance is stopped, and version N+1 accepts connections without a version
                  (ANY).
  5. rollback     if verification fails: the standby instance is stopped and version N+1 removed;
                  users never see a version that has not been verified.

The baseline fingerprint is taken AFTER the DDL was applied at the source, because the Executor
works on events. If the whole fingerprint fails at that moment (e.g. a view without keys makes
Ontop read a column that has already been dropped, finding of VM test A007/A008 2026-10-09), the
fingerprint is taken per predicate: failing predicates are recorded, and the same method is used
for the new instance so that both are comparable.

Every step is reported to Knowledge, so its trail remains even if the Executor stops.
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from rdflib import OWL, RDF, Graph, Namespace

from .artifacts import ontology, r2rml, vdb

log = logging.getLogger('ascam.executor')
# Transient errors while recording the result of an execution: network, 5xx, and 409 from a read
# that overtook the commit of the previous request (finding of evaluation F6).
FINISH_ATTEMPTS = 3
TRANSIENT_STATUS = {409, 500, 502, 503, 504}


@dataclass
class Context:
    obdf_id: int
    plan: dict
    execution_id: int
    vdb_name: str
    old_version: str
    new_version: str
    deployment: str
    backups: dict[str, str] = field(default_factory=dict)
    baseline: dict[str, int] = field(default_factory=dict)
    timings: dict[str, int] = field(default_factory=dict)
    predicates: set[str] = field(default_factory=set)     # candidate predicates for per-predicate fingerprints
    per_predicate: bool = False                           # the whole fingerprint failed on the old instance
    baseline_failed: list[str] = field(default_factory=list)


class ExecutionError(Exception):
    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail or {}


RR = Namespace('http://www.w3.org/ns/r2rml#')


def candidate_predicates(old_mapping: str, new_mapping: str, ontology_text: str) -> set[str]:
    """Predicates that can appear in the graph: rr:predicate constants of ℳ and ℳ′, properties
    declared in 𝒯, and rdf:type."""
    result = {str(RDF.type)}
    for text, public_id in ((old_mapping, 'urn:ascam:mapping'), (new_mapping, 'urn:ascam:mapping'),
                            (ontology_text, None)):
        try:
            graph = Graph().parse(data=text, format='turtle', publicID=public_id)
        except Exception:                            # noqa: BLE001 — only for the fingerprint fallback
            log.exception('artifact cannot be parsed for the predicate list')
            continue
        result |= {str(o) for o in graph.objects(None, RR.predicate)}
        for kind in (OWL.ObjectProperty, OWL.DatatypeProperty):
            result |= {str(s) for s in graph.subjects(RDF.type, kind)}
    return result


def _actions(plan: dict, artifact: str) -> list[dict]:
    out = []
    for action in plan.get('actions', []):
        if action['artifact'] != artifact:
            continue
        out.append({'operation': action['operation'], **(action.get('params') or {})})
    return out


def _type_lookup(type_mappings: list[dict], dbms: str | None = None):
    table_types = {}
    for row in type_mappings:
        table_types.setdefault(row['native_type'].lower(), row['teiid_type'])
    def lookup(native_type: str | None) -> str | None:
        if not native_type:
            return None
        base = native_type.lower().split('(')[0].strip()
        length_suffix = native_type[len(base):].strip() if native_type.lower().startswith(base) else ''
        teiid = table_types.get(native_type.lower()) or table_types.get(base)
        if teiid is None:
            return None
        return f'{teiid}{length_suffix}' if length_suffix.startswith('(') and teiid == 'string' else teiid
    return lookup


def _xsd_for(type_mappings: list[dict], native_type: str | None) -> str | None:
    if not native_type:
        return None
    base = native_type.lower().split('(')[0].strip()
    for row in type_mappings:
        if row['native_type'].lower() in (native_type.lower(), base):
            return row['xsd_datatype']
    return None


class Executor:
    def __init__(self, knowledge, admin, agent, sparql, obdf_id: int, *, sleep=time.sleep,
                 teiid_jdbc_host: str = 'teiid', teiid_jdbc_port: int = 31000,
                 obda_strategy: str = 'restart', sparql_for=None, view_statement: str = 'alter'):
        if obda_strategy not in ('restart', 'bluegreen'):
            raise ValueError(f'unknown OBDA strategy: {obda_strategy!r}')
        if obda_strategy == 'bluegreen' and sparql_for is None:
            raise ValueError('the bluegreen strategy requires sparql_for (a SPARQL client per instance)')
        self.knowledge = knowledge
        self.admin = admin
        self.agent = agent
        self.sparql = sparql
        self.obdf_id = obdf_id
        self.sleep = sleep
        self.jdbc = (teiid_jdbc_host, teiid_jdbc_port)
        self.obda_strategy = obda_strategy
        self.sparql_for = sparql_for
        self.view_statement = view_statement

    # ── reporting ───────────────────────────────────────────────────────────────
    def _step(self, ctx: Context, seq: int, name: str, status: str, started: float,
              **detail: Any) -> None:
        # `detail` must not use keys that clash with the parameters of this function
        assert not {'seq', 'name', 'status'} & set(detail), 'detail keys clash'
        duration = int((time.perf_counter() - started) * 1000)
        ctx.timings[name] = duration
        self.knowledge.add_step(ctx.execution_id, seq=seq, name=name, status=status,
                                detail={'duration_ms': duration, **detail},
                                finished_at=datetime.now(timezone.utc).isoformat())

    # ── steps ───────────────────────────────────────────────────────────────────
    def _prepare_artifacts(self, plan: dict, active_version: dict) -> dict:
        type_mappings = self.knowledge.type_mappings(self.obdf_id)
        xml = self.knowledge.artifact(active_version['id'], 'vdb_xml')['content']
        new_version = vdb.next_version(xml)
        new_xml, statements = vdb.apply_actions(xml, _actions(plan, 'vdb'),
                                                type_lookup=_type_lookup(type_mappings),
                                                new_version=new_version,
                                                view_statement=self.view_statement)
        mapping = self.agent.artifact('r2rml')
        ont = self.agent.artifact('ontology')
        mapping_actions = []
        for action in _actions(plan, 'r2rml'):
            action_copy = dict(action)
            if action['operation'] == 'add_predicate_object_map':
                # the target TriplesMap comes from the plan (Knowledge lineage); the table name is
                # only a fallback for older plans that do not carry it yet
                action_copy.setdefault('table', (plan['impact']['targets'] or [{}])[0].get('table')
                                       or _table_from_vdb(plan))
                action_copy['datatype_iri'] = _expand(_xsd_for(type_mappings, _column_type(plan)))
            mapping_actions.append(action_copy)
        ontology_actions = []
        for action in _actions(plan, 'ontology'):
            action_copy = dict(action)
            if action['operation'] == 'add_datatype_property':
                action_copy['range_iri'] = _expand(_xsd_for(type_mappings, _column_type(plan)))
            ontology_actions.append(action_copy)
        return {
            'vdb_xml': new_xml, 'vdb_statements': statements, 'vdb_version': new_version,
            'r2rml': r2rml.apply_actions(mapping['content'], mapping_actions),
            'r2rml_sha': mapping['sha256'],
            'ontology': ontology.apply_actions(ont['content'], ontology_actions),
            'ontology_sha': ont['sha256'],
            'predicates': candidate_predicates(mapping['content'],
                                               r2rml.apply_actions(mapping['content'], mapping_actions),
                                               ont['content']),
        }

    def _fingerprint(self, ctx: Context, sparql) -> tuple[dict[str, int], list[str]]:
        """(fingerprint, predicates whose query failed). The method is fixed at the baseline."""
        if not ctx.per_predicate:
            try:
                return sparql.fingerprint(), []
            except Exception as exc:                # noqa: BLE001
                if ctx.baseline or ctx.baseline_failed:
                    raise                           # the new instance must answer the whole fingerprint
                log.warning('whole fingerprint failed on the old instance (%s); per predicate', exc)
                ctx.per_predicate = True
        return sparql.fingerprint_per_predicate(sorted(ctx.predicates))

    def _take_baseline(self, ctx: Context) -> None:
        ctx.baseline, ctx.baseline_failed = self._fingerprint(ctx, self.sparql)

    def _compare(self, ctx: Context, sparql=None) -> tuple[bool, dict]:
        after, failed = self._fingerprint(ctx, sparql or self.sparql)
        before = ctx.baseline
        failed_before = set(ctx.baseline_failed)
        pattern = ctx.plan.get('pattern')
        predicate = _target_predicate(ctx.plan)
        missing = sorted(set(before) - set(after))
        details = {'predicates_before': len(before), 'predicates_after': len(after),
                   'missing': missing, 'target_predicates': predicate}
        if ctx.per_predicate:
            details.update(fingerprint='per_predicate', failed_before=sorted(failed_before),
                           failed_after=failed)
        deprecated = _deprecated_predicates(ctx.plan)
        if pattern == 'P-002':
            # One column can be exposed by several predicates, including through views (ADR-0023):
            # every deprecated predicate must disappear, and only those may disappear.
            # Predicates that failed on the old instance and are not targets must answer now.
            # If nothing is deprecated (keep_property: another mapping still fills the predicate),
            # deprecated = ∅, so no predicate may disappear at all.
            ok = (not failed and not (deprecated & set(after)) and set(missing) <= deprecated
                  and (failed_before - deprecated) <= set(after))
            details['target_predicates'] = sorted(deprecated)
            details['expectation'] = ('target predicates disappear, other predicates remain' if deprecated
                                      else 'no predicate disappears (the predicate is still used)')
        elif pattern == 'P-003':
            # predicates that failed on the old instance (the column was already renamed) cannot
            # be compared by value, but they must answer again
            ok = (not failed and all(after.get(p) == n for p, n in before.items())
                  and set(after) - set(before) <= failed_before)
            details['expectation'] = 'answers identical to those before the adaptation'
        else:                                        # P-001: the new column may still be empty
            ok = not failed and not missing
            details['expectation'] = 'no predicate disappears'
        return ok, details

    def _restore_artifacts(self, ctx: Context) -> dict:
        restored = {}
        for kind, backup_id in ctx.backups.items():
            if backup_id:
                restored[kind] = self.agent.restore(backup_id)['sha256'][:12]
        return restored

    # ── main flow ───────────────────────────────────────────────────────────────
    def execute(self, plan: dict) -> dict:
        active_version = self.knowledge.active_version(self.obdf_id)
        if plan['base_spec_version_id'] != active_version['id']:
            # marked superseded so that it is not retried in every cycle (finding of evaluation F6)
            try:
                self.knowledge.supersede(plan['id'], f"base version {plan['base_spec_version_id']} "
                                                     f"is not the active version {active_version['id']}")
            except Exception:                       # noqa: BLE001
                pass
            raise ExecutionError('the plan was built on another specification version',
                                 {'base': plan['base_spec_version_id'], 'active': active_version['id']})
        execution = self.knowledge.start_execution(plan['id'])
        # Once the execution has started, every exit must close it: an execution left `running`
        # never finishes and blocks the next cycles (finding of F6).
        ctx = None
        try:
            artifacts = self._prepare_artifacts(plan, active_version)
            ctx = Context(obdf_id=self.obdf_id, plan=plan, execution_id=execution['id'],
                          predicates=artifacts.get('predicates') or set(),
                          vdb_name=active_version['teiid_vdb_name'],
                          old_version=str(active_version['teiid_vdb_version']),
                          new_version=artifacts['vdb_version'],
                          deployment=f"{active_version['teiid_vdb_name']}-{artifacts['vdb_version']}-vdb.xml")
            started_total = time.perf_counter()
            if self.obda_strategy == 'bluegreen':
                return self._run_bluegreen(ctx, artifacts, started_total)
            self._deploy(ctx, artifacts)
            self._write_and_validate(ctx, artifacts)
            self._take_baseline(ctx)
            self._switch(ctx)
            self._reload(ctx)
            ok, details = self._verify(ctx)
            if not ok:
                return self._rollback(ctx, details, started_total)
            return self._complete(ctx, started_total)
        except ExecutionError as exc:
            self._finish(execution['id'], status='failed',
                         timings=ctx.timings if ctx else {},
                         failure={'message': str(exc), **exc.detail})
            raise
        except Exception as exc:
            last_step = next(reversed(ctx.timings), None) if ctx else None
            try:
                self._finish(execution['id'], status='failed',
                             timings=ctx.timings if ctx else {},
                             failure={'message': f'unexpected error: {type(exc).__name__}: {exc}'[:500],
                                      'after_step': last_step})
            except Exception:                       # noqa: BLE001 — the original error matters more
                log.exception('execution %s cannot be closed', execution['id'])
            raise

    def _finish(self, execution_id: int, **body) -> dict:
        """Records the end of an execution, retrying on transient errors."""
        for attempt in range(1, FINISH_ATTEMPTS + 1):
            try:
                return self.knowledge.finish_execution(execution_id, **body)
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                transient = (isinstance(exc, httpx.TransportError)
                             or exc.response.status_code in TRANSIENT_STATUS)
                if not transient or attempt == FINISH_ATTEMPTS:
                    raise
                log.warning('recording the end of execution %s failed (%s); retrying',
                            execution_id, exc)
                self.sleep(0.5 * attempt)

    def _deploy(self, ctx: Context, artifacts: dict) -> None:
        started = time.perf_counter()
        self.admin.deploy(ctx.deployment, artifacts['vdb_xml'].encode())
        status, errors = self.admin.wait_until_settled(ctx.vdb_name, ctx.new_version,
                                                       sleep=self.sleep)
        self.knowledge.add_validation(ctx.execution_id, validator='teiid_status',
                                      passed=status == 'ACTIVE',
                                      details={'status': status, 'errors': errors,
                                               'vdb_version': ctx.new_version})
        if status != 'ACTIVE':
            self._step(ctx, 1, 'deploy_vdb', 'failed', started, vdb_status=status,
                       errors=errors)
            self.admin.undeploy(ctx.deployment)
            raise ExecutionError(f'VDB version {ctx.new_version} has status {status}',
                                 {'errors': errors})
        self._step(ctx, 1, 'deploy_vdb', 'succeeded', started, vdb_version=ctx.new_version,
                   statements=artifacts['vdb_statements'])

    def _write_and_validate(self, ctx: Context, artifacts: dict) -> None:
        started = time.perf_counter()
        try:
            ctx.backups['r2rml'] = self.agent.write('r2rml', artifacts['r2rml'],
                                                    artifacts['r2rml_sha'])['backup_id']
            ctx.backups['ontology'] = self.agent.write('ontology', artifacts['ontology'],
                                                       artifacts['ontology_sha'])['backup_id']
            host, port = self.jdbc
            db_url = (f'jdbc:teiid:{ctx.vdb_name}@mm://{host}:{port}'
                      f';version={ctx.new_version}')
            result = self.agent.validate(db_url)
        except Exception as exc:                    # noqa: BLE001
            self._restore_artifacts(ctx)
            self.admin.undeploy(ctx.deployment)
            self._step(ctx, 2, 'validate', 'failed', started, error=str(exc)[:300])
            raise ExecutionError(f'writing or validating the artifacts failed: {exc}') from exc

        self.knowledge.add_validation(ctx.execution_id, validator='ontop_validate',
                                      passed=bool(result.get('ok')),
                                      details={'exit_code': result.get('exit_code'),
                                               'output': (result.get('output') or '')[-1000:]})
        if not result.get('ok'):
            self._restore_artifacts(ctx)
            self.admin.undeploy(ctx.deployment)
            self._step(ctx, 2, 'validate', 'failed', started, exit_code=result.get('exit_code'))
            raise ExecutionError('ontop validate rejected the new artifacts',
                                 {'output': (result.get('output') or '')[-500:]})
        self._step(ctx, 2, 'validate', 'succeeded', started, exit_code=0)

    def _switch(self, ctx: Context) -> None:
        started = time.perf_counter()
        self.admin.set_connection_type(ctx.vdb_name, ctx.new_version, 'ANY')
        self._step(ctx, 3, 'switch', 'succeeded', started, connection_type='ANY',
                   vdb_version=ctx.new_version)

    def _reload(self, ctx: Context) -> None:
        started = time.perf_counter()
        result = self.agent.reload()
        status = 'succeeded' if result.get('ok') else 'failed'
        self._step(ctx, 4, 'reload_ontop', status, started, **{k: result.get(k) for k in
                                                               ('stop_ms', 'ready_ms', 'total_ms',
                                                                'error')})
        if not result.get('ok'):
            raise ExecutionError(f"reloading Ontop failed: {result.get('error')}")

    def _verify(self, ctx: Context, sparql=None, seq: int = 5,
                **extra: Any) -> tuple[bool, dict]:
        started = time.perf_counter()
        try:
            ok, details = self._compare(ctx, sparql)
        except Exception as exc:                    # noqa: BLE001
            ok, details = False, {'error': str(exc)[:300]}
        details.update(extra)
        self.knowledge.add_validation(ctx.execution_id, validator='sparql_regression',
                                      passed=ok, details=details)
        self._step(ctx, seq, 'verify', 'succeeded' if ok else 'failed', started, **details)
        return ok, details

    # ── blue-green OBDA layer (ADR-0022) ────────────────────────────────────────
    def _run_bluegreen(self, ctx: Context, artifacts: dict, started_total: float) -> dict:
        self._deploy(ctx, artifacts)
        try:
            standby = self._prepare_standby(ctx, artifacts)
            self._take_baseline(ctx)                            # active instance, through the proxy
            self._start_standby(ctx)
            ok, details = self._verify(ctx, sparql=self.sparql_for(standby['standby_sparql_url']),
                                       seq=4, instance=standby['standby'])
        except ExecutionError:
            raise                                               # the step has already cleaned up
        except Exception:
            # Unexpected error before the switch (e.g. Knowledge rejected a step record): the
            # standby instance and the new VDB must still be cleaned up (finding of VM test 2026-10-08).
            self._discard_before_switch(ctx)
            raise
        if not ok:
            return self._abort_standby(ctx, details, started_total)
        self._switch_over(ctx)
        return self._complete(ctx, started_total)

    def _discard_before_switch(self, ctx: Context) -> None:
        """The standby instance is stopped and the new VDB version removed; the active version is untouched."""
        try:
            self.agent.bluegreen_discard()
        except Exception:                           # noqa: BLE001 — the original error matters more
            log.exception('the standby instance cannot be stopped')
        self.admin.undeploy(ctx.deployment)

    def _prepare_standby(self, ctx: Context, artifacts: dict) -> dict:
        started = time.perf_counter()
        try:
            standby = self.agent.bluegreen_state()
            self.agent.bluegreen_prepare(artifacts['r2rml'], artifacts['ontology'], ctx.new_version,
                                         artifacts['r2rml_sha'], artifacts['ontology_sha'])
            host, port = self.jdbc
            result = self.agent.bluegreen_validate(f'jdbc:teiid:{ctx.vdb_name}@mm://{host}:{port}'
                                                   f';version={ctx.new_version}')
        except Exception as exc:                    # noqa: BLE001
            self._discard_before_switch(ctx)
            self._step(ctx, 2, 'validate', 'failed', started, error=str(exc)[:300])
            raise ExecutionError(f'writing or validating the artifacts failed: {exc}') from exc
        self.knowledge.add_validation(ctx.execution_id, validator='ontop_validate',
                                      passed=bool(result.get('ok')),
                                      details={'exit_code': result.get('exit_code'),
                                               'instance': standby['standby'],
                                               'output': (result.get('output') or '')[-1000:]})
        if not result.get('ok'):
            self._discard_before_switch(ctx)
            self._step(ctx, 2, 'validate', 'failed', started, exit_code=result.get('exit_code'),
                       instance=standby['standby'])
            raise ExecutionError('ontop validate rejected the new artifacts',
                                 {'output': (result.get('output') or '')[-500:]})
        self._step(ctx, 2, 'validate', 'succeeded', started, exit_code=0,
                   instance=standby['standby'], vdb_version=ctx.new_version)
        return standby

    def _start_standby(self, ctx: Context) -> None:
        started = time.perf_counter()
        result = self.agent.bluegreen_start()
        detail = {'instance': result.get('color'), 'error': result.get('error'),
                  **(result.get('detail') or {})}
        if not result.get('ok'):
            self._discard_before_switch(ctx)
            self._step(ctx, 3, 'start_ontop', 'failed', started, **detail)
            raise ExecutionError(f"the standby Ontop instance is not ready: {result.get('error')}")
        self._step(ctx, 3, 'start_ontop', 'succeeded', started, **detail)

    def _switch_over(self, ctx: Context) -> None:
        started = time.perf_counter()
        result = self.agent.bluegreen_switch()
        if not result.get('ok'):                    # the proxy stays on the old instance (agent)
            self._discard_before_switch(ctx)
            self._step(ctx, 5, 'switch', 'failed', started, error=result.get('error'))
            raise ExecutionError(f"switching the proxy failed: {result.get('error')}")
        self.admin.set_connection_type(ctx.vdb_name, ctx.new_version, 'ANY')
        detail = result.get('detail') or {}
        self._step(ctx, 5, 'switch', 'succeeded', started, connection_type='ANY',
                   vdb_version=ctx.new_version, instance=result.get('color'),
                   proxy_ms=detail.get('switch_ms'), retired=detail.get('retired'))

    def _abort_standby(self, ctx: Context, details: dict, started_total: float) -> dict:
        started = time.perf_counter()
        self._discard_before_switch(ctx)
        self._step(ctx, 5, 'rollback', 'succeeded', started, reason=details,
                   impact='none; traffic was never switched')
        ctx.timings['total_ms'] = int((time.perf_counter() - started_total) * 1000)
        return self._finish(ctx.execution_id, status='rolled_back', timings=ctx.timings,
                            failure={'message': 'verification failed', **details})

    def _rollback(self, ctx: Context, details: dict, started_total: float) -> dict:
        started = time.perf_counter()
        self.admin.set_connection_type(ctx.vdb_name, ctx.new_version, 'NONE')
        self.admin.set_connection_type(ctx.vdb_name, ctx.old_version, 'ANY')
        restored = self._restore_artifacts(ctx)
        reloaded = self.agent.reload()
        self._step(ctx, 6, 'rollback', 'succeeded' if reloaded.get('ok') else 'failed', started,
                   artifacts=restored, reason=details, reload_ok=reloaded.get('ok'))
        ctx.timings['total_ms'] = int((time.perf_counter() - started_total) * 1000)
        return self._finish(ctx.execution_id, status='rolled_back',
                            timings=ctx.timings,
                            failure={'message': 'verification failed', **details})

    def _complete(self, ctx: Context, started_total: float) -> dict:
        self.admin.set_connection_type(ctx.vdb_name, ctx.old_version, 'NONE')
        try:                                        # housekeeping, not part of success
            self.agent.prune_backups(keep=20)
        except Exception:                           # noqa: BLE001
            pass
        started = time.perf_counter()
        sync_result = self.knowledge.sync(self.obdf_id)
        self._step(ctx, 6, 'sync', 'succeeded', started,
                   spec_version_id=sync_result.get('spec_version_id'),
                   version_no=sync_result.get('version_no'))
        ctx.timings['total_ms'] = int((time.perf_counter() - started_total) * 1000)
        return self._finish(
            ctx.execution_id, status='succeeded', timings=ctx.timings,
            candidate_spec_version_id=sync_result.get('spec_version_id'))


XSD = 'http://www.w3.org/2001/XMLSchema#'


def _expand(datatype: str | None) -> str | None:
    """'xsd:string' -> full IRI, so that the artifacts contain no IRI with the scheme 'xsd'."""
    if datatype and datatype.startswith('xsd:'):
        return XSD + datatype[4:]
    return datatype


def _target_predicate(plan: dict) -> str | None:
    for action in plan.get('actions', []):
        params = action.get('params') or {}
        if params.get('predicate_iri'):
            return params['predicate_iri']
        if params.get('iri'):
            return params['iri']
    return None


def _deprecated_predicates(plan: dict) -> set[str]:
    """Predicates that a P-002 plan deprecates (no mapping fills them any longer)."""
    return {(a.get('params') or {}).get('predicate_iri') for a in plan.get('actions', [])
            if a.get('operation') == 'deprecate_property'
            and (a.get('params') or {}).get('predicate_iri')}


def _column_type(plan: dict) -> str | None:
    for action in plan.get('actions', []):
        params = action.get('params') or {}
        if params.get('column_type'):
            return params['column_type']
    return None


def _table_from_vdb(plan: dict) -> str | None:
    for action in plan.get('actions', []):
        if action['artifact'] == 'vdb':
            return (action.get('params') or {}).get('table')
    return None
