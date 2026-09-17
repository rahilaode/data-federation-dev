"""
Menjalankan CLI Ontop sebagai kontainer sekali jalan.

Volume diambil dari kontainer Ontop (`volumes_from`), sehingga agen tidak perlu mengetahui
path apa pun di host, dan berkas yang dipakai dijamin sama dengan yang dipakai endpoint.
Jaringan mengikuti jaringan kontainer Ontop agar Teiid dapat dihubungi.
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
    """Pembungkus Docker; disuntik saat pengujian."""

    def __init__(self, cfg: AgentConfig, docker_client=None):
        self.cfg = cfg
        self._client = docker_client

    @property
    def client(self):
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client

    def _network(self) -> str | None:
        container = self.client.containers.get(self.cfg.ontop_container)
        networks = container.attrs['NetworkSettings']['Networks']
        return next(iter(networks), None)

    def run(self, args: list[str]) -> CommandResult:
        t0 = time.perf_counter()
        command = ['-cp', CLASSPATH, '-Dlogback.configurationFile=/opt/ontop/log/logback.xml',
                   'it.unibz.inf.ontop.cli.Ontop', *args]
        try:
            container = self.client.containers.run(
                self.cfg.ontop_image, command=command, entrypoint='java',
                volumes_from=[self.cfg.ontop_container], network=self._network(),
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

    def validate(self, db_url: str | None = None) -> CommandResult:
        args = ['validate',
                '-m', self.cfg.container_path('r2rml'),
                '-t', self.cfg.container_path('ontology'),
                '-p', self.cfg.container_properties]
        if db_url:
            args += ['--db-url', db_url]
        return self.run(args)
