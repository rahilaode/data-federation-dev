"""
Urutan eksekusi adaptation plan.

    1. Snapshot F = (T, M, Σ_S)
    2. Σ'_S : tulis VDB (atomic) -> redeploy via scanner -> tunggu .deployed
    3. M', T': tulis mapping & ontologi (atomic)
    4. Reload Ontop -> tunggu endpoint siap
    5. Verifikasi SPARQL
    6. Bila langkah 2/4/5 gagal: revert ke F, redeploy, reload,
       dan tandai event untuk HITL.

Catatan metodologis tentang revert:
    Setelah DDL dieksekusi di sumber, F yang lama juga sudah tidak konsisten
    terhadap Σ_i yang baru. Revert TIDAK memulihkan layanan; tujuannya
    menjaga artefak pada spesifikasi terakhir yang diketahui dan
    mengeskalasi event ke administrator (HITL).

Urutan Σ'_S sebelum M' penting: Ontop membaca metadata dari Teiid saat
inisialisasi, sehingga M' yang merujuk kolom baru hanya dapat dimuat
setelah Teiid mengekspos kolom tersebut.
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import teiid_deployer, ontop_controller, verifier
from .artifact_store import atomic_write, Snapshot
from .verifier import VerificationCheck

log = logging.getLogger('ascam.executor')


@dataclass
class ArtifactChange:
    kind: str          # 'vdb' | 'mapping' | 'ontology'
    path: str
    content: str


@dataclass
class AdaptationPlan:
    event_id: str
    source: str                       # nama sumber/model, mis. 'kemensos'
    table: str
    alter_type: str
    pattern_id: str
    changes: list[ArtifactChange]
    checks: list[VerificationCheck] = field(default_factory=list)
    timestamps: dict = field(default_factory=dict)   # t_source, t_kafka, t_received, t_planned

    def change(self, kind: str) -> ArtifactChange | None:
        return next((c for c in self.changes if c.kind == kind), None)


class AdaptationExecutor:

    def __init__(self, *, snapshot_dir: str, adaptation_log: str,
                 all_artifact_paths: list[str],
                 ontop_container: str, sparql_url: str,
                 teiid_timeout: int = 120, teiid_poll: float = 0.5,
                 ontop_timeout: int = 180, verify_timeout: int = 30,
                 verify_enabled: bool = True):
        self.snapshot_dir   = snapshot_dir
        self.adaptation_log = Path(adaptation_log)
        self.all_paths      = all_artifact_paths
        self.ontop          = ontop_container
        self.sparql_url     = sparql_url
        self.teiid_timeout  = teiid_timeout
        self.teiid_poll     = teiid_poll
        self.ontop_timeout  = ontop_timeout
        self.verify_timeout = verify_timeout
        self.verify_enabled = verify_enabled

    # ─────────────────────────────────────────────────────────
    def run(self, plan: AdaptationPlan) -> dict:
        t = dict(plan.timestamps)
        record = {'event_id': plan.event_id, 'source': plan.source,
                  'table': plan.table, 'alter_type': plan.alter_type,
                  'pattern_id': plan.pattern_id,
                  'artifacts': [c.kind for c in plan.changes]}

        snap = Snapshot(self.snapshot_dir, plan.event_id, self.all_paths)
        t['t_exec_start'] = time.time()

        status, failure = 'SUCCESS', None
        vdb = plan.change('vdb')

        # ── Langkah 2: Σ'_S ─────────────────────────────────
        if vdb:
            atomic_write(vdb.path, vdb.content)
            res = teiid_deployer.redeploy_vdb(vdb.path, self.teiid_timeout, self.teiid_poll)
            t['t_vdb_deployed'] = time.time()
            if not res.ok:
                status, failure = 'FAILED_VDB_DEPLOY', res.detail

        # ── Langkah 3: M', T' ───────────────────────────────
        if status == 'SUCCESS':
            for kind in ('mapping', 'ontology'):
                ch = plan.change(kind)
                if ch:
                    atomic_write(ch.path, ch.content)
            t['t_artifacts_written'] = time.time()

            # ── Langkah 4: reload Ontop ─────────────────────
            ok, _ = ontop_controller.restart_ontop(self.ontop, self.sparql_url,
                                                   self.ontop_timeout)
            t['t_ontop_ready'] = time.time()
            if not ok:
                status, failure = 'FAILED_ONTOP_RELOAD', 'endpoint tidak siap'

        # ── Langkah 5: verifikasi ───────────────────────────
        if status == 'SUCCESS' and self.verify_enabled and plan.checks:
            report = verifier.run_checks(self.sparql_url, plan.checks, self.verify_timeout)
            t['t_verified'] = time.time()
            record['verification'] = report.as_dict()
            if not report.passed:
                status, failure = 'FAILED_VERIFICATION', 'lihat verification.results'

        # ── Langkah 6: revert + eskalasi ────────────────────
        if status != 'SUCCESS':
            log.error('[Executor] %s: %s -> revert ke F dan eskalasi HITL', status, failure)
            snap.restore()
            revert = {'vdb_redeployed': None}
            if vdb:
                res = teiid_deployer.redeploy_vdb(vdb.path, self.teiid_timeout, self.teiid_poll)
                revert['vdb_redeployed'] = res.ok
                if not res.ok:
                    revert['vdb_detail'] = res.detail
            revert['ontop_ready'], _ = ontop_controller.restart_ontop(
                self.ontop, self.sparql_url, self.ontop_timeout)
            t['t_reverted'] = time.time()
            record['revert'] = revert
            record['needs_hitl'] = True

        t['t_end'] = time.time()
        record.update(status=status, failure=failure, snapshot=str(snap.dir),
                      timestamps=t, durations=_durations(t))
        self._append_log(record)
        log.info('[Executor] Event %s selesai: %s (Δt_adapt=%s s)',
                 plan.event_id, status, record['durations'].get('adapt_total'))
        return record

    # ─────────────────────────────────────────────────────────
    def _append_log(self, record: dict) -> None:
        self.adaptation_log.parent.mkdir(parents=True, exist_ok=True)
        with self.adaptation_log.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + '\n')


def _durations(t: dict) -> dict:
    """Dekomposisi Δt_adapt (detik). Kunci yang tidak tersedia dilewati."""
    def d(a, b):
        return round(t[b] - t[a], 3) if a in t and b in t else None

    last = 't_verified' if 't_verified' in t else 't_ontop_ready'
    out = {
        # t_source = captured_at di ddl_event_log (waktu pencatatan, bukan waktu
        # DDL dieksekusi). Jeda DDL -> captured_at diukur dari sisi eksperimen.
        'capture_to_kafka': d('t_source', 't_kafka'),
        'deliver'      : d('t_kafka', 't_received'),      # Kafka -> diterima Orchestrator
        'analyze_plan' : d('t_received', 't_planned'),
        'vdb_redeploy' : d('t_exec_start', 't_vdb_deployed'),
        'ontop_reload' : d('t_artifacts_written', 't_ontop_ready'),
        'verify'       : d('t_ontop_ready', 't_verified'),
        'adapt_total'  : d('t_source', last),             # captured_at -> F' siap
    }
    return {k: v for k, v in out.items() if v is not None}
