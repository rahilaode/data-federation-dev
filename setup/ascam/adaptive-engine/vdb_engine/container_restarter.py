"""
ASCAM Container Restarter
==========================
Restart Ontop dan Teiid container via Docker Unix socket.

Mengapa dua container perlu di-restart?
  - Teiid    : membaca government-vdb.xml saat startup
  - Ontop    : membaca mapping.obda dan ontology_file.ttl saat startup

Urutan restart yang benar:
  1. Restart Teiid dulu — karena Ontop menunggu koneksi ke Teiid port 31000
     (lihat wait-for-it.sh di docker-compose vkg-system)
  2. Tunggu Teiid port 31000 siap
  3. Restart Ontop
  4. Tunggu Ontop SPARQL endpoint siap
"""

import json
import socket
import time
import logging
import requests

from config.settings import (
    ONTOP_CONTAINER_NAME,
    TEIID_CONTAINER_NAME,
    DOCKER_SOCK,
)

log = logging.getLogger('ascam.restarter')


# ─────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────

def restart_all():
    """
    Restart Teiid lalu Ontop dengan urutan yang benar.
    Dipanggil setelah semua file (VDB, OBDA, TTL) sudah disimpan.
    """
    log.info('[Restart] Memulai sequence restart...')

    # Step 1: Restart Teiid
    teiid_ok = _restart_container(TEIID_CONTAINER_NAME)
    if not teiid_ok:
        log.error('[Restart] Teiid gagal restart. Abort.')
        return False

    # Step 2: Tunggu Teiid port 31000 siap
    log.info('[Restart] Menunggu Teiid port 31000...')
    teiid_ready = _wait_for_port(TEIID_CONTAINER_NAME, 31000, max_wait=120)
    if not teiid_ready:
        log.error('[Restart] Teiid tidak siap dalam 120 detik.')
        return False
    log.info('[Restart] Teiid siap.')

    # Step 3: Restart Ontop
    ontop_ok = _restart_container(ONTOP_CONTAINER_NAME)
    if not ontop_ok:
        log.error('[Restart] Ontop gagal restart.')
        return False

    # Step 4: Tunggu Ontop SPARQL siap
    log.info('[Restart] Menunggu Ontop SPARQL endpoint...')
    ontop_ready = _wait_for_ontop(max_wait=120)
    if not ontop_ready:
        log.error('[Restart] Ontop tidak siap dalam 120 detik.')
        return False

    log.info('[Restart] ✓ Sequence restart selesai. Semua service siap.')
    return True


def restart_ontop_only():
    """
    Restart hanya Ontop — dipakai jika hanya OBDA/TTL yang berubah
    (tidak ada perubahan VDB), sehingga Teiid tidak perlu di-restart.
    """
    log.info('[Restart] Restart Ontop only...')
    ok = _restart_container(ONTOP_CONTAINER_NAME)
    if ok:
        _wait_for_ontop(max_wait=120)
    return ok


# ─────────────────────────────────────────────────────────────
# INTERNAL HELPERS
# ─────────────────────────────────────────────────────────────

def _get_container_id(name: str) -> str | None:
    """Cari container ID berdasarkan nama via Docker API."""
    try:
        body = _docker_get('/containers/json')
        for c in body:
            for n in c.get('Names', []):
                if name in n:
                    return c['Id']
        log.warning('[Docker] Container %s tidak ditemukan', name)
        return None
    except Exception as e:
        log.error('[Docker] Gagal cari container %s: %s', name, e)
        return None


def _restart_container(name: str) -> bool:
    """Kirim POST /containers/{id}/restart ke Docker socket."""
    cid = _get_container_id(name)
    if cid is None:
        return False

    log.info('[Docker] Merestart %s (%s)...', name, cid[:12])
    try:
        status = _docker_post(f'/containers/{cid}/restart?t=10')
        if status in (200, 204):
            log.info('[Docker] Restart berhasil: %s', name)
            return True
        else:
            log.error('[Docker] Restart gagal, status=%d: %s', status, name)
            return False
    except Exception as e:
        log.exception('[Docker] Error restart %s: %s', name, e)
        return False


def _wait_for_port(host: str, port: int, max_wait: int = 120) -> bool:
    """Poll TCP port sampai terbuka atau timeout."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=3)
            s.close()
            return True
        except (ConnectionRefusedError, OSError):
            time.sleep(3)
    return False


def _wait_for_ontop(max_wait: int = 120) -> bool:
    """Poll SPARQL endpoint Ontop sampai siap atau timeout."""
    url      = f'http://{ONTOP_CONTAINER_NAME}:8080/sparql'
    deadline = time.time() + max_wait

    while time.time() < deadline:
        try:
            r = requests.get(
                url,
                params  = {'query': 'ASK { ?s ?p ?o }'},
                headers = {'Accept': 'application/sparql-results+json'},
                timeout = 5,
            )
            if r.status_code in (200, 400):
                log.info('[Restart] Ontop SPARQL endpoint siap.')
                return True
        except requests.exceptions.ConnectionError:
            pass
        log.info('[Restart] Ontop belum siap, retry...')
        time.sleep(5)

    return False


# ── Docker socket helpers ─────────────────────────────────────

def _docker_get(path: str) -> list | dict:
    """GET request ke Docker Unix socket, kembalikan JSON."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(DOCKER_SOCK)
    req = f'GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n'
    sock.sendall(req.encode())

    resp = b''
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        resp += chunk
    sock.close()

    body = resp.decode('utf-8', errors='ignore')
    # Lewati header HTTP, ambil JSON body
    json_start = body.find('[') if '[' in body else body.find('{')
    return json.loads(body[json_start:])


def _docker_post(path: str) -> int:
    """POST request ke Docker Unix socket, kembalikan HTTP status code."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(DOCKER_SOCK)
    req = (
        f'POST {path} HTTP/1.1\r\n'
        f'Host: localhost\r\n'
        f'Content-Length: 0\r\n'
        f'Connection: close\r\n\r\n'
    )
    sock.sendall(req.encode())

    resp = b''
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        resp += chunk
    sock.close()

    status_line = resp.decode('utf-8', errors='ignore').split('\r\n')[0]
    # "HTTP/1.1 204 No Content" → 204
    try:
        return int(status_line.split()[1])
    except (IndexError, ValueError):
        return 0