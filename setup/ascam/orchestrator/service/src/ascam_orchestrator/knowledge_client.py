"""Klien Knowledge Service (satu-satunya jalur Orchestrator ke pengetahuan sistem)."""
import httpx


class KnowledgeClient:
    def __init__(self, base_url: str, token: str, client: httpx.Client | None = None,
                 timeout: float = 30.0):
        self.base = base_url.rstrip('/')
        self._client = client or httpx.Client(timeout=timeout)
        self._headers = {'Authorization': f'Bearer {token}'}

    def _get(self, path: str):
        r = self._client.get(f'{self.base}{path}', headers=self._headers)
        r.raise_for_status()
        return r.json()

    def obdf_id(self, name: str) -> int:
        for obdf in self._get('/api/v1/obdf'):
            if obdf['name'] == name:
                return obdf['id']
        raise RuntimeError(f'OBDF {name!r} belum terdaftar di Knowledge')

    def sources(self, obdf_id: int) -> list[dict]:
        return self._get(f'/api/v1/obdf/{obdf_id}/sources')

    def kafka_bootstrap(self, obdf_id: int) -> str:
        for target in self._get(f'/api/v1/obdf/{obdf_id}/targets'):
            if target['kind'] == 'kafka' and target['enabled']:
                endpoint = target['endpoint']
                return f"{endpoint['host']}:{endpoint['port']}"
        raise RuntimeError('target Kafka belum terdaftar atau nonaktif')

    def post_event(self, obdf_id: int, payload: dict) -> dict:
        r = self._client.post(f'{self.base}/api/v1/obdf/{obdf_id}/events',
                              json=payload, headers=self._headers)
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._client.close()
