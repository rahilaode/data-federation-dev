"""
Mesin eksekusi rencana adaptasi (ADR-0004, ADR-0018, ADR-0019).

Urutan langkah:
  1. deploy_vdb   Σ′_S di-deploy sebagai VERSI BARU di samping versi yang melayani.
                  Bila FAILED, versi lama tidak tersentuh dan eksekusi berhenti.
  2. validate     ℳ′ dan 𝒯′ ditulis lewat agen, lalu `ontop validate` dijalankan terhadap
                  versi VDB baru. Bila gagal, artefak dipulihkan dan VDB baru dihapus.
  3. switch       Koneksi dipindahkan ke versi baru (connection type ANY).
  4. reload_ontop Ontop dimuat ulang agar membaca ℳ′ dan 𝒯′.
  5. verify       Jawaban OBDF diperiksa dengan sidik jari graf, sesuai pola adaptasi.
  6. rollback     Bila verifikasi gagal: koneksi dikembalikan, artefak dipulihkan, Ontop
                  dimuat ulang, dan eksekusi ditandai rolled_back.

Setiap langkah dilaporkan ke Knowledge sehingga jejaknya tetap ada meski Executor berhenti.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .artifacts import ontology, r2rml, vdb


@dataclass
class Konteks:
    obdf_id: int
    plan: dict
    execution_id: int
    vdb_name: str
    versi_lama: str
    versi_baru: str
    deployment: str
    backups: dict[str, str] = field(default_factory=dict)
    baseline: dict[str, int] = field(default_factory=dict)
    timings: dict[str, int] = field(default_factory=dict)


class ExecutionError(Exception):
    def __init__(self, message: str, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail or {}


def _actions(plan: dict, artifact: str) -> list[dict]:
    keluaran = []
    for action in plan.get('actions', []):
        if action['artifact'] != artifact:
            continue
        keluaran.append({'operation': action['operation'], **(action.get('params') or {})})
    return keluaran


def _type_lookup(type_mappings: list[dict], dbms: str | None = None):
    tabel = {}
    for row in type_mappings:
        tabel.setdefault(row['native_type'].lower(), row['teiid_type'])
    def lookup(native_type: str | None) -> str | None:
        if not native_type:
            return None
        dasar = native_type.lower().split('(')[0].strip()
        panjang = native_type[len(dasar):].strip() if native_type.lower().startswith(dasar) else ''
        teiid = tabel.get(native_type.lower()) or tabel.get(dasar)
        if teiid is None:
            return None
        return f'{teiid}{panjang}' if panjang.startswith('(') and teiid == 'string' else teiid
    return lookup


def _xsd_for(type_mappings: list[dict], native_type: str | None) -> str | None:
    if not native_type:
        return None
    dasar = native_type.lower().split('(')[0].strip()
    for row in type_mappings:
        if row['native_type'].lower() in (native_type.lower(), dasar):
            return row['xsd_datatype']
    return None


class Executor:
    def __init__(self, knowledge, admin, agent, sparql, obdf_id: int, *, sleep=time.sleep,
                 teiid_jdbc_host: str = 'teiid', teiid_jdbc_port: int = 31000):
        self.knowledge = knowledge
        self.admin = admin
        self.agent = agent
        self.sparql = sparql
        self.obdf_id = obdf_id
        self.sleep = sleep
        self.jdbc = (teiid_jdbc_host, teiid_jdbc_port)

    # ── pelaporan ───────────────────────────────────────────────────────────────
    def _step(self, konteks: Konteks, seq: int, nama: str, status: str, mulai: float,
              **detail: Any) -> None:
        # `detail` tidak boleh memakai kunci yang bentrok dengan parameter fungsi ini
        assert not {'seq', 'name', 'status'} & set(detail), 'kunci detail bentrok'
        durasi = int((time.perf_counter() - mulai) * 1000)
        konteks.timings[nama] = durasi
        self.knowledge.add_step(konteks.execution_id, seq=seq, name=nama, status=status,
                                detail={'duration_ms': durasi, **detail},
                                finished_at=datetime.now(timezone.utc).isoformat())

    # ── langkah ─────────────────────────────────────────────────────────────────
    def _siapkan_artefak(self, plan: dict, versi_aktif: dict) -> dict:
        type_mappings = self.knowledge.type_mappings(self.obdf_id)
        xml = self.knowledge.artifact(versi_aktif['id'], 'vdb_xml')['content']
        versi_baru = vdb.next_version(xml)
        xml_baru, statements = vdb.apply_actions(xml, _actions(plan, 'vdb'),
                                                 type_lookup=_type_lookup(type_mappings),
                                                 new_version=versi_baru)
        mapping = self.agent.artifact('r2rml')
        ont = self.agent.artifact('ontology')
        aksi_mapping = []
        for action in _actions(plan, 'r2rml'):
            salinan = dict(action)
            if action['operation'] == 'add_predicate_object_map':
                salinan.setdefault('table', (plan['impact']['targets'] or [{}])[0].get('table')
                                   or _tabel_dari_vdb(plan))
                salinan['datatype_iri'] = _xsd_for(
                    type_mappings, _column_type(plan))
            elif action['operation'] == 'remove_predicate_object_map':
                salinan.setdefault('table', (plan['impact']['targets'] or [{}])[0].get('table'))
            aksi_mapping.append(salinan)
        aksi_ontologi = []
        for action in _actions(plan, 'ontology'):
            salinan = dict(action)
            if action['operation'] == 'add_datatype_property':
                salinan['range_iri'] = _xsd_for(type_mappings, _column_type(plan))
            aksi_ontologi.append(salinan)
        return {
            'vdb_xml': xml_baru, 'vdb_statements': statements, 'vdb_version': versi_baru,
            'r2rml': r2rml.apply_actions(mapping['content'], aksi_mapping),
            'r2rml_sha': mapping['sha256'],
            'ontology': ontology.apply_actions(ont['content'], aksi_ontologi),
            'ontology_sha': ont['sha256'],
        }

    def _verifikasi(self, konteks: Konteks) -> tuple[bool, dict]:
        sesudah = self.sparql.fingerprint()
        sebelum = konteks.baseline
        pattern = konteks.plan.get('pattern')
        predikat = _predikat(konteks.plan)
        hilang = sorted(set(sebelum) - set(sesudah))
        rincian = {'predikat_sebelum': len(sebelum), 'predikat_sesudah': len(sesudah),
                   'hilang': hilang, 'predikat_sasaran': predikat}
        if pattern == 'P-002' and predikat:
            ok = predikat not in sesudah and hilang in ([], [predikat])
            rincian['harapan'] = 'predikat sasaran hilang, predikat lain tetap'
        elif pattern == 'P-003':
            ok = sesudah == sebelum
            rincian['harapan'] = 'jawaban identik dengan sebelum adaptasi'
        else:                                        # P-001: kolom baru boleh belum berisi data
            ok = not hilang
            rincian['harapan'] = 'tidak ada predikat yang hilang'
        return ok, rincian

    def _pulihkan_artefak(self, konteks: Konteks) -> dict:
        dipulihkan = {}
        for kind, backup_id in konteks.backups.items():
            if backup_id:
                dipulihkan[kind] = self.agent.restore(backup_id)['sha256'][:12]
        return dipulihkan

    # ── alur utama ──────────────────────────────────────────────────────────────
    def execute(self, plan: dict) -> dict:
        versi_aktif = self.knowledge.active_version(self.obdf_id)
        if plan['base_spec_version_id'] != versi_aktif['id']:
            raise ExecutionError('rencana disusun di atas versi spesifikasi lain',
                                 {'base': plan['base_spec_version_id'], 'aktif': versi_aktif['id']})
        execution = self.knowledge.start_execution(plan['id'])
        artefak = self._siapkan_artefak(plan, versi_aktif)
        konteks = Konteks(obdf_id=self.obdf_id, plan=plan, execution_id=execution['id'],
                          vdb_name=versi_aktif['teiid_vdb_name'],
                          versi_lama=str(versi_aktif['teiid_vdb_version']),
                          versi_baru=artefak['vdb_version'],
                          deployment=f"{versi_aktif['teiid_vdb_name']}-{artefak['vdb_version']}-vdb.xml")
        mulai_total = time.perf_counter()
        try:
            self._deploy(konteks, artefak)
            self._tulis_dan_validasi(konteks, artefak)
            konteks.baseline = self.sparql.fingerprint()
            self._switch(konteks)
            self._reload(konteks)
            ok, rincian = self._verify(konteks)
            if not ok:
                return self._rollback(konteks, rincian, mulai_total)
            return self._selesai(konteks, mulai_total)
        except ExecutionError as exc:
            self.knowledge.finish_execution(konteks.execution_id, status='failed',
                                            timings=konteks.timings,
                                            failure={'message': str(exc), **exc.detail})
            raise

    def _deploy(self, konteks: Konteks, artefak: dict) -> None:
        mulai = time.perf_counter()
        self.admin.deploy(konteks.deployment, artefak['vdb_xml'].encode())
        status, errors = self.admin.wait_until_settled(konteks.vdb_name, konteks.versi_baru,
                                                       sleep=self.sleep)
        self.knowledge.add_validation(konteks.execution_id, validator='teiid_status',
                                      passed=status == 'ACTIVE',
                                      details={'status': status, 'errors': errors,
                                               'vdb_version': konteks.versi_baru})
        if status != 'ACTIVE':
            self._step(konteks, 1, 'deploy_vdb', 'failed', mulai, vdb_status=status,
                       errors=errors)
            self.admin.undeploy(konteks.deployment)
            raise ExecutionError(f'VDB versi {konteks.versi_baru} berstatus {status}',
                                 {'errors': errors})
        self._step(konteks, 1, 'deploy_vdb', 'succeeded', mulai, vdb_version=konteks.versi_baru,
                   statements=artefak['vdb_statements'])

    def _tulis_dan_validasi(self, konteks: Konteks, artefak: dict) -> None:
        mulai = time.perf_counter()
        try:
            konteks.backups['r2rml'] = self.agent.write('r2rml', artefak['r2rml'],
                                                        artefak['r2rml_sha'])['backup_id']
            konteks.backups['ontology'] = self.agent.write('ontology', artefak['ontology'],
                                                           artefak['ontology_sha'])['backup_id']
            host, port = self.jdbc
            db_url = (f'jdbc:teiid:{konteks.vdb_name}@mm://{host}:{port}'
                      f';version={konteks.versi_baru}')
            hasil = self.agent.validate(db_url)
        except Exception as exc:                    # noqa: BLE001
            self._pulihkan_artefak(konteks)
            self.admin.undeploy(konteks.deployment)
            self._step(konteks, 2, 'validate', 'failed', mulai, error=str(exc)[:300])
            raise ExecutionError(f'penulisan atau validasi artefak gagal: {exc}') from exc

        self.knowledge.add_validation(konteks.execution_id, validator='ontop_validate',
                                      passed=bool(hasil.get('ok')),
                                      details={'exit_code': hasil.get('exit_code'),
                                               'output': (hasil.get('output') or '')[-1000:]})
        if not hasil.get('ok'):
            self._pulihkan_artefak(konteks)
            self.admin.undeploy(konteks.deployment)
            self._step(konteks, 2, 'validate', 'failed', mulai, exit_code=hasil.get('exit_code'))
            raise ExecutionError('ontop validate menolak artefak baru',
                                 {'output': (hasil.get('output') or '')[-500:]})
        self._step(konteks, 2, 'validate', 'succeeded', mulai, exit_code=0)

    def _switch(self, konteks: Konteks) -> None:
        mulai = time.perf_counter()
        self.admin.set_connection_type(konteks.vdb_name, konteks.versi_baru, 'ANY')
        self._step(konteks, 3, 'switch', 'succeeded', mulai, connection_type='ANY',
                   vdb_version=konteks.versi_baru)

    def _reload(self, konteks: Konteks) -> None:
        mulai = time.perf_counter()
        hasil = self.agent.reload()
        status = 'succeeded' if hasil.get('ok') else 'failed'
        self._step(konteks, 4, 'reload_ontop', status, mulai, **{k: hasil.get(k) for k in
                                                                 ('stop_ms', 'ready_ms', 'total_ms',
                                                                  'error')})
        if not hasil.get('ok'):
            raise ExecutionError(f"muat ulang Ontop gagal: {hasil.get('error')}")

    def _verify(self, konteks: Konteks) -> tuple[bool, dict]:
        mulai = time.perf_counter()
        try:
            ok, rincian = self._verifikasi(konteks)
        except Exception as exc:                    # noqa: BLE001
            ok, rincian = False, {'error': str(exc)[:300]}
        self.knowledge.add_validation(konteks.execution_id, validator='sparql_regression',
                                      passed=ok, details=rincian)
        self._step(konteks, 5, 'verify', 'succeeded' if ok else 'failed', mulai, **rincian)
        return ok, rincian

    def _rollback(self, konteks: Konteks, rincian: dict, mulai_total: float) -> dict:
        mulai = time.perf_counter()
        self.admin.set_connection_type(konteks.vdb_name, konteks.versi_baru, 'NONE')
        self.admin.set_connection_type(konteks.vdb_name, konteks.versi_lama, 'ANY')
        dipulihkan = self._pulihkan_artefak(konteks)
        muat = self.agent.reload()
        self._step(konteks, 6, 'rollback', 'succeeded' if muat.get('ok') else 'failed', mulai,
                   artefak=dipulihkan, alasan=rincian, reload_ok=muat.get('ok'))
        konteks.timings['total_ms'] = int((time.perf_counter() - mulai_total) * 1000)
        return self.knowledge.finish_execution(konteks.execution_id, status='rolled_back',
                                               timings=konteks.timings,
                                               failure={'message': 'verifikasi gagal', **rincian})

    def _selesai(self, konteks: Konteks, mulai_total: float) -> dict:
        self.admin.set_connection_type(konteks.vdb_name, konteks.versi_lama, 'NONE')
        mulai = time.perf_counter()
        hasil_sync = self.knowledge.sync(self.obdf_id)
        self._step(konteks, 6, 'sync', 'succeeded', mulai,
                   spec_version_id=hasil_sync.get('spec_version_id'),
                   version_no=hasil_sync.get('version_no'))
        konteks.timings['total_ms'] = int((time.perf_counter() - mulai_total) * 1000)
        return self.knowledge.finish_execution(
            konteks.execution_id, status='succeeded', timings=konteks.timings,
            candidate_spec_version_id=hasil_sync.get('spec_version_id'))


def _predikat(plan: dict) -> str | None:
    for action in plan.get('actions', []):
        params = action.get('params') or {}
        if params.get('predicate_iri'):
            return params['predicate_iri']
        if params.get('iri'):
            return params['iri']
    return None


def _column_type(plan: dict) -> str | None:
    for action in plan.get('actions', []):
        params = action.get('params') or {}
        if params.get('column_type'):
            return params['column_type']
    return None


def _tabel_dari_vdb(plan: dict) -> str | None:
    for action in plan.get('actions', []):
        if action['artifact'] == 'vdb':
            return (action.get('params') or {}).get('table')
    return None
