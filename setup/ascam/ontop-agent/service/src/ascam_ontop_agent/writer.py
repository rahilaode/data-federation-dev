"""
Atomic writing of the OBDA artifacts, with backups and restore.

Principles:
  * atomic writes (a temporary file, then `os.replace`), so Ontop never reads a half-written
    file;
  * every write keeps a backup of the OLD content, so a restore is always possible (used by
    the Executor when verification fails, ADR-0004 step 6);
  * a write can be bound to the fingerprint of the current content (`expected_sha256`), so a
    change made by someone else is not overwritten silently.
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
    """The current content of the file differs from what the caller expects."""


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
    """Writes an artifact; returns (new sha256, backup_id of the old content)."""
    path = cfg.path_of(kind)
    backup_id = None
    if path.is_file():
        current = path.read_bytes()
        current_sha = _digest(current)
        if expected_sha256 and expected_sha256 != current_sha:
            raise Conflict(f'content of {kind} has changed (sha256 {current_sha[:12]}, '
                           f'expected {expected_sha256[:12]})')
        stamp = datetime.now(timezone.utc).strftime(STAMP)
        backup_id = f'{kind}-{stamp}-{current_sha[:12]}{path.suffix}'
        (backups_dir(cfg) / backup_id).write_bytes(current)
    elif expected_sha256:
        raise Conflict(f'{kind} does not exist yet, but the caller expects some content')

    data = content.encode('utf-8')
    temporary = path.with_name(f'.{path.name}.ascam-tmp')
    temporary.write_bytes(data)
    os.replace(temporary, path)             # atomic on the same filesystem
    return _digest(data), backup_id


def restore(cfg: AgentConfig, backup_id: str) -> tuple[str, str]:
    """Restores an artifact from a backup; returns (artifact kind, new sha256)."""
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
    """Keeps the `keep` newest backups per artifact kind."""
    removed = 0
    for kind in cfg.names:
        for backup in list_backups(cfg, kind)[keep:]:
            (backups_dir(cfg) / backup.backup_id).unlink(missing_ok=True)
            removed += 1
    return removed
