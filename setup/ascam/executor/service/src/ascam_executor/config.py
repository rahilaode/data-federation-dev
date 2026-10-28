"""Executor configuration: addresses from Knowledge, secrets from Docker secrets."""
import os
from dataclasses import dataclass


def read_secret(path: str, client: str | None = None) -> str:
    with open(path, encoding='utf-8') as fh:
        entries = [b.strip() for b in fh if b.strip() and not b.strip().startswith('#')]
    if client:
        for line in entries:
            if ':' in line and line.split(':', 1)[0] == client:
                return line.split(':', 1)[1]
        raise RuntimeError(f'token for client {client!r} not found in {path}')
    line = entries[0]
    return line.split(':', 1)[1] if ':' in line else line


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
    # 'bluegreen' (ADR-0022): the standby Ontop is verified before traffic is switched.
    # 'restart' (ADR-0001): a single Ontop is restarted in place, the design before ADR-0022.
    obda_strategy: str = os.getenv('ASCAM_EXEC_OBDA_STRATEGY', 'bluegreen')
    # Form of the view adjustment (ADR-0023): 'alter' (ALTER VIEW) or 'recreate' (DROP VIEW then
    # CREATE VIEW), chosen from the results of feasibility experiment F0.8 on Teiid 16.
    view_statement: str = os.getenv('ASCAM_EXEC_VIEW_STATEMENT', 'alter')

    def knowledge_token(self) -> str:
        if not self.token_file:
            raise RuntimeError('ASCAM_EXEC_TOKEN_FILE is not set')
        return read_secret(self.token_file, self.token_client)

    def teiid_password(self) -> str:
        if not self.teiid_password_file:
            raise RuntimeError('ASCAM_EXEC_TEIID_PASSWORD_FILE is not set')
        return read_secret(self.teiid_password_file)

    def agent_token(self) -> str:
        if not self.agent_token_file:
            raise RuntimeError('ASCAM_EXEC_AGENT_TOKEN_FILE is not set')
        return read_secret(self.agent_token_file)
