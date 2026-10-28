"""Executor with blue-green on the OBDA layer (ADR-0022)."""
import pytest

import fixtures_plan as fx
from ascam_executor.engine import ExecutionError, Executor


class FakeAgentBG(fx.FakeAgent):
    """Agent with a standby slot: the canonical artifacts change only at the switch."""
    def __init__(self, validate_ok=True, start_ok=True, switch_ok=True, **kw):
        super().__init__(validate_ok=validate_ok, **kw)
        self.active, self.standby_files, self.log = 'blue', None, []
        self.start_ok, self.switch_ok = start_ok, switch_ok

    def _standby(self):
        return 'green' if self.active == 'blue' else 'blue'

    def bluegreen_state(self):
        s = self._standby()
        return {'active': self.active, 'standby': s,
                'standby_sparql_url': f'http://vkg-system-ontop-{s}:8080/sparql'}

    def bluegreen_prepare(self, r2rml, ontology, vdb_version, exp_r2rml, exp_ont):
        if exp_r2rml and exp_r2rml != fx.FakeAgent.artifact(self, 'r2rml')['sha256']:
            raise RuntimeError('409 fingerprint conflict')
        self.standby_files = {'r2rml': r2rml, 'ontology': ontology, 'vdb_version': vdb_version}
        self.log.append(('prepare', vdb_version))
        return {'ok': True, 'color': self._standby()}

    def bluegreen_validate(self, db_url=None):
        self.validate_calls.append(db_url)
        self.log.append(('validate', self._standby()))
        return {'ok': self.validate_ok, 'exit_code': 0 if self.validate_ok else 1,
                'output': '' if self.validate_ok else 'ERROR: mapping rejected'}

    def bluegreen_start(self):
        self.log.append(('start', self._standby()))
        return {'ok': self.start_ok, 'color': self._standby(), 'duration_ms': 9000,
                'error': None if self.start_ok else 'endpoint not ready',
                'detail': {'start_ms': 300, 'ready_ms': 8700}}

    def bluegreen_switch(self):
        self.log.append(('switch', self._standby()))
        if not self.switch_ok:
            return {'ok': False, 'color': self._standby(), 'duration_ms': 5, 'error': 'nginx failed'}
        retired, self.active = self.active, self._standby()
        self.files['r2rml'], self.files['ontology'] = (self.standby_files['r2rml'],
                                                       self.standby_files['ontology'])
        return {'ok': True, 'color': self.active, 'duration_ms': 40,
                'detail': {'switch_ms': 12, 'retired': retired}}

    def bluegreen_discard(self):
        self.log.append(('discard', self._standby()))
        self.standby_files = None
        return {'ok': True, 'color': self._standby(), 'duration_ms': 300}


def build(agent=None, active=fx.BASELINE, standby=fx.WITHOUT_STATUS, admin=None):
    knowledge, admin, agent = fx.FakeKnowledge(), admin or fx.FakeAdmin(), agent or FakeAgentBG()
    clients = {}

    def sparql_for(url):
        clients['url'] = url
        return fx.FakeSparql([standby])
    executor = Executor(knowledge, admin, agent, fx.FakeSparql([active]), obdf_id=1,
                        sleep=lambda s: None, obda_strategy='bluegreen', sparql_for=sparql_for)
    return executor, knowledge, admin, agent, clients


def steps_of(knowledge):
    return [(s['name'], s['status']) for s in knowledge.steps]


def test_bluegreen_requires_instance_client():
    with pytest.raises(ValueError):
        Executor(None, None, None, None, 1, obda_strategy='bluegreen')
    with pytest.raises(ValueError):
        Executor(None, None, None, None, 1, obda_strategy='unknown')


