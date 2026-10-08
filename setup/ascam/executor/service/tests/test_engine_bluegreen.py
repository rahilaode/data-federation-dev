"""Executor dengan blue-green pada lapisan OBDA (ADR-0022)."""
import pytest

import fixtures_plan as fx
from ascam_executor.engine import ExecutionError, Executor


class FakeAgentBG(fx.FakeAgent):
    """Agen dengan slot siaga: artefak kanonik baru berubah saat peralihan."""
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
            raise RuntimeError('409 konflik sidik jari')
        self.standby_files = {'r2rml': r2rml, 'ontology': ontology, 'vdb_version': vdb_version}
        self.log.append(('prepare', vdb_version))
        return {'ok': True, 'color': self._standby()}

    def bluegreen_validate(self, db_url=None):
        self.validate_calls.append(db_url)
        self.log.append(('validate', self._standby()))
        return {'ok': self.validate_ok, 'exit_code': 0 if self.validate_ok else 1,
                'output': '' if self.validate_ok else 'ERROR: mapping ditolak'}

    def bluegreen_start(self):
        self.log.append(('start', self._standby()))
        return {'ok': self.start_ok, 'color': self._standby(), 'duration_ms': 9000,
                'error': None if self.start_ok else 'endpoint tidak siap',
                'detail': {'start_ms': 300, 'ready_ms': 8700}}

    def bluegreen_switch(self):
        self.log.append(('switch', self._standby()))
        if not self.switch_ok:
            return {'ok': False, 'color': self._standby(), 'duration_ms': 5, 'error': 'nginx gagal'}
        lama, self.active = self.active, self._standby()
        self.files['r2rml'], self.files['ontology'] = (self.standby_files['r2rml'],
                                                       self.standby_files['ontology'])
        return {'ok': True, 'color': self.active, 'duration_ms': 40,
                'detail': {'switch_ms': 12, 'retired': lama}}

    def bluegreen_discard(self):
        self.log.append(('discard', self._standby()))
        self.standby_files = None
        return {'ok': True, 'color': self._standby(), 'duration_ms': 300}


def buat(agent=None, aktif=fx.BASELINE, siaga=fx.TANPA_STATUS, admin=None):
    knowledge, admin, agent = fx.FakeKnowledge(), admin or fx.FakeAdmin(), agent or FakeAgentBG()
    klien = {}

    def sparql_for(url):
        klien['url'] = url
        return fx.FakeSparql([siaga])
    executor = Executor(knowledge, admin, agent, fx.FakeSparql([aktif]), obdf_id=1,
                        sleep=lambda s: None, obda_strategy='bluegreen', sparql_for=sparql_for)
    return executor, knowledge, admin, agent, klien


def langkah(knowledge):
    return [(s['name'], s['status']) for s in knowledge.steps]


def test_bluegreen_requires_instance_client():
    with pytest.raises(ValueError):
        Executor(None, None, None, None, 1, obda_strategy='bluegreen')
    with pytest.raises(ValueError):
        Executor(None, None, None, None, 1, obda_strategy='entah')


def test_drop_is_verified_on_standby_before_switch():
    executor, knowledge, admin, agent, klien = buat()
    hasil = executor.execute(fx.plan_drop())
    assert langkah(knowledge) == [('deploy_vdb', 'succeeded'), ('validate', 'succeeded'),
                                  ('start_ontop', 'succeeded'), ('verify', 'succeeded'),
                                  ('switch', 'succeeded'), ('sync', 'succeeded')]
    # verifikasi langsung ke instance siaga; peralihan baru terjadi sesudahnya
    assert klien['url'] == 'http://vkg-system-ontop-green:8080/sparql'
    assert [l[0] for l in agent.log] == ['prepare', 'validate', 'start', 'switch']
    assert agent.log[0] == ('prepare', '2')                      # siaga dikunci ke VDB versi 2
    assert agent.validate_calls == ['jdbc:teiid:government@mm://teiid:31000;version=2']
    assert admin.connection_types == [('2', 'ANY'), ('1', 'NONE')]
    assert agent.reloads == 0                                    # tidak ada restart di tempat
    assert fx.STATUS not in agent.files['r2rml'] and agent.active == 'green'
    assert hasil['status'] == 'succeeded' and knowledge.synced == 1
    assert {'start_ontop', 'switch', 'verify'} <= set(knowledge.finished['timings'])


