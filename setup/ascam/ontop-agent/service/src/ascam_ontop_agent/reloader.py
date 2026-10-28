"""
Reloads the Ontop endpoint.

Ontop reads ℳ and 𝒯 at initialisation and has no hot reload for production (ADR-0001), so the
container is restarted. The configuration from ADR-0001 (init as PID 1 and an entrypoint that
`exec`s Java) makes the shutdown take less than a second. Readiness is measured by the SPARQL
endpoint answering, not by the container status.
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
                 sleep=time.sleep, container_name: str | None = None) -> ReloadResult:
    # With blue-green (ADR-0022) the active instance is restarted, not the proxy.
    container_name = container_name or cfg.ontop_container
    start = time.perf_counter()
    try:
        if docker_client is None:
            import docker
            docker_client = docker.from_env()
        container = docker_client.containers.get(container_name)
        container.restart(timeout=stop_timeout)
    except Exception as exc:                        # noqa: BLE001
        elapsed = int((time.perf_counter() - start) * 1000)
        return ReloadResult(False, elapsed, 0, elapsed, f'{type(exc).__name__}: {exc}'[:300])

    stopped = time.perf_counter()
    client = http or httpx.Client(timeout=5)
    url = f'http://{container_name}:8080{cfg.sparql_path}'
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
            except Exception as exc:                # noqa: BLE001 — the endpoint is not ready yet
                error = f'{type(exc).__name__}'
            sleep(0.5)
    finally:
        if http is None:
            client.close()
    now = time.perf_counter()
    return ReloadResult(False, int((stopped - start) * 1000), int((now - stopped) * 1000),
                        int((now - start) * 1000), f'endpoint not ready: {error}')
