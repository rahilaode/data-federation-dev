"""
Runs the Ontop CLI as a one-off container.

The volumes are taken from the Ontop container (`volumes_from`), so the agent needs to know no
host path, and the files used are guaranteed to be those of the endpoint. The network follows
that of the Ontop container so that Teiid can be reached.
"""
import time
from dataclasses import dataclass

from .config import AgentConfig

CLASSPATH = '/opt/ontop/lib/*:/opt/ontop/jdbc/*'


@dataclass
class CommandResult:
    ok: bool
    exit_code: int
    output: str
    duration_ms: int


class OntopRunner:
    """Docker wrapper; injected in tests."""

    def __init__(self, cfg: AgentConfig, docker_client=None):
        self.cfg = cfg
        self._client = docker_client

    @property
    def client(self):
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client

    def _network(self, container_name: str) -> str | None:
        if self.cfg.network:
            return self.cfg.network
        container = self.client.containers.get(container_name)
        networks = container.attrs['NetworkSettings']['Networks']
        return next(iter(networks), None)

    def run(self, args: list[str], container_name: str | None = None) -> CommandResult:
        # The volumes come from the Ontop instance whose artifacts are validated. With blue-green
        # (ADR-0022) that is the standby instance, which may be stopped.
        container_name = container_name or self.cfg.ontop_container
        t0 = time.perf_counter()
        command = ['-cp', CLASSPATH, '-Dlogback.configurationFile=/opt/ontop/log/logback.xml',
                   'it.unibz.inf.ontop.cli.Ontop', *args]
        try:
            container = self.client.containers.run(
                self.cfg.ontop_image, command=command, entrypoint='java',
                volumes_from=[container_name], network=self._network(container_name),
                detach=True, remove=False)
        except Exception as exc:                        # noqa: BLE001
            return CommandResult(False, -1, f'{type(exc).__name__}: {exc}'[:2000],
                                 int((time.perf_counter() - t0) * 1000))
        try:
            status = container.wait(timeout=180)
            code = int(status.get('StatusCode', -1))
            logs = container.logs().decode('utf-8', 'replace')
        finally:
            try:
                container.remove(force=True)
            except Exception:                           # noqa: BLE001
                pass
        return CommandResult(code == 0, code, logs[-4000:], int((time.perf_counter() - t0) * 1000))

    def validate(self, db_url: str | None = None, container_name: str | None = None) -> CommandResult:
        args = ['validate',
                '-m', self.cfg.container_path('r2rml'),
                '-t', self.cfg.container_path('ontology'),
                '-p', self.cfg.container_properties]
        if db_url:
            args += ['--db-url', db_url]
        return self.run(args, container_name)
