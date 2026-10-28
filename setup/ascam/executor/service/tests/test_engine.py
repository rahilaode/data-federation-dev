"""Executor engine: blue-green, validation, verification, and revert."""
import pytest

import fixtures_plan as fx
from ascam_executor.engine import ExecutionError, Executor


def build(knowledge=None, admin=None, agent=None, sparql=None):
    knowledge = knowledge or fx.FakeKnowledge()
    admin = admin or fx.FakeAdmin()
    agent = agent or fx.FakeAgent()
    sparql = sparql or fx.FakeSparql([fx.BASELINE, fx.WITHOUT_STATUS])
    executor = Executor(knowledge, admin, agent, sparql, obdf_id=1, sleep=lambda s: None)
    return executor, knowledge, admin, agent


def steps_of(knowledge):
    return [(s['name'], s['status']) for s in knowledge.steps]


def test_drop_plan_runs_blue_green_and_succeeds():
    executor, knowledge, admin, agent = build()
    result = executor.execute(fx.plan_drop())

    assert steps_of(knowledge) == [('deploy_vdb', 'succeeded'), ('validate', 'succeeded'),
                                  ('switch', 'succeeded'), ('reload_ontop', 'succeeded'),
                                  ('verify', 'succeeded'), ('sync', 'succeeded')]
    # the new VDB version is deployed next to the old one, then connections are moved
    assert admin.deployed[0][0] == 'government-2-vdb.xml'
    assert 'DROP COLUMN "status_ekonomi"' in admin.deployed[0][1]
    assert admin.connection_types == [('2', 'ANY'), ('1', 'NONE')]
    # the artifacts are edited and validated against the NEW VERSION
    assert fx.STATUS not in agent.files['r2rml']
    assert 'owl:deprecated true' in agent.files['ontology']
    assert agent.validate_calls == ['jdbc:teiid:government@mm://teiid:31000;version=2']
    assert agent.reloads == 1 and not agent.restored
    assert result['status'] == 'succeeded' and knowledge.synced == 1
    assert agent.pruned == 20                        # old backups are pruned after success
    assert knowledge.finished['candidate_spec_version_id'] == 8
    assert set(knowledge.finished['timings']) >= {'deploy_vdb', 'validate', 'switch',
                                                  'reload_ontop', 'verify', 'total_ms'}


def test_add_plan_writes_property_and_mapping():
    executor, knowledge, admin, agent = build(
        sparql=fx.FakeSparql([fx.BASELINE, fx.BASELINE]))       # the new column holds no data yet
    executor.execute(fx.plan_add())
    assert 'ADD COLUMN "email" string(100)' in admin.deployed[0][1]   # type mapped from Knowledge
    assert 'bansos:email a owl:DatatypeProperty' in agent.files['ontology']
    assert 'rr:predicate bansos:email' in agent.files['r2rml']
    assert knowledge.finished['status'] == 'succeeded'


def test_failed_vdb_stops_before_touching_artifacts():
    executor, knowledge, admin, agent = build(
        admin=fx.FakeAdmin(status='FAILED', errors=['TEIID31259 invalid ddl']))
    with pytest.raises(ExecutionError, match='FAILED'):
        executor.execute(fx.plan_drop())
    assert steps_of(knowledge) == [('deploy_vdb', 'failed')]
    assert admin.undeployed == ['government-2-vdb.xml']          # the failed version is cleaned up
    assert agent.files['r2rml'] == fx.MAPPING                    # ℳ untouched
    assert admin.connection_types == []                          # connections were never moved
    assert knowledge.finished['status'] == 'failed'
    assert knowledge.validations[0]['validator'] == 'teiid_status'


def test_failed_validation_restores_artifacts_and_removes_new_vdb():
    executor, knowledge, admin, agent = build(agent=fx.FakeAgent(validate_ok=False))
    with pytest.raises(ExecutionError, match='ontop validate'):
        executor.execute(fx.plan_drop())
    assert steps_of(knowledge) == [('deploy_vdb', 'succeeded'), ('validate', 'failed')]
    assert agent.files['r2rml'] == fx.MAPPING and agent.files['ontology'] == fx.ONTOLOGY
    assert len(agent.restored) == 2
    assert admin.undeployed == ['government-2-vdb.xml'] and admin.connection_types == []


def test_failed_verification_triggers_rollback():
    """Verification fails: connections go back to the old version and the artifacts are restored."""
    executor, knowledge, admin, agent = build(
        sparql=fx.FakeSparql([fx.BASELINE, {}]))                 # empty graph after the adaptation
    result = executor.execute(fx.plan_drop())
    assert steps_of(knowledge)[-1] == ('rollback', 'succeeded')
    assert admin.connection_types == [('2', 'ANY'), ('2', 'NONE'), ('1', 'ANY')]
    assert agent.files['r2rml'] == fx.MAPPING and agent.files['ontology'] == fx.ONTOLOGY
    assert agent.reloads == 2                                    # reloaded again after the restore
    assert result['status'] == 'rolled_back' and knowledge.synced == 0


