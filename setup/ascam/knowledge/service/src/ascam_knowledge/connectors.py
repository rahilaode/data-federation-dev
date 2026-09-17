"""
Uji koneksi ke target OBDF (tombol "Test connection" pada UI).

Setiap pemeriksa mengembalikan CheckResult dan TIDAK pernah memuat rahasia pada hasilnya:
pesan galat disaring dari kata sandi sebelum disimpan. Fungsi koneksi dapat disuntikkan
(parameter `http`, `connect`, `admin_factory`) agar dapat diuji tanpa layanan sungguhan.

Dasar protokol:
- teiid_mgmt  : WildFly HTTP management API, autentikasi Digest (ADR-0004)
- teiid_odbc  : transport ODBC Teiid (emulasi PostgreSQL), SYS.VirtualDatabases (ADR-0003)
- ontop_sparql: SPARQL 1.1 Protocol, kueri ASK
- kafka       : metadata broker dan keberadaan topik sumber
- ontop_agent : GET /health (agen dibangun pada F4)
"""
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

DEFAULT_TIMEOUT = 5.0


@dataclass
class CheckResult:
    ok: bool
    latency_ms: int
    summary: str
    facts: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def detail(self) -> dict[str, Any]:
        return {'summary': self.summary, 'facts': self.facts, 'error': self.error}


def _redact(text: str, *secrets: str | None) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, '***')
    return text[:500]


def _url(endpoint: dict, default_path: str = '') -> str:
    scheme = 'https' if endpoint.get('tls') else 'http'
    path = endpoint.get('path') or default_path
    return f"{scheme}://{endpoint['host']}:{endpoint['port']}{path}"


def _timed(fn: Callable[[], CheckResult], secret: str | None = None) -> CheckResult:
    t0 = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:                       # noqa: BLE001 — semua kegagalan dilaporkan
        result = CheckResult(False, 0, 'gagal terhubung',
                             error=_redact(f'{type(exc).__name__}: {exc}', secret))
    result.latency_ms = int((time.perf_counter() - t0) * 1000)
    return result


def check_teiid_mgmt(endpoint: dict, username: str, password: str,
                     http: httpx.Client | None = None, timeout: float = DEFAULT_TIMEOUT) -> CheckResult:
    def run():
        client = http or httpx.Client(timeout=timeout)
        try:
            auth = httpx.DigestAuth(username, password)
            url = _url(endpoint, '/management')
            state = client.post(url, json={'operation': 'read-attribute', 'name': 'server-state'}, auth=auth)
            if state.status_code == 401:
                return CheckResult(False, 0, 'autentikasi ditolak', error='HTTP 401')
            state.raise_for_status()
            body = state.json()
            version = client.post(url, json={'operation': 'read-attribute', 'name': 'product-version'},
                                  auth=auth).json().get('result')
            vdbs = client.post(url, json={'operation': 'list-vdbs', 'address': [{'subsystem': 'teiid'}]},
                               auth=auth).json().get('result') or []
            facts = {'server_state': body.get('result'), 'product_version': version,
                     'vdbs': [{'name': v.get('vdb-name'), 'version': v.get('vdb-version'),
                               'status': v.get('status'), 'connection_type': v.get('connection-type')}
                              for v in vdbs if isinstance(v, dict)]}
            ok = body.get('outcome') == 'success' and body.get('result') == 'running'
            return CheckResult(ok, 0, f"server {body.get('result')}", facts)
        finally:
            if http is None:
                client.close()
    return _timed(run, password)


def check_teiid_odbc(endpoint: dict, username: str, password: str,
                     connect: Callable | None = None, timeout: float = DEFAULT_TIMEOUT) -> CheckResult:
    def run():
        import psycopg
        vdb = endpoint.get('options', {}).get('vdb')
        conn_fn = connect or psycopg.connect
        with conn_fn(host=endpoint['host'], port=endpoint['port'], dbname=vdb, user=username,
                     password=password, sslmode='require' if endpoint.get('tls') else 'disable',
                     gssencmode='disable', connect_timeout=int(timeout)) as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT "Name", "Version", "ActiveTimestamp" FROM SYS.VirtualDatabases')
                name, version, active = cur.fetchone()
        return CheckResult(True, 0, f'VDB {name} v{version} aktif',
                           {'vdb': name, 'version': str(version),
                            'active_since': active.isoformat() if hasattr(active, 'isoformat') else active})
    return _timed(run, password)


def check_ontop_sparql(endpoint: dict, http: httpx.Client | None = None,
                       timeout: float = DEFAULT_TIMEOUT) -> CheckResult:
    def run():
        client = http or httpx.Client(timeout=timeout)
        try:
            r = client.get(_url(endpoint, '/sparql'), params={'query': 'ASK { ?s ?p ?o }'},
                           headers={'Accept': 'application/sparql-results+json'})
            if r.status_code != 200:
                return CheckResult(False, 0, f'HTTP {r.status_code}', error=r.text[:200])
            answer = r.json().get('boolean')
            return CheckResult(True, 0, 'endpoint SPARQL menjawab', {'ask_non_empty': answer})
        finally:
            if http is None:
                client.close()
    return _timed(run)


def check_kafka(endpoint: dict, expected_topics: list[str],
                admin_factory: Callable | None = None, timeout: float = DEFAULT_TIMEOUT) -> CheckResult:
    def run():
        if admin_factory is None:
            from kafka.admin import KafkaAdminClient
            factory = lambda: KafkaAdminClient(                       # noqa: E731
                bootstrap_servers=f"{endpoint['host']}:{endpoint['port']}",
                security_protocol='SSL' if endpoint.get('tls') else 'PLAINTEXT',
                request_timeout_ms=int(timeout * 1000), client_id='ascam-knowledge-check')
        else:
            factory = admin_factory
        admin = factory()
        try:
            topics = set(admin.list_topics())
        finally:
            admin.close()
        missing = sorted(t for t in expected_topics if t not in topics)
        facts = {'topic_count': len(topics), 'expected_topics': sorted(expected_topics),
                 'missing_topics': missing}
        if missing:
            return CheckResult(False, 0, f'{len(missing)} topik sumber belum ada', facts)
        return CheckResult(True, 0, 'broker menjawab; semua topik sumber ada', facts)
    return _timed(run)


def check_ontop_agent(endpoint: dict, token: str | None = None,
                      http: httpx.Client | None = None,
                      timeout: float = DEFAULT_TIMEOUT) -> CheckResult:
    def run():
        client = http or httpx.Client(timeout=timeout)
        try:
            base = _url(endpoint, '')
            health = client.get(f'{base}/health')
            if health.status_code != 200:
                return CheckResult(False, 0, f'HTTP {health.status_code}', error=health.text[:200])
            facts = health.json()
            if token:                       # sekaligus memastikan token diterima agen
                listing = client.get(f'{base}/api/v1/artifacts',
                                     headers={'Authorization': f'Bearer {token}'})
                if listing.status_code != 200:
                    return CheckResult(False, 0, 'token agen ditolak',
                                       facts, error=f'HTTP {listing.status_code}')
                facts['artifact_digests'] = {a['kind']: a['sha256'][:12] for a in listing.json()}
            return CheckResult(True, 0, 'agen menjawab', facts)
        finally:
            if http is None:
                client.close()
    return _timed(run, token)