def test_drop_is_verified_on_standby_before_switch():
    executor, knowledge, admin, agent, clients = build()
    result = executor.execute(fx.plan_drop())
    assert steps_of(knowledge) == [('deploy_vdb', 'succeeded'), ('validate', 'succeeded'),
                                  ('start_ontop', 'succeeded'), ('verify', 'succeeded'),
                                  ('switch', 'succeeded'), ('sync', 'succeeded')]
    # verification goes directly to the standby instance; the switch happens only afterwards
    assert clients['url'] == 'http://vkg-system-ontop-green:8080/sparql'
    assert [l[0] for l in agent.log] == ['prepare', 'validate', 'start', 'switch']
    assert agent.log[0] == ('prepare', '2')                      # the standby is bound to VDB version 2
    assert agent.validate_calls == ['jdbc:teiid:government@mm://teiid:31000;version=2']
    assert admin.connection_types == [('2', 'ANY'), ('1', 'NONE')]
    assert agent.reloads == 0                                    # no restart in place
    assert fx.STATUS not in agent.files['r2rml'] and agent.active == 'green'
    assert result['status'] == 'succeeded' and knowledge.synced == 1
    assert {'start_ontop', 'switch', 'verify'} <= set(knowledge.finished['timings'])


def test_failed_verification_never_reaches_users():
    """Empty standby graph: the standby is stopped, the new VDB removed, proxy and connections unchanged."""
    executor, knowledge, admin, agent, _ = build(standby={})
    result = executor.execute(fx.plan_drop())
    assert steps_of(knowledge)[-2:] == [('verify', 'failed'), ('rollback', 'succeeded')]
    assert ('switch', 'green') not in agent.log and agent.log[-1] == ('discard', 'green')
    assert admin.connection_types == [] and admin.undeployed == ['government-2-vdb.xml']
    assert agent.files['r2rml'] == fx.MAPPING and agent.active == 'blue'
    assert result['status'] == 'rolled_back' and knowledge.synced == 0


def test_failed_validation_discards_standby():
    executor, knowledge, admin, agent, _ = build(agent=FakeAgentBG(validate_ok=False))
    with pytest.raises(ExecutionError, match='ontop validate'):
        executor.execute(fx.plan_drop())
    assert steps_of(knowledge) == [('deploy_vdb', 'succeeded'), ('validate', 'failed')]
    assert agent.log[-1] == ('discard', 'green') and admin.undeployed == ['government-2-vdb.xml']
    assert agent.files['r2rml'] == fx.MAPPING and knowledge.finished['status'] == 'failed'


def test_unready_standby_is_discarded():
    executor, knowledge, admin, agent, _ = build(agent=FakeAgentBG(start_ok=False))
    with pytest.raises(ExecutionError, match='standby Ontop instance is not ready'):
        executor.execute(fx.plan_drop())
    assert steps_of(knowledge)[-1] == ('start_ontop', 'failed')
    assert agent.log[-1] == ('discard', 'green') and admin.connection_types == []


def test_failed_proxy_switch_keeps_old_version_serving():
    executor, knowledge, admin, agent, _ = build(agent=FakeAgentBG(switch_ok=False))
    with pytest.raises(ExecutionError, match='switching the proxy'):
        executor.execute(fx.plan_drop())
    assert steps_of(knowledge)[-1] == ('switch', 'failed')
    assert admin.connection_types == [] and admin.undeployed == ['government-2-vdb.xml']
    assert agent.active == 'blue' and agent.files['r2rml'] == fx.MAPPING


def test_write_conflict_is_reported_before_any_instance_change():
    class ChangedAgent(FakeAgentBG):
        def artifact(self, kind):
            return {**super().artifact(kind), 'sha256': 'x' * 64}
    executor, knowledge, admin, agent, _ = build(agent=ChangedAgent())
    with pytest.raises(ExecutionError, match='writing or validating'):
        executor.execute(fx.plan_drop())
    assert steps_of(knowledge)[-1] == ('validate', 'failed')
    assert ('start', 'green') not in agent.log


def test_add_pattern_succeeds_on_standby():
    executor, knowledge, _, agent, _ = build(standby=fx.BASELINE)
    assert executor.execute(fx.plan_add())['status'] == 'succeeded'
    assert 'rr:predicate bansos:email' in agent.files['r2rml']


def test_drop_through_view_expects_every_deprecated_predicate_gone():
    """ADR-0023: one column, two predicates; verification demands that both disappear."""
    OTHER = 'http://bansos.go.id/ontology/statusLain'
    plan = fx.plan_drop()
    plan['actions'] += [{'seq': 9, 'artifact': 'ontology', 'operation': 'deprecate_property',
                         'params': {'predicate_iri': OTHER}}]
    before = {**fx.BASELINE, OTHER: 3}
    executor, knowledge, _, _, _ = build(active=before, standby={**fx.WITHOUT_STATUS, OTHER: 3})
    assert executor.execute(plan)['status'] == 'rolled_back'          # OTHER is still there
    executor, knowledge, _, _, _ = build(active=before, standby=fx.WITHOUT_STATUS)
    assert executor.execute(plan)['status'] == 'succeeded'


