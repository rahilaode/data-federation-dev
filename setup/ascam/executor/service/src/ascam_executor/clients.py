"""
Klien yang dipakai Executor.

Endpoint (host, port, nama VDB) diambil dari Knowledge, sedangkan **rahasia** (kata sandi
ManagementRealm Teiid dan token agen) berasal dari Docker secret milik Executor sendiri.
Knowledge memang tidak pernah mengembalikan rahasia lewat API (ADR-0008).
"""
import base64
import time

import httpx

TIMEOUT = 60.0


class KnowledgeClient:
    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None):
        self.base = base_url.rstrip('/')
        self._client = client or httpx.Client(timeout=TIMEOUT)
        self._headers = {'Authorization': f'Bearer {token}'}

    def _request(self, method: str, path: str, **kwargs):
        response = self._client.request(method, f'{self.base}{path}', headers=self._headers, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}

    def obdf_id(self, name: str) -> int:
        for obdf in self._request('GET', '/api/v1/obdf'):
            if obdf['name'] == name:
                return obdf['id']
        raise RuntimeError(f'OBDF {name!r} tidak terdaftar')

    def approved_plans(self, obdf_id: int) -> list[dict]:
        return self._request('GET', f'/api/v1/obdf/{obdf_id}/plans',
                             params={'status': 'approved', 'limit': 20})

    def active_version(self, obdf_id: int) -> dict:
        versions = self._request('GET', f'/api/v1/obdf/{obdf_id}/versions', params={'limit': 50})
        for version in versions:
            if version['status'] == 'active':
                return version
        raise RuntimeError('tidak ada versi spesifikasi aktif')

    def artifact(self, version_id: int, kind: str) -> dict:
        return self._request('GET', f'/api/v1/versions/{version_id}/artifacts/{kind}')

    def type_mappings(self, obdf_id: int) -> list[dict]:
        return self._request('GET', f'/api/v1/obdf/{obdf_id}/type-mappings')

    def targets(self, obdf_id: int) -> list[dict]:
        return self._request('GET', f'/api/v1/obdf/{obdf_id}/targets')

    def start_execution(self, plan_id: int) -> dict:
        return self._request('POST', f'/api/v1/plans/{plan_id}/executions', json={})

    def add_step(self, execution_id: int, **step) -> dict:
        return self._request('POST', f'/api/v1/executions/{execution_id}/steps', json=step)

    def add_validation(self, execution_id: int, **validation) -> dict:
        return self._request('POST', f'/api/v1/executions/{execution_id}/validations', json=validation)

    def finish_execution(self, execution_id: int, **body) -> dict:
        return self._request('POST', f'/api/v1/executions/{execution_id}/finish', json=body)

    def supersede(self, plan_id: int, note: str) -> dict:
        return self._request('POST', f'/api/v1/plans/{plan_id}/supersede', json={'note': note})

    def sync(self, obdf_id: int) -> dict:
        return self._request('POST', f'/api/v1/obdf/{obdf_id}/sync')


class TeiidAdminClient:
    """Management API WildFly untuk penerapan blue-green versi VDB (ADR-0004)."""

    def __init__(self, endpoint: dict, username: str, password: str,
                 client: httpx.Client | None = None):
        scheme = 'https' if endpoint.get('tls') else 'http'
        path = endpoint.get('path') or '/management'
        self.url = f"{scheme}://{endpoint['host']}:{endpoint['port']}{path}"
        self._client = client or httpx.Client(timeout=TIMEOUT)
        self._auth = httpx.DigestAuth(username, password)

    def op(self, payload: dict) -> dict:
        response = self._client.post(self.url, json=payload, auth=self._auth)
        body = response.json()
        if body.get('outcome') != 'success':
            raise RuntimeError(f"operasi {payload.get('operation')} gagal: "
                               f"{str(body.get('failure-description'))[:300]}")
        return body.get('result')

    def deploy(self, deployment: str, content: bytes) -> None:
        upload = self._client.post(f'{self.url}/add-content', auth=self._auth,
                                   files={'file': (deployment, content)})
        upload.raise_for_status()
        digest = upload.json()['result']['BYTES_VALUE']
        self.op({'operation': 'add', 'address': [{'deployment': deployment}],
                 'content': [{'hash': {'BYTES_VALUE': digest}}], 'enabled': True})

    def undeploy(self, deployment: str) -> None:
        for operation in ('undeploy', 'remove'):
            try:
                self.op({'operation': operation, 'address': [{'deployment': deployment}]})
            except Exception:                       # noqa: BLE001 — pembersihan tetap lanjut
                pass

    def vdb_status(self, name: str, version: str) -> tuple[str | None, list[str]]:
        result = self.op({'operation': 'get-vdb', 'address': [{'subsystem': 'teiid'}],
                          'vdb-name': name, 'vdb-version': str(version)}) or {}
        errors = [f"[{m.get('model-name')}] {e.get('message')}"
                  for m in result.get('models', []) for e in (m.get('validity-errors') or [])
                  if e.get('severity') == 'ERROR']
        return result.get('status'), errors

    def wait_until_settled(self, name: str, version: str, timeout: float = 60.0,
                           sleep=time.sleep) -> tuple[str | None, list[str]]:
        batas = time.perf_counter() + timeout
        while time.perf_counter() < batas:
            status, errors = self.vdb_status(name, version)
            if status in ('ACTIVE', 'FAILED'):
                return status, errors
            sleep(0.2)
        return 'TIMEOUT', []

    def set_connection_type(self, name: str, version: str, connection_type: str) -> None:
        self.op({'operation': 'change-vdb-connection-type', 'address': [{'subsystem': 'teiid'}],
                 'vdb-name': name, 'vdb-version': str(version), 'connection-type': connection_type})


