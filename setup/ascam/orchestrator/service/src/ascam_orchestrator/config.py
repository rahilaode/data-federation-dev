"""Konfigurasi Orchestrator. Identitas OBDF, sumber, dan alamat Kafka diambil dari Knowledge."""
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
    # Titik awal grup konsumen BARU: 'earliest' membaca seluruh riwayat topik, 'latest' hanya
    # pesan sesudah Orchestrator tersambung. Setelah Knowledge direset, 'latest' mencegah DDL
    # lama diperlakukan sebagai perubahan baru.
    offset_reset: str = os.getenv('ASCAM_ORCH_OFFSET_RESET', 'earliest')
    bootstrap: str | None = os.getenv('ASCAM_ORCH_KAFKA_BOOTSTRAP')
    poll_timeout_ms: int = int(os.getenv('ASCAM_ORCH_POLL_TIMEOUT_MS', '2000'))
    retry_seconds: float = float(os.getenv('ASCAM_ORCH_RETRY_SECONDS', '5'))

    def __post_init__(self) -> None:
        if self.offset_reset not in ('earliest', 'latest'):
            raise ValueError(f'ASCAM_ORCH_OFFSET_RESET harus earliest atau latest, bukan {self.offset_reset!r}')

    def bearer(self) -> str:
        """Token untuk klien ini.

        Berkas token memuat beberapa klien (`<klien>:<token>` per baris), sehingga baris yang
        dipakai dipilih berdasarkan `token_client`; berkas berisi token polos juga diterima.
        """
        if self.token:
            return self.token
        if not self.token_file:
            raise RuntimeError('Token Knowledge belum diset (ASCAM_ORCH_TOKEN atau _TOKEN_FILE)')
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
        raise RuntimeError(f'token untuk klien {self.token_client!r} tidak ada di {self.token_file}')
