"""
Effector untuk T' dan M': reload Ontop.

Ontop membaca ontologi, mapping, dan metadata basis data (dari Teiid)
saat inisialisasi. Karena dev mode dimatikan, reload dilakukan dengan
me-restart kontainer Ontop melalui Docker Engine API (Docker SDK for Python).
"""

import logging
import time

import docker
import requests

log = logging.getLogger('ascam.ontop')


def restart_ontop(container_name: str, sparql_url: str,
                  timeout: int = 180) -> tuple[bool, float]:
    t0 = time.time()
    try:
        client = docker.from_env()
        client.containers.get(container_name).restart(timeout=10)
    except docker.errors.DockerException as exc:
        log.error('[Ontop] Gagal restart %s: %s', container_name, exc)
        return False, time.time() - t0

    ok = wait_ready(sparql_url, timeout=timeout, since=t0)
    return ok, time.time() - t0


def wait_ready(sparql_url: str, timeout: int = 180, since: float | None = None) -> bool:
    """Endpoint dianggap siap bila kueri ASK sederhana dijawab HTTP 200."""
    deadline = (since or time.time()) + timeout
    while time.time() < deadline:
        try:
            r = requests.get(
                sparql_url,
                params={'query': 'ASK { ?s ?p ?o }'},
                headers={'Accept': 'application/sparql-results+json'},
                timeout=5,
            )
            if r.status_code == 200:
                log.info('[Ontop] Endpoint SPARQL siap.')
                return True
            log.info('[Ontop] Endpoint menjawab HTTP %d, menunggu...', r.status_code)
        except requests.exceptions.RequestException:
            pass
        time.sleep(2)
    log.error('[Ontop] Endpoint tidak siap dalam %d s', timeout)
    return False
