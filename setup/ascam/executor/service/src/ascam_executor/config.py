"""Konfigurasi Executor: alamat dari Knowledge, rahasia dari Docker secret."""
import os
from dataclasses import dataclass


def baca_rahasia(path: str, klien: str | None = None) -> str:
    with open(path, encoding='utf-8') as fh:
        isi = [b.strip() for b in fh if b.strip() and not b.strip().startswith('#')]
    if klien:
        for baris in isi:
            if ':' in baris and baris.split(':', 1)[0] == klien:
                return baris.split(':', 1)[1]
        raise RuntimeError(f'token untuk klien {klien!r} tidak ada di {path}')
    baris = isi[0]
    return baris.split(':', 1)[1] if ':' in baris else baris


@dataclass
class Settings:
    knowledge_url: str = os.getenv('ASCAM_EXEC_KNOWLEDGE_URL',
                                   'http://ascam-knowledge-service:8000')
    token_file: str | None = os.getenv('ASCAM_EXEC_TOKEN_FILE')
    token_client: str = os.getenv('ASCAM_EXEC_TOKEN_CLIENT', 'executor')
    obdf_name: str = os.getenv('ASCAM_EXEC_OBDF', 'bansos')
    teiid_password_file: str | None = os.getenv('ASCAM_EXEC_TEIID_PASSWORD_FILE')
    agent_token_file: str | None = os.getenv('ASCAM_EXEC_AGENT_TOKEN_FILE')
    teiid_jdbc_host: str = os.getenv('ASCAM_EXEC_TEIID_JDBC_HOST', 'teiid')
    teiid_jdbc_port: int = int(os.getenv('ASCAM_EXEC_TEIID_JDBC_PORT', '31000'))
    poll_seconds: float = float(os.getenv('ASCAM_EXEC_POLL_SECONDS', '5'))
    enabled: bool = os.getenv('ASCAM_EXEC_ENABLED', 'true').lower() == 'true'
    # 'bluegreen' (ADR-0022): Ontop siaga diverifikasi sebelum lalu lintas dialihkan.
    # 'restart' (ADR-0001): Ontop tunggal di-restart di tempat, rancangan sebelum ADR-0022.
    obda_strategy: str = os.getenv('ASCAM_EXEC_OBDA_STRATEGY', 'bluegreen')
    # Bentuk penyesuaian view (ADR-0023): 'alter' (ALTER VIEW) atau 'recreate' (DROP VIEW lalu
    # CREATE VIEW), dipilih berdasarkan hasil uji kelayakan F0.8 pada Teiid 16.
    view_statement: str = os.getenv('ASCAM_EXEC_VIEW_STATEMENT', 'alter')

    def knowledge_token(self) -> str:
        if not self.token_file:
            raise RuntimeError('ASCAM_EXEC_TOKEN_FILE belum diset')
        return baca_rahasia(self.token_file, self.token_client)

    def teiid_password(self) -> str:
        if not self.teiid_password_file:
            raise RuntimeError('ASCAM_EXEC_TEIID_PASSWORD_FILE belum diset')
        return baca_rahasia(self.teiid_password_file)

    def agent_token(self) -> str:
        if not self.agent_token_file:
            raise RuntimeError('ASCAM_EXEC_AGENT_TOKEN_FILE belum diset')
        return baca_rahasia(self.agent_token_file)
