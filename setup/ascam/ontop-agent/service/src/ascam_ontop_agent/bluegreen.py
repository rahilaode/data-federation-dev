"""
Blue-green on the OBDA layer (ADR-0022).

Two Ontop instances, `<prefix>-blue` and `<prefix>-green`, run behind an nginx proxy that takes
the name and port of the former endpoint (`vkg-system-ontop-teiid:8080`). Every instance reads its
own slot (`slots/<colour>/`: mapping, ontology, properties file) and is bound to ONE VDB version
through `;version=N` in the JDBC URL. One instance is active and answers queries; the other is
the standby and is stopped.

One adaptation:
  prepare   ℳ′ and 𝒯′ are written to the standby slot, with the JDBC URL bound to the new VDB version
  validate  `ontop validate` with the volume of the standby instance (agent: ontop_cli)
  start     the standby instance is started and awaited until its SPARQL endpoint answers
  (the Executor verifies directly against the standby instance, before traffic is switched)
  switch    the proxy upstream is replaced and nginx reloaded gracefully, the standby artifacts
            are promoted to the canonical artifact directory, and the old instance is stopped
  discard   if validation or verification fails: the standby instance is stopped; users never
            see it

The canonical artifact directory (`config/`) always reflects the ACTIVE instance, so Knowledge
keeps reading ℳ and 𝒯 from the same place as before blue-green.
"""
import json
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from . import writer
from .config import AgentConfig

COLORS = ('blue', 'green')
STATE_FILE = 'state.json'
UPSTREAM_FILE = 'upstream.conf'
JDBC_URL = re.compile(r'^(?P<key>\s*jdbc\.url\s*=\s*)(?P<url>.*)$', re.MULTILINE)


class BlueGreenError(Exception):
    pass


@dataclass
class StepResult:
    ok: bool
    color: str
    duration_ms: int
    error: str | None = None
    detail: dict | None = None


def other(color: str) -> str:
    return COLORS[1 - COLORS.index(color)]


def pin_version(properties: str, version: str | int) -> str:
    """Binds the JDBC URL to one VDB version; any other version given is replaced."""
    def replace(match: re.Match) -> str:
        url = re.sub(r';version=[^;\s]*', '', match.group('url').strip())
        return f"{match.group('key')}{url};version={version}"
    result, count = JDBC_URL.subn(replace, properties, count=1)
    if not count:
        raise BlueGreenError('the Ontop properties file has no jdbc.url')
    return result


def upstream_conf(container: str) -> str:
    return (f'# managed by the ASCAM agent (ADR-0022); do not edit by hand\n'
            f'set $ontop_upstream http://{container}:8080;\n')


