"""
Service-to-service authentication with bearer tokens.

The token file (Docker secret) holds one client per line: `<client_name>:<token>`.
Tokens are compared in constant time (hmac.compare_digest) over their SHA-256 digests.
The actor recorded in the audit log is the client name, plus the UI user when the client
sends the header `X-ASCAM-User`.
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
                raise ValueError('Token format must be <client>:<token>')
            self._digests.append((hashlib.sha256(token.encode()).digest(), client))
        if not self._digests:
            raise ValueError('No API token')

    @classmethod
    def from_file(cls, path: str) -> 'TokenRegistry':
        with open(path, encoding='utf-8') as fh:
            return cls(fh.read().splitlines())

    def client_for(self, token: str) -> str | None:
        digest = hashlib.sha256(token.encode()).digest()
        found = None
        for known, client in self._digests:     # no early exit (constant time)
            if hmac.compare_digest(known, digest):
                found = client
        return found


USER_PATTERN = re.compile(r'[A-Za-z0-9._@-]{1,64}')


def current_actor(request: Request,
                  creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    registry: TokenRegistry = request.app.state.tokens
    client = registry.client_for(creds.credentials) if creds else None
    if client is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, 'Invalid token',
                            headers={'WWW-Authenticate': 'Bearer'})
    # Only the UI client may state the name of the acting administrator (for the audit trail
    # of HITL approvals); its format is restricted so that nothing can be injected into the log.

    user = request.headers.get('X-ASCAM-User')
    if user and client == 'ui' and USER_PATTERN.fullmatch(user):
        return f'{client}:{user}'
    return client
