"""API agen Ontop."""
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import artifacts, writer
from .bluegreen import BlueGreen, BlueGreenError, as_dict
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


class PruneOut(BaseModel):
    removed: int
    keep: int


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


class PrepareIn(BaseModel):
    r2rml: str
    ontology: str
    vdb_version: str
    expected_r2rml_sha256: str | None = None
    expected_ontology_sha256: str | None = None


class ResetIn(BaseModel):
    vdb_version: str = '1'


class StepOut(BaseModel):
    ok: bool
    color: str
    duration_ms: int
    error: str | None = None
    detail: dict[str, Any] | None = None


class CommandOut(BaseModel):
    ok: bool
    exit_code: int
    output: str
    duration_ms: int
    facts: dict[str, Any] = Field(default_factory=dict)


def create_app(cfg: AgentConfig | None = None, tokens: TokenRegistry | None = None,
               runner: OntopRunner | None = None, docker_client=None,
               bluegreen: BlueGreen | None = None) -> FastAPI:
    app = FastAPI(title='ASCAM Ontop Agent', version='0.1.0')
    app.state.cfg = cfg or from_env()
    app.state.tokens = tokens or TokenRegistry.from_file(app.state.cfg.tokens_file)
    app.state.runner = runner or OntopRunner(app.state.cfg)
    app.state.docker = docker_client
    app.state.bluegreen = bluegreen

    def bg() -> BlueGreen:
        if app.state.bluegreen is None:
            try:
                app.state.bluegreen = BlueGreen(app.state.cfg, runner=app.state.runner,
                                                docker_client=app.state.docker)
            except BlueGreenError as exc:
                raise HTTPException(409, str(exc))
        return app.state.bluegreen

    def active_instance() -> str | None:
        """Instance Ontop aktif bila blue-green dikonfigurasi; None berarti rancangan lama."""
        if app.state.cfg.slots_dir is None:
            return None
        return guarded(lambda: bg().container(bg().state()['active']))

    def guarded(func, *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except BlueGreenError as exc:
            raise HTTPException(409, str(exc))
        except writer.Conflict as exc:
            raise HTTPException(409, str(exc))

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

    @app.post('/api/v1/backups/prune', response_model=PruneOut, tags=['artefak'])
    def prune_backups(keep: int = 20, _: str = Depends(current_client)):
        """Menyisakan sejumlah cadangan terbaru per jenis artefak."""
        keep = max(1, min(keep, 200))
        return PruneOut(removed=writer.prune(app.state.cfg, keep=keep), keep=keep)

    @app.post('/api/v1/reload', response_model=ReloadOut, tags=['operasi'])
    def reload(_: str = Depends(current_client)):
        """Memuat ulang Ontop dan menunggu endpoint SPARQL menjawab kembali (ADR-0001)."""
        result = reload_ontop(app.state.cfg, docker_client=app.state.docker,
                              container_name=active_instance())
        return ReloadOut(**result.__dict__)

    @app.post('/api/v1/validate', response_model=CommandOut, tags=['validasi'])
    def validate(body: ValidateIn | None = None, client: str = Depends(current_client)):
        """Menjalankan `ontop validate` terhadap artefak yang terpasang di host Ontop."""
        result = app.state.runner.validate(body.db_url if body else None,
                                           container_name=active_instance())
        return CommandOut(**result.__dict__,
                          facts={'db_url': (body.db_url if body else None), 'client': client})

    # ── blue-green lapisan OBDA (ADR-0022) ─────────────────────────────────────
    @app.get('/api/v1/bluegreen', tags=['blue-green'])
    def bluegreen_state(_: str = Depends(current_client)):
        """Warna aktif dan siaga, versi VDB tiap instance, dan URL SPARQL langsungnya."""
        return guarded(bg().describe)

    @app.post('/api/v1/bluegreen/prepare', response_model=StepOut, tags=['blue-green'])
    def bluegreen_prepare(body: PrepareIn, _: str = Depends(current_client)):
        """Menulis ℳ′ dan 𝒯′ ke slot siaga, URL JDBC dikunci ke versi VDB baru."""
        result = guarded(bg().prepare, body.r2rml, body.ontology, body.vdb_version,
                         {'r2rml': body.expected_r2rml_sha256,
                          'ontology': body.expected_ontology_sha256})
        return StepOut(**as_dict(result))

    @app.post('/api/v1/bluegreen/validate', response_model=CommandOut, tags=['blue-green'])
    def bluegreen_validate(body: ValidateIn | None = None, client: str = Depends(current_client)):
        """`ontop validate` atas artefak slot siaga."""
        color, result = guarded(bg().validate, body.db_url if body else None)
        return CommandOut(**result.__dict__, facts={'color': color, 'client': client,
                                                    'db_url': body.db_url if body else None})

    @app.post('/api/v1/bluegreen/start', response_model=StepOut, tags=['blue-green'])
    def bluegreen_start(_: str = Depends(current_client)):
        """Menyalakan instance siaga dan menunggu endpoint SPARQL-nya menjawab."""
        return StepOut(**as_dict(guarded(bg().start)))

    @app.post('/api/v1/bluegreen/switch', response_model=StepOut, tags=['blue-green'])
    def bluegreen_switch(_: str = Depends(current_client)):
        """Proxy dialihkan ke instance siaga; artefaknya dipromosikan; instance lama dihentikan."""
        return StepOut(**as_dict(guarded(bg().switch)))

    @app.post('/api/v1/bluegreen/discard', response_model=StepOut, tags=['blue-green'])
    def bluegreen_discard(_: str = Depends(current_client)):
        """Menghentikan instance siaga tanpa mengalihkan lalu lintas."""
        return StepOut(**as_dict(guarded(bg().discard)))

    @app.post('/api/v1/bluegreen/reset', response_model=StepOut, tags=['blue-green'])
    def bluegreen_reset(body: ResetIn | None = None, _: str = Depends(current_client)):
        """Instance aktif dibangun ulang dari artefak kanonik (dipakai harness evaluasi)."""
        return StepOut(**as_dict(guarded(bg().reset, (body or ResetIn()).vdb_version)))

    return app