def test_unexpected_error_before_switch_cleans_up_standby():
    """Finding of the VM test: Knowledge rejects the start_ontop step (422); standby and new VDB are cleaned up."""
    class KnowledgeRejectsStep(fx.FakeKnowledge):
        def add_step(self, execution_id, **step):
            if step['name'] == 'start_ontop':
                raise RuntimeError('422 Unprocessable Entity')
            return super().add_step(execution_id, **step)
    executor, knowledge, admin, agent, _ = build()
    executor.knowledge = KnowledgeRejectsStep()
    with pytest.raises(RuntimeError):
        executor.execute(fx.plan_drop())
    assert agent.log[-1] == ('discard', 'green') and admin.undeployed == ['government-2-vdb.xml']
    assert agent.active == 'blue' and admin.connection_types == []
    assert executor.knowledge.finished['status'] == 'failed'


class BrokenSparql:
    """Old instance after the DDL: the whole fingerprint fails because one mapping path reads a
    dropped column (finding of VM test A007/A008); per predicate, only that predicate fails."""
    def __init__(self, result, failed=(), whole_fails=True):
        self.result, self.failed, self.whole_fails = dict(result), list(failed), whole_fails
        self.per_predicate = []

    def fingerprint(self):
        if self.whole_fails:
            raise RuntimeError('500 TEIID: column does not exist')
        return dict(self.result)

    def fingerprint_per_predicate(self, predicates):
        self.per_predicate.append(list(predicates))
        return ({p: n for p, n in self.result.items() if p in predicates},
                [p for p in self.failed if p in predicates])


RDF_TYPE = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#type'


def build_broken(active, standby):
    knowledge, admin, agent = fx.FakeKnowledge(), fx.FakeAdmin(), FakeAgentBG()
    executor = Executor(knowledge, admin, agent, active, obdf_id=1, sleep=lambda s: None,
                        obda_strategy='bluegreen', sparql_for=lambda url: standby)
    return executor, knowledge


def test_baseline_falls_back_to_per_predicate_fingerprint():
    nik = RDF_TYPE                               # another predicate in the list (rdf:type)
    active = BrokenSparql({nik: 15}, failed=[fx.STATUS])
    standby = BrokenSparql({nik: 15}, whole_fails=False)
    executor, knowledge = build_broken(active, standby)
    assert executor.execute(fx.plan_drop())['status'] == 'succeeded'
    # the new instance is checked the same way, over the predicate list from ℳ, ℳ′, and 𝒯
    assert standby.per_predicate and fx.STATUS in standby.per_predicate[0] and nik in standby.per_predicate[0]
    details = next(s for s in knowledge.steps if s['name'] == 'verify')['detail']
    assert details['fingerprint'] == 'per_predicate' and details['failed_before'] == [fx.STATUS]


def test_per_predicate_failure_on_new_instance_is_rolled_back():
    nik = RDF_TYPE                               # another predicate in the list (rdf:type)
    active = BrokenSparql({nik: 15}, failed=[fx.STATUS])
    standby = BrokenSparql({}, failed=[nik], whole_fails=False)        # the new instance is broken too
    executor, _ = build_broken(active, standby)
    assert executor.execute(fx.plan_drop())['status'] == 'rolled_back'


def test_unrelated_baseline_failure_must_be_answered_after_adaptation():
    nik = RDF_TYPE                               # another predicate in the list (rdf:type)
    active = BrokenSparql({}, failed=[fx.STATUS, nik])               # nik fails for another reason
    executor, _ = build_broken(active, BrokenSparql({}, whole_fails=False))
    assert executor.execute(fx.plan_drop())['status'] == 'rolled_back'
    executor, _ = build_broken(BrokenSparql({}, failed=[fx.STATUS, nik]),
                                BrokenSparql({nik: 15}, whole_fails=False))
    assert executor.execute(fx.plan_drop())['status'] == 'succeeded'