class AgentClient:
    """Agen pada host Ontop (ADR-0011, ADR-0018)."""

    def __init__(self, endpoint: dict, token: str, client: httpx.Client | None = None):
        scheme = 'https' if endpoint.get('tls') else 'http'
        self.base = f"{scheme}://{endpoint['host']}:{endpoint['port']}"
        self._client = client or httpx.Client(timeout=300.0)
        self._headers = {'Authorization': f'Bearer {token}'}

    def _request(self, method: str, path: str, **kwargs):
        response = self._client.request(method, f'{self.base}{path}', headers=self._headers, **kwargs)
        response.raise_for_status()
        return response.json()

    def artifact(self, kind: str) -> dict:
        return self._request('GET', f'/api/v1/artifacts/{kind}')

    def write(self, kind: str, content: str, expected_sha256: str | None) -> dict:
        return self._request('PUT', f'/api/v1/artifacts/{kind}',
                             json={'content': content, 'expected_sha256': expected_sha256})

    def restore(self, backup_id: str) -> dict:
        return self._request('POST', '/api/v1/artifacts/restore', json={'backup_id': backup_id})

    def validate(self, db_url: str | None = None) -> dict:
        return self._request('POST', '/api/v1/validate', json={'db_url': db_url})

    def reload(self) -> dict:
        return self._request('POST', '/api/v1/reload')

    def prune_backups(self, keep: int = 20) -> dict:
        return self._request('POST', '/api/v1/backups/prune', params={'keep': keep})


class SparqlClient:
    """Verifikasi jawaban OBDF setelah adaptasi."""

    FINGERPRINT = 'SELECT ?p (COUNT(*) AS ?n) WHERE { ?s ?p ?o } GROUP BY ?p ORDER BY ?p'

    def __init__(self, endpoint: dict, client: httpx.Client | None = None):
        scheme = 'https' if endpoint.get('tls') else 'http'
        path = endpoint.get('path') or '/sparql'
        self.url = f"{scheme}://{endpoint['host']}:{endpoint['port']}{path}"
        self._client = client or httpx.Client(timeout=TIMEOUT)

    def fingerprint(self) -> dict[str, int]:
        """{IRI predikat: jumlah triple} — sidik jari graf virtual."""
        response = self._client.get(self.url, params={'query': self.FINGERPRINT},
                                    headers={'Accept': 'application/sparql-results+json'})
        response.raise_for_status()
        hasil = {}
        for baris in response.json()['results']['bindings']:
            hasil[baris['p']['value']] = int(baris['n']['value'])
        return hasil


def decode_hash(value: str) -> str:
    return base64.b64decode(value).hex()


class KnowledgeCredentials:
    """Nama pengguna diambil dari Knowledge; kata sandinya dari Docker secret Executor."""

    def __init__(self, knowledge: KnowledgeClient, obdf_id: int):
        self._by_id = {c['id']: c for c in knowledge._request(
            'GET', f'/api/v1/obdf/{obdf_id}/credentials')}

    def username(self, credential_id: int | None, default: str) -> str:
        credential = self._by_id.get(credential_id)
        return credential['username'] if credential else default
