"""
Klien pengambilan spesifikasi OBDF (ADR-0003, ADR-0006, ADR-0009, ADR-0011).

Tiga sumber:
  TeiidAdminClient    — WildFly HTTP management API (status VDB, model/sumber, isi berkas VDB)
  TeiidMetadataClient — transport ODBC Teiid (SYS.*, SYSADMIN.*): Σ_S efektif
  OntopAgentClient    — agen di host Ontop (artefak ℳ dan 𝒯 beserta SHA-256)

Nama kolom tabel sistem ditanyakan lebih dahulu dan identifier selalu dikutip (pelajaran F0.3).
"""
import base64
import hashlib
from dataclasses import dataclass

import httpx
import psycopg

TIMEOUT = 30.0


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class TeiidAdminClient:
    def __init__(self, endpoint: dict, username: str, password: str, client: httpx.Client | None = None):
        scheme = 'https' if endpoint.get('tls') else 'http'
        path = endpoint.get('path') or '/management'
        self.url = f"{scheme}://{endpoint['host']}:{endpoint['port']}{path}"
        self._client = client
        self._auth = httpx.DigestAuth(username, password)

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=TIMEOUT)
        return self._client

    def op(self, payload: dict, **kwargs) -> dict:
        r = self.client.post(self.url, json=payload, auth=self._auth, **kwargs)
        body = r.json()
        if body.get('outcome') != 'success':
            raise RuntimeError(f"operasi {payload.get('operation')} gagal: "
                               f"{str(body.get('failure-description'))[:300]}")
        return body.get('result')

    def get_vdb(self, name: str, version: str) -> dict:
        return self.op({'operation': 'get-vdb', 'address': [{'subsystem': 'teiid'}],
                        'vdb-name': name, 'vdb-version': str(version)})

    def get_schema(self, name: str, version: str, model: str) -> str:
        return self.op({'operation': 'get-schema', 'address': [{'subsystem': 'teiid'}],
                        'vdb-name': name, 'vdb-version': str(version), 'model-name': model})

    def deployment_hash(self, deployment: str) -> str | None:
        """SHA-1 konten deployment yang dilaporkan WildFly (hex), untuk verifikasi keutuhan."""
        result = self.op({'operation': 'read-resource', 'address': [{'deployment': deployment}]})
        content = (result or {}).get('content') or []
        value = content[0].get('hash', {}).get('BYTES_VALUE') if content else None
        return base64.b64decode(value).hex() if value else None

    def read_deployment_content(self, deployment: str) -> bytes:
        """Isi berkas deployment lewat attached stream (ADR-0009)."""
        r = self.client.post(self.url, json={'operation': 'read-content',
                                             'address': [{'deployment': deployment}]},
                             auth=self._auth, params={'useStreamAsResponse': ''})
        r.raise_for_status()
        return r.content


class TeiidMetadataClient:
    SYSTEM_SCHEMAS = ("'SYS'", "'SYSADMIN'", "'pg_catalog'")

    def __init__(self, endpoint: dict, username: str, password: str, connect=None):
        self.params = dict(host=endpoint['host'], port=endpoint['port'],
                           dbname=endpoint.get('options', {}).get('vdb'),
                           user=username, password=password,
                           sslmode='require' if endpoint.get('tls') else 'disable',
                           gssencmode='disable', connect_timeout=int(TIMEOUT))
        self._connect = connect or psycopg.connect

    def fetch_all(self) -> dict[str, list[dict]]:
        """Satu koneksi, satu snapshot konsisten dari tabel sistem."""
        out: dict[str, list[dict]] = {}
        with self._connect(**self.params) as conn, conn.cursor() as cur:
            def columns_of(schema: str, table: str) -> list[str]:
                cur.execute('SELECT "Name" FROM SYS.Columns WHERE "SchemaName" = %s '
                            'AND "TableName" = %s ORDER BY "Position"', (schema, table))
                return [r[0] for r in cur.fetchall()]

            def read(schema: str, table: str, where: str = '') -> list[dict]:
                cols = columns_of(schema, table)
                if not cols:
                    return []
                cur.execute(f'SELECT {", ".join(quote_ident(c) for c in cols)} '
                            f'FROM {schema}.{quote_ident(table)} {where}')
                return [dict(zip(cols, row)) for row in cur.fetchall()]

            not_system = f'WHERE "SchemaName" NOT IN ({", ".join(self.SYSTEM_SCHEMAS)})'
            out['virtual_databases'] = read('SYS', 'VirtualDatabases')
            out['schemas'] = read('SYS', 'Schemas',
                                  f'WHERE "Name" NOT IN ({", ".join(self.SYSTEM_SCHEMAS)})')
            out['tables'] = read('SYS', 'Tables', not_system)
            out['columns'] = read('SYS', 'Columns', not_system)
            out['keys'] = read('SYS', 'KeyColumns', not_system)
            out['views'] = read('SYSADMIN', 'Views', not_system)
            out['usage'] = read('SYSADMIN', 'Usage')
            out['matviews'] = read('SYSADMIN', 'MatViews')
            out['procedures'] = read('SYSADMIN', 'StoredProcedures')
            out['triggers'] = read('SYSADMIN', 'Triggers')
        return out


@dataclass
class AgentArtifact:
    kind: str
    name: str
    media_type: str
    content: str
    sha256: str


class OntopAgentClient:
    def __init__(self, endpoint: dict, token: str, client: httpx.Client | None = None):
        scheme = 'https' if endpoint.get('tls') else 'http'
        self.base = f"{scheme}://{endpoint['host']}:{endpoint['port']}"
        self.headers = {'Authorization': f'Bearer {token}'}
        self._client = client

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=TIMEOUT)
        return self._client

    def artifacts(self) -> list[AgentArtifact]:
        listing = self.client.get(f'{self.base}/api/v1/artifacts', headers=self.headers)
        listing.raise_for_status()
        out = []
        for item in listing.json():
            if not item.get('exists'):
                continue
            r = self.client.get(f"{self.base}/api/v1/artifacts/{item['kind']}", headers=self.headers)
            r.raise_for_status()
            body = r.json()
            digest = hashlib.sha256(body['content'].encode()).hexdigest()
            if digest != body['sha256']:
                raise RuntimeError(f"sidik jari artefak {item['kind']} tidak cocok dengan isinya")
            out.append(AgentArtifact(kind=body['kind'], name=body['name'],
                                     media_type=body['media_type'], content=body['content'],
                                     sha256=body['sha256']))
        return out