def test_failed_verification_never_reaches_users():
    """Graf siaga kosong: siaga dihentikan, VDB baru dihapus, proxy dan koneksi tidak berubah."""
    executor, knowledge, admin, agent, _ = buat(siaga={})
    hasil = executor.execute(fx.plan_drop())
    assert langkah(knowledge)[-2:] == [('verify', 'failed'), ('rollback', 'succeeded')]
    assert ('switch', 'green') not in agent.log and agent.log[-1] == ('discard', 'green')
    assert admin.connection_types == [] and admin.undeployed == ['government-2-vdb.xml']
    assert agent.files['r2rml'] == fx.MAPPING and agent.active == 'blue'
    assert hasil['status'] == 'rolled_back' and knowledge.synced == 0


def test_failed_validation_discards_standby():
    executor, knowledge, admin, agent, _ = buat(agent=FakeAgentBG(validate_ok=False))
    with pytest.raises(ExecutionError, match='ontop validate'):
        executor.execute(fx.plan_drop())
    assert langkah(knowledge) == [('deploy_vdb', 'succeeded'), ('validate', 'failed')]
    assert agent.log[-1] == ('discard', 'green') and admin.undeployed == ['government-2-vdb.xml']
    assert agent.files['r2rml'] == fx.MAPPING and knowledge.finished['status'] == 'failed'


def test_unready_standby_is_discarded():
    executor, knowledge, admin, agent, _ = buat(agent=FakeAgentBG(start_ok=False))
    with pytest.raises(ExecutionError, match='siaga tidak siap'):
        executor.execute(fx.plan_drop())
    assert langkah(knowledge)[-1] == ('start_ontop', 'failed')
    assert agent.log[-1] == ('discard', 'green') and admin.connection_types == []


def test_failed_proxy_switch_keeps_old_version_serving():
    executor, knowledge, admin, agent, _ = buat(agent=FakeAgentBG(switch_ok=False))
    with pytest.raises(ExecutionError, match='peralihan proxy'):
        executor.execute(fx.plan_drop())
    assert langkah(knowledge)[-1] == ('switch', 'failed')
    assert admin.connection_types == [] and admin.undeployed == ['government-2-vdb.xml']
    assert agent.active == 'blue' and agent.files['r2rml'] == fx.MAPPING


def test_write_conflict_is_reported_before_any_instance_change():
    class AgenBerubah(FakeAgentBG):
        def artifact(self, kind):
            return {**super().artifact(kind), 'sha256': 'x' * 64}
    executor, knowledge, admin, agent, _ = buat(agent=AgenBerubah())
    with pytest.raises(ExecutionError, match='penulisan atau validasi'):
        executor.execute(fx.plan_drop())
    assert langkah(knowledge)[-1] == ('validate', 'failed')
    assert ('start', 'green') not in agent.log


def test_add_pattern_succeeds_on_standby():
    executor, knowledge, _, agent, _ = buat(siaga=fx.BASELINE)
    assert executor.execute(fx.plan_add())['status'] == 'succeeded'
    assert 'rr:predicate bansos:email' in agent.files['r2rml']


def test_drop_through_view_expects_every_deprecated_predicate_gone():
    """ADR-0023: satu kolom, dua predikat; verifikasi menuntut keduanya hilang."""
    LAIN = 'http://bansos.go.id/ontology/statusLain'
    plan = fx.plan_drop()
    plan['actions'] += [{'seq': 9, 'artifact': 'ontology', 'operation': 'deprecate_property',
                         'params': {'predicate_iri': LAIN}}]
    sebelum = {**fx.BASELINE, LAIN: 3}
    executor, knowledge, _, _, _ = buat(aktif=sebelum, siaga={**fx.TANPA_STATUS, LAIN: 3})
    assert executor.execute(plan)['status'] == 'rolled_back'          # LAIN masih ada
    executor, knowledge, _, _, _ = buat(aktif=sebelum, siaga=fx.TANPA_STATUS)
    assert executor.execute(plan)['status'] == 'succeeded'


def test_unexpected_error_before_switch_cleans_up_standby():
    """Temuan uji VM: Knowledge menolak langkah start_ontop (422); siaga dan VDB baru dibersihkan."""
    class KnowledgeTolakLangkah(fx.FakeKnowledge):
        def add_step(self, execution_id, **step):
            if step['name'] == 'start_ontop':
                raise RuntimeError('422 Unprocessable Entity')
            return super().add_step(execution_id, **step)
    executor, knowledge, admin, agent, _ = buat()
    executor.knowledge = KnowledgeTolakLangkah()
    with pytest.raises(RuntimeError):
        executor.execute(fx.plan_drop())
    assert agent.log[-1] == ('discard', 'green') and admin.undeployed == ['government-2-vdb.xml']
    assert agent.active == 'blue' and admin.connection_types == []
    assert executor.knowledge.finished['status'] == 'failed'
