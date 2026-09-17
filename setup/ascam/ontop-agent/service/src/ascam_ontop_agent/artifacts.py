"""Pembacaan artefak OBDA (ℳ dan 𝒯) beserta sidik jarinya."""
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import MEDIA_TYPES, AgentConfig


@dataclass
class ArtifactInfo:
    kind: str
    name: str
    media_type: str
    size: int
    sha256: str
    modified_at: datetime
    exists: bool = True


def info(cfg: AgentConfig, kind: str) -> ArtifactInfo:
    path = cfg.path_of(kind)
    if not path.is_file():
        return ArtifactInfo(kind, cfg.names[kind], MEDIA_TYPES[kind], 0, '', 
                            datetime.fromtimestamp(0, timezone.utc), exists=False)
    data = path.read_bytes()
    return ArtifactInfo(kind=kind, name=cfg.names[kind], media_type=MEDIA_TYPES[kind],
                        size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                        modified_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc))


def listing(cfg: AgentConfig) -> list[ArtifactInfo]:
    return [info(cfg, kind) for kind in cfg.names]


def read(cfg: AgentConfig, kind: str) -> tuple[ArtifactInfo, str]:
    meta = info(cfg, kind)
    if not meta.exists:
        raise FileNotFoundError(cfg.path_of(kind))
    return meta, cfg.path_of(kind).read_text(encoding='utf-8')
