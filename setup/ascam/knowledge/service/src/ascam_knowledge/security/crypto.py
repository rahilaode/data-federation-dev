"""
Enkripsi kredensial di sisi aplikasi (D4).

Kunci Fernet (AES-128-CBC + HMAC-SHA256) dibaca dari berkas rahasia, satu kunci per
baris, **kunci terbaru di baris pertama**. Dekripsi mencoba semua kunci (MultiFernet),
sehingga rotasi kunci dapat dilakukan tanpa downtime: tambahkan kunci baru di baris
pertama, jalankan rotasi ulang (re-encrypt), lalu hapus kunci lama.

`key_version` = jumlah kunci pada saat enkripsi (kunci tertua = 1).
"""
from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecretBox:
    def __init__(self, keys: list[str]):
        keys = [k.strip() for k in keys if k.strip() and not k.strip().startswith('#')]
        if not keys:
            raise ValueError('Tidak ada kunci enkripsi')
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
            raise ValueError('Kredensial tidak dapat didekripsi dengan kunci yang tersedia') from exc


def generate_key() -> str:
    return Fernet.generate_key().decode()
