"""Autentikasi bearer token (berkas `<klien>:<token>`, satu klien per baris)."""
import hashlib
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
        for known, client in self._digests:
            if hmac.compare_digest(known, digest):
                found = client
        return found


def current_client(request: Request,
                   creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    registry: TokenRegistry = request.app.state.tokens
    client = registry.client_for(creds.credentials) if creds else None
    if client is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'Token tidak valid',
                            headers={'WWW-Authenticate': 'Bearer'})
    return client
