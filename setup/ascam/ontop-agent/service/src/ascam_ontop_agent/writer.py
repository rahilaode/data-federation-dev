"""
Penulisan artefak OBDA secara atomik, beserta cadangan dan pemulihannya.

Prinsip:
  * penulisan atomik (berkas sementara lalu `os.replace`), sehingga Ontop tidak pernah
    membaca berkas setengah tertulis;
  * setiap penulisan menyimpan cadangan isi LAMA, sehingga pemulihan selalu mungkin
    (dipakai Executor saat verifikasi gagal, ADR-0004 langkah 6);
  * penulisan dapat dikunci pada sidik jari isi saat ini (`expected_sha256`), sehingga
    perubahan yang dilakukan pihak lain tidak tertimpa diam-diam.
"""
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import AgentConfig

BACKUP_DIR = 'ascam-backups'
STAMP = '%Y%m%dT%H%M%S%f'
NAME_PATTERN = re.compile(r'^(?P<kind>[a-z0-9_]+)-(?P<stamp>\d{8}T\d{6}\d{6})-(?P<sha>[0-9a-f]{12})$')


class Conflict(Exception):
    """Isi berkas saat ini berbeda dari yang diharapkan pemanggil."""


@dataclass
class BackupInfo:
    backup_id: str
    kind: str
    created_at: datetime
    size: int
    sha256: str


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def backups_dir(cfg: AgentConfig) -> Path:
    path = cfg.artifacts_dir / BACKUP_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_backups(cfg: AgentConfig, kind: str | None = None) -> list[BackupInfo]:
    out = []
    for path in sorted(backups_dir(cfg).glob('*')):
        match = NAME_PATTERN.match(path.stem)
        if not match or (kind and match.group('kind') != kind):
            continue
        data = path.read_bytes()
        out.append(BackupInfo(backup_id=path.name, kind=match.group('kind'),
                              created_at=datetime.strptime(match.group('stamp'), STAMP).replace(
                                  tzinfo=timezone.utc),
                              size=len(data), sha256=_digest(data)))
    return sorted(out, key=lambda b: b.created_at, reverse=True)


def write(cfg: AgentConfig, kind: str, content: str,
          expected_sha256: str | None = None) -> tuple[str, str | None]:
    """Menulis artefak; mengembalikan (sha256 baru, backup_id isi lama)."""
    path = cfg.path_of(kind)
    backup_id = None
    if path.is_file():
        current = path.read_bytes()
        current_sha = _digest(current)
        if expected_sha256 and expected_sha256 != current_sha:
            raise Conflict(f'isi {kind} sudah berubah (sha256 {current_sha[:12]}, '
                           f'diharapkan {expected_sha256[:12]})')
        stamp = datetime.now(timezone.utc).strftime(STAMP)
        backup_id = f'{kind}-{stamp}-{current_sha[:12]}{path.suffix}'
        (backups_dir(cfg) / backup_id).write_bytes(current)
    elif expected_sha256:
        raise Conflict(f'{kind} belum ada, tetapi pemanggil mengharapkan isi tertentu')

    data = content.encode('utf-8')
    temporary = path.with_name(f'.{path.name}.ascam-tmp')
    temporary.write_bytes(data)
    os.replace(temporary, path)             # atomik pada filesystem yang sama
    return _digest(data), backup_id


def restore(cfg: AgentConfig, backup_id: str) -> tuple[str, str]:
    """Mengembalikan artefak dari cadangan; mengembalikan (jenis artefak, sha256 baru)."""
    candidate = (backups_dir(cfg) / backup_id).resolve()
    if candidate.parent != backups_dir(cfg).resolve() or not candidate.is_file():
        raise FileNotFoundError(backup_id)
    match = NAME_PATTERN.match(candidate.stem)
    if not match:
        raise FileNotFoundError(backup_id)
    kind = match.group('kind')
    if kind not in cfg.names:
        raise FileNotFoundError(backup_id)
    sha, _ = write(cfg, kind, candidate.read_text(encoding='utf-8'))
    return kind, sha


def prune(cfg: AgentConfig, keep: int = 20) -> int:
    """Menyisakan `keep` cadangan terbaru per jenis artefak."""
    removed = 0
    for kind in cfg.names:
        for backup in list_backups(cfg, kind)[keep:]:
            (backups_dir(cfg) / backup.backup_id).unlink(missing_ok=True)
            removed += 1
    return removed
