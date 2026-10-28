"""
Application-side encryption of credentials (D4).

Fernet keys (AES-128-CBC + HMAC-SHA256) are read from a secret file, one key per line,
**newest key on the first line**. Decryption tries every key (MultiFernet), so keys can be
rotated without downtime: add the new key on the first line, re-encrypt, then remove the
old key.

`key_version` = number of keys at encryption time (oldest key = 1).
"""
from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecretBox:
    def __init__(self, keys: list[str]):
        keys = [k.strip() for k in keys if k.strip() and not k.strip().startswith('#')]
        if not keys:
            raise ValueError('No encryption key')
        self._fernets = [Fernet(k.encode()) for k in keys]
        self._multi = MultiFernet(self._fernets)
        self.current_version = len(keys)

    @classmethod
    def from_file(cls, path: str) -> 'SecretBox':
        with open(path, encoding='utf-8') as fh:
            return cls(fh.read().splitlines())

    def encrypt(self, plaintext: str) -> tuple[bytes, int]:
        return self._fernets[0].encrypt(plaintext.encode()), self.current_version

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._multi.decrypt(bytes(ciphertext)).decode()
        except InvalidToken as exc:
            raise ValueError('The credential cannot be decrypted with the available keys'
) from exc


def generate_key() -> str:
    return Fernet.generate_key().decode()
