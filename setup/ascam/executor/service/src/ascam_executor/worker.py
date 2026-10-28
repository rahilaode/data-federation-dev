"""
Executor worker: takes plans with status `approved` from Knowledge and runs them.

One plan runs at a time, because every plan is built on a specific specification version;
the next plan is built again after the sync (ADR-0016).
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
    paused: bool = False

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
        missing = {'teiid_mgmt', 'ontop_agent', 'ontop_sparql'} - set(targets)
        if missing:
            raise RuntimeError(f'targets not registered or disabled: {sorted(missing)}')
        credentials = KnowledgeCredentials(knowledge, obdf_id)
        admin = TeiidAdminClient(targets['teiid_mgmt']['endpoint'],
                                 credentials.username(targets['teiid_mgmt']['credential_id'], 'admin'),
                                 self.settings.teiid_password())
        agent = AgentClient(targets['ontop_agent']['endpoint'], self.settings.agent_token())
        sparql = SparqlClient(targets['ontop_sparql']['endpoint'])
        executor = Executor(knowledge, admin, agent, sparql, obdf_id,
                            teiid_jdbc_host=self.settings.teiid_jdbc_host,
                            teiid_jdbc_port=self.settings.teiid_jdbc_port,
                            obda_strategy=self.settings.obda_strategy,
                            sparql_for=SparqlClient.from_url,
                            view_statement=self.settings.view_statement)
        return knowledge, obdf_id, executor

    def process_once(self, knowledge: KnowledgeClient, obdf_id: int, executor: Executor) -> int:
        plans = knowledge.approved_plans(obdf_id)
        for plan in sorted(plans, key=lambda p: p['id']):
            self.stats.plans_seen += 1
            self.stats.last_plan_id = plan['id']
            try:
                result = executor.execute(plan)
            except ExecutionError as exc:
                self.stats.failed += 1
                self.stats.last_status, self.stats.last_error = 'failed', str(exc)[:300]
                log.warning('plan %s failed: %s', plan['id'], exc)
                continue
            except Exception as exc:                # noqa: BLE001
                self.stats.failed += 1
                self.stats.last_status = 'error'
                self.stats.last_error = f'{type(exc).__name__}: {exc}'[:300]
                log.exception('plan %s stopped unexpectedly', plan['id'])
                continue
            self.stats.last_status = result.get('status')
            self.stats.last_timings = result.get('timings') or {}
            if result.get('status') == 'succeeded':
                self.stats.executed += 1
            else:
                self.stats.rolled_back += 1
            log.info('plan %s -> %s (%s ms)', plan['id'], result.get('status'),
                     self.stats.last_timings.get('total_ms'))
        return len(plans)

    def run(self) -> None:
        """Set-up is retried until it succeeds (Knowledge may be restarting at start-up)."""
        while not self._stop.is_set():
            try:
                knowledge, obdf_id, executor = self.build()
                break
            except Exception as exc:                # noqa: BLE001
                self.stats.state = 'retrying'
                self.stats.last_error = f'{type(exc).__name__}: {exc}'[:300]
                log.warning('set-up failed (%s); retrying', self.stats.last_error)
                if self._stop.wait(self.settings.poll_seconds):
                    self.stats.state = 'stopped'
                    return
        else:
            self.stats.state = 'stopped'
            return

        self.stats.state = 'running'
        self.stats.last_error = None
        while not self._stop.is_set():
            if self.stats.paused:
                self._stop.wait(0.5)
                continue
            try:
                self.process_once(knowledge, obdf_id, executor)
            except Exception as exc:                # noqa: BLE001 — the worker stays alive
                self.stats.last_error = f'{type(exc).__name__}: {exc}'[:300]
                log.warning('cycle failed: %s', self.stats.last_error)
            self._stop.wait(self.settings.poll_seconds)
        self.stats.state = 'stopped'

    def pause(self) -> None:
        self.stats.paused = True

    def resume(self) -> None:
        self.stats.paused = False

    def stop(self) -> None:
        self._stop.set()
