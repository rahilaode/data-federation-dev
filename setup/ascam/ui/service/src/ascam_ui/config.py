"""Konfigurasi konsol. Rahasia dibaca dari Docker secret, alamat layanan dari lingkungan."""
import os
from dataclasses import dataclass, field


def baca(path: str | None, klien: str | None = None) -> str | None:
    """Isi berkas rahasia. Dengan `klien`, memilih baris `<klien>:<token>`; tanpa `klien`,
    mengembalikan baris pertama apa adanya (kata sandi, kunci sesi)."""
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as fh:
        baris = [b.strip() for b in fh if b.strip() and not b.strip().startswith('#')]
    if klien is None:
        return baris[0] if baris else None
    for b in baris:
        nama, _, token = b.partition(':')
        if nama == klien and token:
            return token
    return None


@dataclass
class Settings:
    knowledge_url: str = os.getenv('ASCAM_UI_KNOWLEDGE_URL', 'http://ascam-knowledge-service:8000')
    orchestrator_url: str = os.getenv('ASCAM_UI_ORCHESTRATOR_URL', 'http://ascam-orchestrator:8000')
    executor_url: str = os.getenv('ASCAM_UI_EXECUTOR_URL', 'http://ascam-executor:8000')
    agent_url: str = os.getenv('ASCAM_UI_AGENT_URL', 'http://ascam-ontop-agent:8000')
    obdf_name: str = os.getenv('ASCAM_UI_OBDF', 'bansos')
    admin_user: str = os.getenv('ASCAM_UI_ADMIN_USER', 'admin')
    knowledge_token: str | None = field(default_factory=lambda: baca(
        os.getenv('ASCAM_UI_TOKEN_FILE'), 'ui'))
    admin_password: str | None = field(default_factory=lambda: baca(
        os.getenv('ASCAM_UI_PASSWORD_FILE')))
    session_key: str | None = field(default_factory=lambda: baca(
        os.getenv('ASCAM_UI_SESSION_KEY_FILE')))
    session_max_age: int = int(os.getenv('ASCAM_UI_SESSION_MAX_AGE', str(8 * 3600)))
    secure_cookie: bool = os.getenv('ASCAM_UI_SECURE_COOKIE', 'false').lower() == 'true'