def test_verification_rules_follow_pattern():
    # P-002: the target predicate must disappear; if it is still there -> rollback
    executor, knowledge, admin, agent = build(sparql=fx.FakeSparql([fx.BASELINE, fx.BASELINE]))
    result = executor.execute(fx.plan_drop())
    assert result['status'] == 'rolled_back'
    # P-001: other predicates must not disappear
    executor, knowledge, _, _ = build(sparql=fx.FakeSparql([fx.BASELINE, fx.WITHOUT_STATUS]))
    assert executor.execute(fx.plan_add())['status'] == 'rolled_back'


def test_drop_keeping_shared_predicate_is_verified_without_disappearance():
    """P-002 without deprecation (keep_property): another mapping still fills the predicate, so its
    count may drop but the predicate must not disappear. The old rule required the predicate to
    disappear, so a correct plan was rolled back."""
    plan = fx.plan_drop()
    plan['actions'][2] = {'seq': 3, 'artifact': 'ontology', 'operation': 'keep_property',
                          'params': {'predicate_iri': fx.STATUS, 'reason': 'still used'}}
    lower = {**fx.BASELINE, fx.STATUS: 9}
    executor, _, _, _ = build(sparql=fx.FakeSparql([fx.BASELINE, lower]))
    assert executor.execute(plan)['status'] == 'succeeded'
    # a predicate that is not deprecated still must not disappear
    executor, _, _, _ = build(sparql=fx.FakeSparql([fx.BASELINE, fx.WITHOUT_STATUS]))
    assert executor.execute(plan)['status'] == 'rolled_back'


def test_failed_reload_is_reported():
    executor, knowledge, admin, agent = build(agent=fx.FakeAgent(reload_ok=False))
    with pytest.raises(ExecutionError, match='reloading Ontop'):
        executor.execute(fx.plan_drop())
    assert steps_of(knowledge)[-1] == ('reload_ontop', 'failed')
    assert knowledge.finished['status'] == 'failed'


def test_stale_plan_is_refused_before_any_change():
    executor, knowledge, admin, agent = build()
    with pytest.raises(ExecutionError, match='another specification version'):
        executor.execute(fx.plan_drop(base=999))
    assert knowledge.steps == [] and admin.deployed == [] and agent.files['r2rml'] == fx.MAPPING
    assert knowledge.superseded[0] == 1                 # will not be retried in every cycle


def test_write_conflict_is_handled_as_failure():
    """The artifact is changed by someone else between reading and writing: the agent rejects the write."""
    class ChangedAgent(fx.FakeAgent):
        def artifact(self, kind):
            data = super().artifact(kind)
            return {**data, 'sha256': 'x' * 64}          # stale fingerprint

    executor, knowledge, admin, agent = build(agent=ChangedAgent())
    with pytest.raises(ExecutionError, match='writing or validating'):
        executor.execute(fx.plan_drop())
    assert steps_of(knowledge)[-1] == ('validate', 'failed')
    assert agent.files['r2rml'] == fx.MAPPING            # content unchanged
    assert admin.undeployed == ['government-2-vdb.xml']
    assert knowledge.finished['status'] == 'failed'


# ── regression of evaluation F6: an execution must not be left with status running ─
import httpx  # noqa: E402


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request('POST', 'http://knowledge/api/v1/executions/99/finish')
    return httpx.HTTPStatusError(f'{status}', request=request,
                                 response=httpx.Response(status, request=request))


class KnowledgeConflictOnce(fx.FakeKnowledge):
    """/finish rejects once with 409, as when the version produced by the sync is not committed yet."""
    def __init__(self):
        super().__init__()
        self.finish_attempts = 0

    def finish_execution(self, execution_id, **body):
        self.finish_attempts += 1
        if self.finish_attempts == 1:
            raise _http_error(409)
        return super().finish_execution(execution_id, **body)


def test_finish_is_retried_after_a_transient_conflict():
    executor, knowledge, _, _ = build(knowledge=KnowledgeConflictOnce())
    result = executor.execute(fx.plan_drop())
    assert knowledge.finish_attempts == 2
    assert result['status'] == 'succeeded' and knowledge.finished['status'] == 'succeeded'


def test_finish_is_not_retried_for_a_client_error():
    class KnowledgeRejects(fx.FakeKnowledge):
        def finish_execution(self, execution_id, **body):
            raise _http_error(422)
    executor, _, _, _ = build(knowledge=KnowledgeRejects())
    with pytest.raises(httpx.HTTPStatusError):
        executor.execute(fx.plan_drop())


def test_unexpected_error_after_switch_closes_the_execution_as_failed():
    agent = fx.FakeAgent()

    def broken_reload():
        raise RuntimeError('the agent does not answer')
    agent.reload = broken_reload
    executor, knowledge, _, _ = build(agent=agent)
    with pytest.raises(RuntimeError):
        executor.execute(fx.plan_drop())
    assert knowledge.finished['status'] == 'failed'
    assert knowledge.finished['failure']['after_step'] == 'switch'
    assert 'RuntimeError' in knowledge.finished['failure']['message']


def test_error_while_preparing_artifacts_closes_the_execution():
    """Regression of plan 2: a MappingError before the first step left the execution running."""
    executor, knowledge, _, _ = build()

    def broken_prepare(plan, active_version):
        raise ValueError('predicate not found in the mapping')
    executor._prepare_artifacts = broken_prepare
    with pytest.raises(ValueError):
        executor.execute(fx.plan_drop())
    assert knowledge.finished['status'] == 'failed'
    assert knowledge.finished['failure']['after_step'] is None