class BlueGreen:
    def __init__(self, cfg: AgentConfig, runner=None, docker_client=None,
                 http: httpx.Client | None = None, sleep=time.sleep):
        if cfg.slots_dir is None or cfg.proxy_runtime_dir is None:
            raise BlueGreenError('blue-green is not configured (slots_dir/proxy_runtime_dir)')
        self.cfg = cfg
        self.runner = runner
        self._docker = docker_client
        self.http = http or httpx.Client(timeout=5)
        self.sleep = sleep

    # ── state ───────────────────────────────────────────────────────────────────
    @property
    def docker(self):
        if self._docker is None:
            import docker
            self._docker = docker.from_env()
        return self._docker

    def state(self) -> dict:
        path = self.cfg.slots_dir / STATE_FILE
        if not path.is_file():
            raise BlueGreenError(f'{path} does not exist; run setup/vkg-system/init-bluegreen.sh')
        data = json.loads(path.read_text())
        if data.get('active') not in COLORS:
            raise BlueGreenError(f'unknown active colour: {data.get("active")!r}')
        return data

    def _save_state(self, data: dict) -> None:
        path = self.cfg.slots_dir / STATE_FILE
        temporary = path.with_name(f'.{path.name}.ascam-tmp')
        temporary.write_text(json.dumps(data, indent=2) + '\n')
        temporary.replace(path)

    def container(self, color: str) -> str:
        return f'{self.cfg.instance_prefix}-{color}'

    def slot(self, color: str) -> Path:
        return self.cfg.slots_dir / color

    def sparql_url(self, color: str) -> str:
        return f'http://{self.container(color)}:8080{self.cfg.sparql_path}'

    def describe(self) -> dict:
        data = self.state()
        standby = other(data['active'])
        return {**data, 'standby': standby,
                'active_container': self.container(data['active']),
                'standby_container': self.container(standby),
                'active_sparql_url': self.sparql_url(data['active']),
                'standby_sparql_url': self.sparql_url(standby)}

    # ── steps ───────────────────────────────────────────────────────────────────
    def prepare(self, mapping: str, ontology: str, vdb_version: str,
                expected: dict[str, str | None] | None = None) -> StepResult:
        """Writes ℳ′, 𝒯′, and the bound properties to the standby slot."""
        t0 = time.perf_counter()
        expected = expected or {}
        for kind in ('r2rml', 'ontology'):               # bound to the content of the active instance
            want = expected.get(kind)
            if want:
                current = writer._digest(self.cfg.path_of(kind).read_bytes())
                if current != want:
                    raise writer.Conflict(f'content of {kind} has changed (sha256 {current[:12]}, '
                                          f'expected {want[:12]})')
        standby = other(self.state()['active'])
        slot = self.slot(standby)
        slot.mkdir(parents=True, exist_ok=True)
        properties = (self.cfg.artifacts_dir / self.cfg.properties_name).read_text()
        for name, content in ((self.cfg.mapping_name, mapping),
                              (self.cfg.ontology_name, ontology),
                              (self.cfg.properties_name, pin_version(properties, vdb_version))):
            temporary = slot / f'.{name}.ascam-tmp'
            temporary.write_text(content, encoding='utf-8')
            temporary.replace(slot / name)
        state = self.state()
        state['standby_vdb_version'] = str(vdb_version)
        self._save_state(state)
        return StepResult(True, standby, _ms(t0), detail={'vdb_version': str(vdb_version)})

    def validate(self, db_url: str | None = None) -> tuple[str, object]:
        if self.runner is None:
            raise BlueGreenError('the validation runner is not available')
        standby = other(self.state()['active'])
        return standby, self.runner.validate(db_url, container_name=self.container(standby))

    def start(self, ready_timeout: float = 180.0) -> StepResult:
        """Starts the standby instance (or restarts it if it is still running)."""
        t0 = time.perf_counter()
        standby = other(self.state()['active'])
        try:
            container = self.docker.containers.get(self.container(standby))
            container.reload()
            if container.status == 'running':
                container.restart(timeout=10)
            else:
                container.start()
        except Exception as exc:                        # noqa: BLE001
            return StepResult(False, standby, _ms(t0), f'{type(exc).__name__}: {exc}'[:300])
        started = time.perf_counter()
        ok, error = self._wait_ready(self.sparql_url(standby), ready_timeout)
        return StepResult(ok, standby, _ms(t0), None if ok else f'endpoint not ready: {error}',
                          detail={'start_ms': _ms(t0) - _ms(started), 'ready_ms': _ms(started)})

    def switch(self) -> StepResult:
        """Switches the proxy to the standby instance, promotes its artifacts, and stops the old one."""
        t0 = time.perf_counter()
        state = self.state()
        old, new = state['active'], other(state['active'])
        runtime = self.cfg.proxy_runtime_dir
        previous = (runtime / UPSTREAM_FILE).read_text() if (runtime / UPSTREAM_FILE).is_file() else None
        self._write_upstream(new)
        reload_error = self._reload_proxy()
        if reload_error:
            if previous is not None:                    # the proxy stays on the old instance
                (runtime / UPSTREAM_FILE).write_text(previous)
                self._reload_proxy()
            return StepResult(False, new, _ms(t0), f'nginx failed to reload: {reload_error}')
        proxy_url = f'http://{self.cfg.proxy_container}:8080{self.cfg.sparql_path}'
        ok, error = self._wait_ready(proxy_url, 30.0)
        if not ok:
            return StepResult(False, new, _ms(t0), f'the proxy does not answer: {error}')
        switched_ms = _ms(t0)

        backups = {}
        for kind, name in (('r2rml', self.cfg.mapping_name), ('ontology', self.cfg.ontology_name)):
            _, backups[kind] = writer.write(self.cfg, kind,
                                            (self.slot(new) / name).read_text(encoding='utf-8'))
        state.update(active=new, vdb_version=state.get('standby_vdb_version', state.get('vdb_version')))
        state.pop('standby_vdb_version', None)
        self._save_state(state)
        stop_error = self._stop(old)
        return StepResult(True, new, _ms(t0), detail={
            'switch_ms': switched_ms, 'retired': old, 'retire_error': stop_error,
            'backups': backups, 'vdb_version': state['vdb_version']})

    def discard(self) -> StepResult:
        t0 = time.perf_counter()
        standby = other(self.state()['active'])
        error = self._stop(standby)
        state = self.state()
        state.pop('standby_vdb_version', None)
        self._save_state(state)
        return StepResult(error is None, standby, _ms(t0), error)

    def reset(self, vdb_version: str) -> StepResult:
        """The active instance is rebuilt from the canonical artifacts, bound to `vdb_version`.

        Used by the evaluation harness to return to the base state; the endpoint may be interrupted."""
        t0 = time.perf_counter()
        state = self.state()
        active = state['active']
        slot = self.slot(active)
        slot.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.cfg.path_of('r2rml'), slot / self.cfg.mapping_name)
        shutil.copyfile(self.cfg.path_of('ontology'), slot / self.cfg.ontology_name)
        properties = (self.cfg.artifacts_dir / self.cfg.properties_name).read_text()
        (slot / self.cfg.properties_name).write_text(pin_version(properties, vdb_version))
        self._stop(other(active))
        try:
            container = self.docker.containers.get(self.container(active))
            container.restart(timeout=10)
        except Exception as exc:                        # noqa: BLE001
            return StepResult(False, active, _ms(t0), f'{type(exc).__name__}: {exc}'[:300])
        state.update(vdb_version=str(vdb_version))
        state.pop('standby_vdb_version', None)
        self._save_state(state)
        self._write_upstream(active)
        self._reload_proxy()
        ok, error = self._wait_ready(self.sparql_url(active), 180.0)
        return StepResult(ok, active, _ms(t0), None if ok else f'endpoint not ready: {error}')

    # ── helpers ─────────────────────────────────────────────────────────────────
    def _write_upstream(self, color: str) -> None:
        runtime = self.cfg.proxy_runtime_dir
        runtime.mkdir(parents=True, exist_ok=True)
        temporary = runtime / f'.{UPSTREAM_FILE}.ascam-tmp'
        temporary.write_text(upstream_conf(self.container(color)))
        temporary.replace(runtime / UPSTREAM_FILE)

    def _reload_proxy(self) -> str | None:
        """`nginx -s reload`: old workers finish the requests they are serving."""
        try:
            proxy = self.docker.containers.get(self.cfg.proxy_container)
            code, output = proxy.exec_run(['nginx', '-s', 'reload'])
        except Exception as exc:                        # noqa: BLE001
            return f'{type(exc).__name__}: {exc}'[:300]
        if code != 0:
            return (output or b'').decode('utf-8', 'replace')[-300:]
        return None

    def _stop(self, color: str) -> str | None:
        try:
            container = self.docker.containers.get(self.container(color))
            container.reload()
            if container.status == 'running':
                container.stop(timeout=10)
        except Exception as exc:                        # noqa: BLE001
            return f'{type(exc).__name__}: {exc}'[:300]
        return None

    def _wait_ready(self, url: str, timeout: float) -> tuple[bool, str | None]:
        start, error = time.perf_counter(), None
        while time.perf_counter() - start < timeout:
            try:
                response = self.http.get(url, params={'query': 'ASK { ?s ?p ?o }'},
                                         headers={'Accept': 'application/sparql-results+json'})
                if response.status_code == 200:
                    return True, None
                error = f'HTTP {response.status_code}'
            except Exception as exc:                    # noqa: BLE001 — not ready yet
                error = type(exc).__name__
            self.sleep(0.5)
        return False, error


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def as_dict(result: StepResult) -> dict:
    return asdict(result)
