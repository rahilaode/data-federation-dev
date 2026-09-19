"""
Pekerja Executor: mengambil rencana berstatus `approved` dari Knowledge dan menjalankannya.

Satu rencana dieksekusi pada satu waktu, karena setiap rencana disusun di atas versi
spesifikasi tertentu; rencana berikutnya dibentuk ulang setelah sync (ADR-0016).
"""
import logging
import threading
import time
from dataclasses import asdict, dataclass, field

from .clients import AgentClient, KnowledgeClient, KnowledgeCredentials, SparqlClient, TeiidAdminClient
from .config import Settings
from .engine import ExecutionError, Executor

log = logging.getLogger('ascam.executor')


@dataclass
class Stats:
    state: str = 'starting'
    obdf_id: int | None = None
    plans_seen: int = 0
    executed: int = 0
    rolled_back: int = 0
    failed: int = 0
    last_plan_id: int | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_timings: dict = field(default_factory=dict)

    def snapshot(self) -> dict:
        return asdict(self)


class ExecutorWorker:
    def __init__(self, settings: Settings, knowledge: KnowledgeClient | None = None,
                 executor_factory=None):
        self.settings = settings
        self.stats = Stats()
        self._knowledge = knowledge
        self._executor_factory = executor_factory
        self._stop = threading.Event()

    def build(self) -> tuple[KnowledgeClient, int, Executor]:
        knowledge = self._knowledge or KnowledgeClient(self.settings.knowledge_url,
                                                       self.settings.knowledge_token())
        obdf_id = knowledge.obdf_id(self.settings.obdf_name)
        self.stats.obdf_id = obdf_id
        if self._executor_factory:
            return knowledge, obdf_id, self._executor_factory(knowledge, obdf_id)

        targets = {t['kind']: t for t in knowledge.targets(obdf_id) if t['enabled']}
        kurang = {'teiid_mgmt', 'ontop_agent', 'ontop_sparql'} - set(targets)
        if kurang:
            raise RuntimeError(f'target belum terdaftar atau nonaktif: {sorted(kurang)}')
        credentials = KnowledgeCredentials(knowledge, obdf_id)
        admin = TeiidAdminClient(targets['teiid_mgmt']['endpoint'],
                                 credentials.username(targets['teiid_mgmt']['credential_id'], 'admin'),
                                 self.settings.teiid_password())
        agent = AgentClient(targets['ontop_agent']['endpoint'], self.settings.agent_token())
        sparql = SparqlClient(targets['ontop_sparql']['endpoint'])
        executor = Executor(knowledge, admin, agent, sparql, obdf_id,
                            teiid_jdbc_host=self.settings.teiid_jdbc_host,
                            teiid_jdbc_port=self.settings.teiid_jdbc_port)
        return knowledge, obdf_id, executor

    def process_once(self, knowledge: KnowledgeClient, obdf_id: int, executor: Executor) -> int:
        plans = knowledge.approved_plans(obdf_id)
        for plan in sorted(plans, key=lambda p: p['id']):
            self.stats.plans_seen += 1
            self.stats.last_plan_id = plan['id']
            try:
                hasil = executor.execute(plan)
            except ExecutionError as exc:
                self.stats.failed += 1
                self.stats.last_status, self.stats.last_error = 'failed', str(exc)[:300]
                log.warning('rencana %s gagal: %s', plan['id'], exc)
                continue
            except Exception as exc:                # noqa: BLE001
                self.stats.failed += 1
                self.stats.last_status = 'error'
                self.stats.last_error = f'{type(exc).__name__}: {exc}'[:300]
                log.exception('rencana %s berhenti tak terduga', plan['id'])
                continue
            self.stats.last_status = hasil.get('status')
            self.stats.last_timings = hasil.get('timings') or {}
            if hasil.get('status') == 'succeeded':
                self.stats.executed += 1
            else:
                self.stats.rolled_back += 1
            log.info('rencana %s -> %s (%s ms)', plan['id'], hasil.get('status'),
                     self.stats.last_timings.get('total_ms'))
        return len(plans)

    def run(self) -> None:
        """Penyiapan diulang sampai berhasil (Knowledge dapat sedang restart saat start)."""
        while not self._stop.is_set():
            try:
                knowledge, obdf_id, executor = self.build()
                break
            except Exception as exc:                # noqa: BLE001
                self.stats.state = 'retrying'
                self.stats.last_error = f'{type(exc).__name__}: {exc}'[:300]
                log.warning('penyiapan gagal (%s); mencoba lagi', self.stats.last_error)
                if self._stop.wait(self.settings.poll_seconds):
                    self.stats.state = 'stopped'
                    return
        else:
            self.stats.state = 'stopped'
            return

        self.stats.state = 'running'
        self.stats.last_error = None
        while not self._stop.is_set():
            try:
                self.process_once(knowledge, obdf_id, executor)
            except Exception as exc:                # noqa: BLE001 — pekerja tetap hidup
                self.stats.last_error = f'{type(exc).__name__}: {exc}'[:300]
                log.warning('siklus gagal: %s', self.stats.last_error)
            self._stop.wait(self.settings.poll_seconds)
        self.stats.state = 'stopped'

    def stop(self) -> None:
        self._stop.set()
