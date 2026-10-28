"""
Connection settings of the Knowledge database.

Sources, in order:
1. ASCAM_KNOWLEDGE_DB_URL (complete; used for local tests);
2. separate parts: ASCAM_KNOWLEDGE_DB_HOST, _PORT, _NAME, _USER, and the password from the
   file ASCAM_KNOWLEDGE_DB_PASSWORD_FILE (Docker secret), so the password is never in an
   environment variable or in the repository (D4).
"""
import os

from sqlalchemy.engine import URL, make_url


def database_url() -> URL:
    direct = os.getenv('ASCAM_KNOWLEDGE_DB_URL')
    if direct:
        return make_url(direct)
    password_file = os.getenv('ASCAM_KNOWLEDGE_DB_PASSWORD_FILE')
    if not password_file:
        raise RuntimeError('Set ASCAM_KNOWLEDGE_DB_URL or ASCAM_KNOWLEDGE_DB_PASSWORD_FILE'
)
    with open(password_file, encoding='utf-8') as fh:
        password = fh.read().strip()
    return URL.create(
        'postgresql+psycopg',
        username=os.getenv('ASCAM_KNOWLEDGE_DB_USER', 'ascam_owner'),
        password=password,
        host=os.getenv('ASCAM_KNOWLEDGE_DB_HOST', 'ascam-knowledge-db'),
        port=int(os.getenv('ASCAM_KNOWLEDGE_DB_PORT', '5432')),
        database=os.getenv('ASCAM_KNOWLEDGE_DB_NAME', 'ascam_knowledge'),
    )


def read_secret_file(path: str) -> str:
    with open(path, encoding='utf-8') as fh:
        return fh.read().strip()


def env_path(name: str) -> str | None:
    value = os.getenv(name)
    return value or None
