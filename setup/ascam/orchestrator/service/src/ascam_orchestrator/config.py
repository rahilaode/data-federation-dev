"""Orchestrator configuration. The OBDF identity, the sources, and the Kafka address come from Knowledge."""
import os
from dataclasses import dataclass


@dataclass
class Settings:
    knowledge_url: str = os.getenv('ASCAM_ORCH_KNOWLEDGE_URL', 'http://ascam-knowledge-service:8000')
    token_file: str | None = os.getenv('ASCAM_ORCH_TOKEN_FILE')
    token: str | None = os.getenv('ASCAM_ORCH_TOKEN')
    token_client: str = os.getenv('ASCAM_ORCH_TOKEN_CLIENT', 'orchestrator')
    obdf_name: str = os.getenv('ASCAM_ORCH_OBDF', 'bansos')
    group_id: str = os.getenv('ASCAM_ORCH_GROUP_ID', 'ascam-orchestrator')
    # Starting point of a NEW consumer group: 'earliest' reads the whole topic history, 'latest'
    # only messages after the Orchestrator connects. After Knowledge is reset, 'latest' prevents
    # old DDL from being treated as new changes.
    offset_reset: str = os.getenv('ASCAM_ORCH_OFFSET_RESET', 'earliest')
    bootstrap: str | None = os.getenv('ASCAM_ORCH_KAFKA_BOOTSTRAP')
    poll_timeout_ms: int = int(os.getenv('ASCAM_ORCH_POLL_TIMEOUT_MS', '2000'))
    retry_seconds: float = float(os.getenv('ASCAM_ORCH_RETRY_SECONDS', '5'))

    def __post_init__(self) -> None:
        if self.offset_reset not in ('earliest', 'latest'):
            raise ValueError(f'ASCAM_ORCH_OFFSET_RESET must be earliest or latest, not {self.offset_reset!r}')

    def bearer(self) -> str:
        """Token for this client.

        The token file holds several clients (`<client>:<token>` per line), so the line is
        chosen by `token_client`; a file holding a plain token is accepted too.
        """
        if self.token:
            return self.token
        if not self.token_file:
            raise RuntimeError('Knowledge token is not set (ASCAM_ORCH_TOKEN or _TOKEN_FILE)')
        fallback = None
        with open(self.token_file, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if ':' not in line:
                    fallback = fallback or line
                    continue
                client, token = line.split(':', 1)
                if client == self.token_client:
                    return token
        if fallback:
            return fallback
        raise RuntimeError(f'token for client {self.token_client!r} not found in {self.token_file}')
