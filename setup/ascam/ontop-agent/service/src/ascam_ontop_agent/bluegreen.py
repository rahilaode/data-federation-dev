"""
Blue-green pada lapisan OBDA (ADR-0022).

Dua instance Ontop, `<prefix>-blue` dan `<prefix>-green`, berjalan di belakang proxy nginx
yang menempati nama dan port endpoint lama (`vkg-system-ontop-teiid:8080`). Setiap instance
membaca slotnya sendiri (`slots/<warna>/`: mapping, ontologi, berkas properti) dan terkunci
pada SATU versi VDB lewat `;version=N` pada URL JDBC. Satu instance aktif melayani kueri;
yang lain siaga dan dalam keadaan berhenti.

Satu adaptasi:
  prepare   ℳ′ dan 𝒯′ ditulis ke slot siaga, URL JDBC dikunci ke versi VDB baru
  validate  `ontop validate` dengan volume instance siaga (agen: ontop_cli)
  start     instance siaga dinyalakan dan ditunggu sampai endpoint SPARQL-nya menjawab
  (verifikasi dilakukan Executor langsung ke instance siaga, sebelum lalu lintas dialihkan)
  switch    upstream proxy diganti dan nginx dimuat ulang secara graceful, artefak siaga
            dipromosikan ke direktori artefak kanonik, lalu instance lama dihentikan
  discard   bila validasi atau verifikasi gagal: instance siaga dihentikan; pengguna tidak
            pernah melihatnya

Direktori artefak kanonik (`config/`) selalu mencerminkan instance AKTIF, sehingga Knowledge
tetap membaca ℳ dan 𝒯 dari tempat yang sama seperti sebelum blue-green.
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
    """URL JDBC dikunci ke satu versi VDB; versi lain yang tercantum diganti."""
    def ganti(match: re.Match) -> str:
        url = re.sub(r';version=[^;\s]*', '', match.group('url').strip())
        return f"{match.group('key')}{url};version={version}"
    hasil, jumlah = JDBC_URL.subn(ganti, properties, count=1)
    if not jumlah:
        raise BlueGreenError('berkas properti Ontop tidak memuat jdbc.url')
    return hasil


def upstream_conf(container: str) -> str:
    return (f'# dikelola agen ASCAM (ADR-0022); jangan diubah manual\n'
            f'set $ontop_upstream http://{container}:8080;\n')


class BlueGreen:
    def __init__(self, cfg: AgentConfig, runner=None, docker_client=None,
                 http: httpx.Client | None = None, sleep=time.sleep):
        if cfg.slots_dir is None or cfg.proxy_runtime_dir is None:
            raise BlueGreenError('blue-green tidak dikonfigurasi (slots_dir/proxy_runtime_dir)')
        self.cfg = cfg
        self.runner = runner
        self._docker = docker_client
        self.http = http or httpx.Client(timeout=5)
        self.sleep = sleep

    # ── keadaan ─────────────────────────────────────────────────────────────────
    @property
    def docker(self):
        if self._docker is None:
            import docker
            self._docker = docker.from_env()
        return self._docker

    def state(self) -> dict:
        path = self.cfg.slots_dir / STATE_FILE
        if not path.is_file():
            raise BlueGreenError(f'{path} tidak ada; jalankan setup/vkg-system/init-bluegreen.sh')
        data = json.loads(path.read_text())
        if data.get('active') not in COLORS:
            raise BlueGreenError(f'warna aktif tidak dikenal: {data.get("active")!r}')
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

    # ── langkah ─────────────────────────────────────────────────────────────────
    def prepare(self, mapping: str, ontology: str, vdb_version: str,
                expected: dict[str, str | None] | None = None) -> StepResult:
        """Menulis ℳ′, 𝒯′, dan properti terkunci ke slot siaga."""
        t0 = time.perf_counter()
        expected = expected or {}
        for kind in ('r2rml', 'ontology'):               # dikunci pada isi instance aktif
            want = expected.get(kind)
            if want:
                current = writer._digest(self.cfg.path_of(kind).read_bytes())
                if current != want:
                    raise writer.Conflict(f'isi {kind} sudah berubah (sha256 {current[:12]}, '
                                          f'diharapkan {want[:12]})')
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
            raise BlueGreenError('runner validasi tidak tersedia')
        standby = other(self.state()['active'])
        return standby, self.runner.validate(db_url, container_name=self.container(standby))

    def start(self, ready_timeout: float = 180.0) -> StepResult:
        """Menyalakan instance siaga (atau menyalakan ulang bila masih berjalan)."""
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
        return StepResult(ok, standby, _ms(t0), None if ok else f'endpoint tidak siap: {error}',
                          detail={'start_ms': _ms(t0) - _ms(started), 'ready_ms': _ms(started)})

    def switch(self) -> StepResult:
        """Mengalihkan proxy ke instance siaga, mempromosikan artefaknya, menghentikan yang lama."""
        t0 = time.perf_counter()
        state = self.state()
        old, new = state['active'], other(state['active'])
        runtime = self.cfg.proxy_runtime_dir
        previous = (runtime / UPSTREAM_FILE).read_text() if (runtime / UPSTREAM_FILE).is_file() else None
        self._write_upstream(new)
        reload_error = self._reload_proxy()
        if reload_error:
            if previous is not None:                    # proxy tetap pada instance lama
                (runtime / UPSTREAM_FILE).write_text(previous)
                self._reload_proxy()
            return StepResult(False, new, _ms(t0), f'nginx gagal dimuat ulang: {reload_error}')
        proxy_url = f'http://{self.cfg.proxy_container}:8080{self.cfg.sparql_path}'
        ok, error = self._wait_ready(proxy_url, 30.0)
        if not ok:
            return StepResult(False, new, _ms(t0), f'proxy tidak menjawab: {error}')
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
        """Instance aktif dibangun ulang dari artefak kanonik, terkunci pada `vdb_version`.

        Dipakai harness evaluasi untuk kembali ke keadaan dasar; endpoint boleh terputus."""
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
        return StepResult(ok, active, _ms(t0), None if ok else f'endpoint tidak siap: {error}')

    # ── pembantu ────────────────────────────────────────────────────────────────
    def _write_upstream(self, color: str) -> None:
        runtime = self.cfg.proxy_runtime_dir
        runtime.mkdir(parents=True, exist_ok=True)
        temporary = runtime / f'.{UPSTREAM_FILE}.ascam-tmp'
        temporary.write_text(upstream_conf(self.container(color)))
        temporary.replace(runtime / UPSTREAM_FILE)

    def _reload_proxy(self) -> str | None:
        """`nginx -s reload`: worker lama menyelesaikan permintaan yang sedang berjalan."""
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
            except Exception as exc:                    # noqa: BLE001 — belum siap
                error = type(exc).__name__
            self.sleep(0.5)
        return False, error


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def as_dict(result: StepResult) -> dict:
    return asdict(result)
