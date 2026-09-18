"""API agen Ontop."""
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import artifacts, writer
from .config import AgentConfig, from_env
from .ontop_cli import OntopRunner
from .reloader import reload_ontop
from .security import TokenRegistry, current_client


class ArtifactOut(BaseModel):
    kind: str
    name: str
    media_type: str
    size: int
    sha256: str
    modified_at: datetime
    exists: bool


class ArtifactContentOut(ArtifactOut):
    content: str


class WriteIn(BaseModel):
    content: str
    expected_sha256: str | None = Field(
        default=None, description='sidik jari isi saat ini; menolak penulisan bila sudah berubah')


class WriteOut(BaseModel):
    kind: str
    sha256: str
    backup_id: str | None
    size: int


class BackupOut(BaseModel):
    backup_id: str
    kind: str
    created_at: datetime
    size: int
    sha256: str


class RestoreIn(BaseModel):
    backup_id: str


class ReloadOut(BaseModel):
    ok: bool
    stop_ms: int
    ready_ms: int
    total_ms: int
    error: str | None = None


class ValidateIn(BaseModel):
    db_url: str | None = Field(default=None,
                               description="JDBC URL pengganti, mis. '...;version=3' (ADR-0005)")


class CommandOut(BaseModel):
    ok: bool
    exit_code: int
    output: str
    duration_ms: int
    facts: dict[str, Any] = Field(default_factory=dict)


def create_app(cfg: AgentConfig | None = None, tokens: TokenRegistry | None = None,
               runner: OntopRunner | None = None, docker_client=None) -> FastAPI:
    app = FastAPI(title='ASCAM Ontop Agent', version='0.1.0')
    app.state.cfg = cfg or from_env()
    app.state.tokens = tokens or TokenRegistry.from_file(app.state.cfg.tokens_file)
    app.state.runner = runner or OntopRunner(app.state.cfg)
    app.state.docker = docker_client

    @app.get('/health', tags=['kesehatan'])
    def health():
        c = app.state.cfg
        return {'status': 'ok', 'ontop_container': c.ontop_container,
                'artifacts': {a.kind: a.exists for a in artifacts.listing(c)}}

    @app.get('/api/v1/artifacts', response_model=list[ArtifactOut], tags=['artefak'])
    def list_artifacts(_: str = Depends(current_client)):
        """Daftar artefak beserta sidik jarinya (untuk deteksi perubahan tanpa mengunduh isi)."""
        return [ArtifactOut(**a.__dict__) for a in artifacts.listing(app.state.cfg)]

    @app.get('/api/v1/artifacts/{kind}', response_model=ArtifactContentOut, tags=['artefak'])
    def get_artifact(kind: str, _: str = Depends(current_client)):
        try:
            meta, content = artifacts.read(app.state.cfg, kind)
        except KeyError:
            # berkas properti sengaja tidak diekspos: memuat kredensial JDBC
            raise HTTPException(404, f'artefak {kind} tidak diekspos agen')
        except FileNotFoundError:
            raise HTTPException(404, f'artefak {kind} tidak ditemukan di host Ontop')
        return ArtifactContentOut(**meta.__dict__, content=content)

    @app.put('/api/v1/artifacts/{kind}', response_model=WriteOut, tags=['artefak'])
    def put_artifact(kind: str, body: WriteIn, client: str = Depends(current_client)):
        """Menulis artefak secara atomik; isi lama disimpan sebagai cadangan."""
        try:
            sha, backup_id = writer.write(app.state.cfg, kind, body.content, body.expected_sha256)
        except KeyError:
            raise HTTPException(404, f'artefak {kind} tidak dikelola agen')
        except writer.Conflict as exc:
            raise HTTPException(409, str(exc))
        except OSError as exc:
            raise HTTPException(500, f'gagal menulis artefak: {exc}')
        return WriteOut(kind=kind, sha256=sha, backup_id=backup_id, size=len(body.content.encode()))

    @app.get('/api/v1/backups', response_model=list[BackupOut], tags=['artefak'])
    def list_backups(kind: str | None = None, _: str = Depends(current_client)):
        return [BackupOut(**b.__dict__) for b in writer.list_backups(app.state.cfg, kind)]

    @app.post('/api/v1/artifacts/restore', response_model=WriteOut, tags=['artefak'])
    def restore_artifact(body: RestoreIn, _: str = Depends(current_client)):
        """Mengembalikan artefak dari cadangan (dipakai Executor saat verifikasi gagal)."""
        try:
            kind, sha = writer.restore(app.state.cfg, body.backup_id)
        except FileNotFoundError:
            raise HTTPException(404, f'cadangan {body.backup_id} tidak ditemukan')
        size = app.state.cfg.path_of(kind).stat().st_size
        return WriteOut(kind=kind, sha256=sha, backup_id=body.backup_id, size=size)

    @app.post('/api/v1/reload', response_model=ReloadOut, tags=['operasi'])
    def reload(_: str = Depends(current_client)):
        """Memuat ulang Ontop dan menunggu endpoint SPARQL menjawab kembali (ADR-0001)."""
        result = reload_ontop(app.state.cfg, docker_client=app.state.docker)
        return ReloadOut(**result.__dict__)

    @app.post('/api/v1/validate', response_model=CommandOut, tags=['validasi'])
    def validate(body: ValidateIn | None = None, client: str = Depends(current_client)):
        """Menjalankan `ontop validate` terhadap artefak yang terpasang di host Ontop."""
        result = app.state.runner.validate(body.db_url if body else None)
        return CommandOut(**result.__dict__,
                          facts={'db_url': (body.db_url if body else None), 'client': client})

    return app
