"""Mesin Executor: blue-green, validasi, verifikasi, dan revert."""
import pytest

import fixtures_plan as fx
from ascam_executor.engine import ExecutionError, Executor


def buat(knowledge=None, admin=None, agent=None, sparql=None):
    knowledge = knowledge or fx.FakeKnowledge()
    admin = admin or fx.FakeAdmin()
    agent = agent or fx.FakeAgent()
    sparql = sparql or fx.FakeSparql([fx.BASELINE, fx.TANPA_STATUS])
    executor = Executor(knowledge, admin, agent, sparql, obdf_id=1, sleep=lambda s: None)
    return executor, knowledge, admin, agent


def langkah(knowledge):
    return [(s['name'], s['status']) for s in knowledge.steps]


def test_drop_plan_runs_blue_green_and_succeeds():
    executor, knowledge, admin, agent = buat()
    hasil = executor.execute(fx.plan_drop())

    assert langkah(knowledge) == [('deploy_vdb', 'succeeded'), ('validate', 'succeeded'),
                                  ('switch', 'succeeded'), ('reload_ontop', 'succeeded'),
                                  ('verify', 'succeeded'), ('sync', 'succeeded')]
    # VDB versi baru di-deploy di samping versi lama, lalu koneksi dipindahkan
    assert admin.deployed[0][0] == 'government-2-vdb.xml'
    assert 'DROP COLUMN "status_ekonomi"' in admin.deployed[0][1]
    assert admin.connection_types == [('2', 'ANY'), ('1', 'NONE')]
    # artefak disunting dan divalidasi terhadap VERSI BARU
    assert fx.STATUS not in agent.files['r2rml']
    assert 'owl:deprecated true' in agent.files['ontology']
    assert agent.validate_calls == ['jdbc:teiid:government@mm://teiid:31000;version=2']
    assert agent.reloads == 1 and not agent.restored
    assert hasil['status'] == 'succeeded' and knowledge.synced == 1
    assert agent.pruned == 20                        # cadangan lama dipangkas setelah berhasil
    assert knowledge.finished['candidate_spec_version_id'] == 8
    assert set(knowledge.finished['timings']) >= {'deploy_vdb', 'validate', 'switch',
                                                  'reload_ontop', 'verify', 'total_ms'}


def test_add_plan_writes_property_and_mapping():
    executor, knowledge, admin, agent = buat(
        sparql=fx.FakeSparql([fx.BASELINE, fx.BASELINE]))       # kolom baru belum berisi data
    executor.execute(fx.plan_add())
    assert 'ADD COLUMN "email" string(100)' in admin.deployed[0][1]   # tipe dipetakan dari Knowledge
    assert 'bansos:email a owl:DatatypeProperty' in agent.files['ontology']
    assert 'rr:predicate bansos:email' in agent.files['r2rml']
    assert knowledge.finished['status'] == 'succeeded'


def test_failed_vdb_stops_before_touching_artifacts():
    executor, knowledge, admin, agent = buat(
        admin=fx.FakeAdmin(status='FAILED', errors=['TEIID31259 ddl tidak valid']))
    with pytest.raises(ExecutionError, match='FAILED'):
        executor.execute(fx.plan_drop())
    assert langkah(knowledge) == [('deploy_vdb', 'failed')]
    assert admin.undeployed == ['government-2-vdb.xml']          # versi gagal dibersihkan
    assert agent.files['r2rml'] == fx.MAPPING                    # ℳ tidak tersentuh
    assert admin.connection_types == []                          # koneksi tidak pernah dipindahkan
    assert knowledge.finished['status'] == 'failed'
    assert knowledge.validations[0]['validator'] == 'teiid_status'


def test_failed_validation_restores_artifacts_and_removes_new_vdb():
    executor, knowledge, admin, agent = buat(agent=fx.FakeAgent(validate_ok=False))
    with pytest.raises(ExecutionError, match='ontop validate'):
        executor.execute(fx.plan_drop())
    assert langkah(knowledge) == [('deploy_vdb', 'succeeded'), ('validate', 'failed')]
    assert agent.files['r2rml'] == fx.MAPPING and agent.files['ontology'] == fx.ONTOLOGY
    assert len(agent.restored) == 2
    assert admin.undeployed == ['government-2-vdb.xml'] and admin.connection_types == []


def test_failed_verification_triggers_rollback():
    """Verifikasi gagal: koneksi dikembalikan ke versi lama dan artefak dipulihkan."""
    executor, knowledge, admin, agent = buat(
        sparql=fx.FakeSparql([fx.BASELINE, {}]))                 # graf kosong setelah adaptasi
    hasil = executor.execute(fx.plan_drop())
    assert langkah(knowledge)[-1] == ('rollback', 'succeeded')
    assert admin.connection_types == [('2', 'ANY'), ('2', 'NONE'), ('1', 'ANY')]
    assert agent.files['r2rml'] == fx.MAPPING and agent.files['ontology'] == fx.ONTOLOGY
    assert agent.reloads == 2                                    # muat ulang lagi setelah pemulihan
    assert hasil['status'] == 'rolled_back' and knowledge.synced == 0


def test_verification_rules_follow_pattern():
    # P-002: predikat sasaran harus hilang; bila masih ada -> rollback
    executor, knowledge, admin, agent = buat(sparql=fx.FakeSparql([fx.BASELINE, fx.BASELINE]))
    hasil = executor.execute(fx.plan_drop())
    assert hasil['status'] == 'rolled_back'
    # P-001: predikat lain tidak boleh hilang
    executor, knowledge, _, _ = buat(sparql=fx.FakeSparql([fx.BASELINE, fx.TANPA_STATUS]))
    assert executor.execute(fx.plan_add())['status'] == 'rolled_back'


def test_failed_reload_is_reported():
    executor, knowledge, admin, agent = buat(agent=fx.FakeAgent(reload_ok=False))
    with pytest.raises(ExecutionError, match='muat ulang'):
        executor.execute(fx.plan_drop())
    assert langkah(knowledge)[-1] == ('reload_ontop', 'failed')
    assert knowledge.finished['status'] == 'failed'


def test_stale_plan_is_refused_before_any_change():
    executor, knowledge, admin, agent = buat()
    with pytest.raises(ExecutionError, match='versi spesifikasi lain'):
        executor.execute(fx.plan_drop(base=999))
    assert knowledge.steps == [] and admin.deployed == [] and agent.files['r2rml'] == fx.MAPPING
    assert knowledge.superseded[0] == 1                 # tidak akan dicoba ulang setiap siklus


def test_write_conflict_is_handled_as_failure():
    """Artefak diubah pihak lain di antara pembacaan dan penulisan: penulisan ditolak agen."""
    class AgenBerubah(fx.FakeAgent):
        def artifact(self, kind):
            data = super().artifact(kind)
            return {**data, 'sha256': 'x' * 64}          # sidik jari usang

    executor, knowledge, admin, agent = buat(agent=AgenBerubah())
    with pytest.raises(ExecutionError, match='penulisan atau validasi'):
        executor.execute(fx.plan_drop())
    assert langkah(knowledge)[-1] == ('validate', 'failed')
    assert agent.files['r2rml'] == fx.MAPPING            # isi tidak berubah
    assert admin.undeployed == ['government-2-vdb.xml']
    assert knowledge.finished['status'] == 'failed'
