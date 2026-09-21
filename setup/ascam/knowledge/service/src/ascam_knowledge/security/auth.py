"""
Autentikasi antarlayanan dengan bearer token.

Berkas token (Docker secret) berisi satu klien per baris: `<nama_klien>:<token>`.
Token dibandingkan dengan waktu konstan (hmac.compare_digest) atas digest SHA-256.
Aktor yang tercatat di audit = nama klien, ditambah pengguna UI bila klien mengirim
header `X-ASCAM-User`.
"""
import hashlib
import re
import hmac

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


class TokenRegistry:
    def __init__(self, lines: list[str]):
        self._digests: list[tuple[bytes, str]] = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            client, _, token = line.partition(':')
            if not client or not token:
                raise ValueError('Format token harus <klien>:<token>')
            self._digests.append((hashlib.sha256(token.encode()).digest(), client))
        if not self._digests:
            raise ValueError('Tidak ada token API')

    @classmethod
    def from_file(cls, path: str) -> 'TokenRegistry':
        with open(path, encoding='utf-8') as fh:
            return cls(fh.read().splitlines())

    def client_for(self, token: str) -> str | None:
        digest = hashlib.sha256(token.encode()).digest()
        found = None
        for known, client in self._digests:     # tanpa keluar lebih awal (waktu konstan)
            if hmac.compare_digest(known, digest):
                found = client
        return found


USER_PATTERN = re.compile(r'[A-Za-z0-9._@-]{1,64}')


def current_actor(request: Request,
                  creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    registry: TokenRegistry = request.app.state.tokens
    client = registry.client_for(creds.credentials) if creds else None
    if client is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'Token tidak valid',
                            headers={'WWW-Authenticate': 'Bearer'})
    # Hanya klien UI yang boleh menyatakan nama administrator yang bertindak (untuk jejak audit
    # persetujuan HITL); formatnya dibatasi agar tidak dapat menyusupkan isi ke log.
    user = request.headers.get('X-ASCAM-User')
    if user and client == 'ui' and USER_PATTERN.fullmatch(user):
        return f'{client}:{user}'
    return client
