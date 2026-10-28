"""Console configuration. Secrets are read from Docker secrets, service addresses from the environment."""
import os
from dataclasses import dataclass, field


def read_secret(path: str | None, client: str | None = None) -> str | None:
    """Content of a secret file. With `client`, picks the line `<client>:<token>`; without `client`,
    returns the first line as it is (password, session key)."""
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as fh:
        lines = [b.strip() for b in fh if b.strip() and not b.strip().startswith('#')]
    if client is None:
        return lines[0] if lines else None
    for b in lines:
        name, _, token = b.partition(':')
        if name == client and token:
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
    knowledge_token: str | None = field(default_factory=lambda: read_secret(
        os.getenv('ASCAM_UI_TOKEN_FILE'), 'ui'))
    admin_password: str | None = field(default_factory=lambda: read_secret(
        os.getenv('ASCAM_UI_PASSWORD_FILE')))
    session_key: str | None = field(default_factory=lambda: read_secret(
        os.getenv('ASCAM_UI_SESSION_KEY_FILE')))
    session_max_age: int = int(os.getenv('ASCAM_UI_SESSION_MAX_AGE', str(8 * 3600)))
    secure_cookie: bool = os.getenv('ASCAM_UI_SECURE_COOKIE', 'false').lower() == 'true'
