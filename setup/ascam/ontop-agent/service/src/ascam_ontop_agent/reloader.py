"""
Memuat ulang endpoint Ontop.

Ontop membaca ℳ dan 𝒯 saat inisialisasi dan tidak memiliki hot-reload untuk produksi
(ADR-0001), sehingga kontainer di-restart. Konfigurasi hasil ADR-0001 (init sebagai PID 1 dan
entrypoint yang meng-`exec` Java) membuat penghentian berlangsung di bawah satu detik.
Kesiapan diukur dari endpoint SPARQL yang menjawab, bukan dari status kontainer.
"""
import time
from dataclasses import dataclass

import httpx

from .config import AgentConfig


@dataclass
class ReloadResult:
    ok: bool
    stop_ms: int
    ready_ms: int
    total_ms: int
    error: str | None = None


def reload_ontop(cfg: AgentConfig, docker_client=None, http: httpx.Client | None = None,
                 stop_timeout: int = 10, ready_timeout: float = 120.0,
                 sleep=time.sleep) -> ReloadResult:
    start = time.perf_counter()
    try:
        if docker_client is None:
            import docker
            docker_client = docker.from_env()
        container = docker_client.containers.get(cfg.ontop_container)
        container.restart(timeout=stop_timeout)
    except Exception as exc:                        # noqa: BLE001
        elapsed = int((time.perf_counter() - start) * 1000)
        return ReloadResult(False, elapsed, 0, elapsed, f'{type(exc).__name__}: {exc}'[:300])

    stopped = time.perf_counter()
    client = http or httpx.Client(timeout=5)
    url = f'http://{cfg.ontop_container}:8080{cfg.sparql_path}'
    error = None
    try:
        while time.perf_counter() - stopped < ready_timeout:
            try:
                response = client.get(url, params={'query': 'ASK { ?s ?p ?o }'},
                                      headers={'Accept': 'application/sparql-results+json'})
                if response.status_code == 200:
                    now = time.perf_counter()
                    return ReloadResult(True, int((stopped - start) * 1000),
                                        int((now - stopped) * 1000), int((now - start) * 1000))
                error = f'HTTP {response.status_code}'
            except Exception as exc:                # noqa: BLE001 — endpoint belum siap
                error = f'{type(exc).__name__}'
            sleep(0.5)
    finally:
        if http is None:
            client.close()
    now = time.perf_counter()
    return ReloadResult(False, int((stopped - start) * 1000), int((now - stopped) * 1000),
                        int((now - start) * 1000), f'endpoint tidak siap: {error}')
