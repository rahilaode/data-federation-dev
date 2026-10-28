"""API of the Ontop agent."""
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
        default=None, description='fingerprint of the current content; the write is rejected if it has changed')


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
                               description="replacement JDBC URL, e.g. '...;version=3' (ADR-0005)")


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
        """The active Ontop instance if blue-green is configured; None means the older design."""
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

    @app.get('/health', tags=['health'])
    def health():
        c = app.state.cfg
        return {'status': 'ok', 'ontop_container': c.ontop_container,
                'artifacts': {a.kind: a.exists for a in artifacts.listing(c)}}

    @app.get('/api/v1/artifacts', response_model=list[ArtifactOut], tags=['artifacts'])
    def list_artifacts(_: str = Depends(current_client)):
        """Artifacts with their fingerprints (to detect changes without downloading the content)."""
        return [ArtifactOut(**a.__dict__) for a in artifacts.listing(app.state.cfg)]

    @app.get('/api/v1/artifacts/{kind}', response_model=ArtifactContentOut, tags=['artifacts'])
    def get_artifact(kind: str, _: str = Depends(current_client)):
        try:
            meta, content = artifacts.read(app.state.cfg, kind)
        except KeyError:
            # the properties file is deliberately not exposed: it holds the JDBC credentials
            raise HTTPException(404, f'artifact {kind} is not exposed by the agent')
        except FileNotFoundError:
            raise HTTPException(404, f'artifact {kind} not found on the Ontop host')
        return ArtifactContentOut(**meta.__dict__, content=content)

    @app.put('/api/v1/artifacts/{kind}', response_model=WriteOut, tags=['artifacts'])
    def put_artifact(kind: str, body: WriteIn, client: str = Depends(current_client)):
        """Writes an artifact atomically; the old content is kept as a backup."""
        try:
            sha, backup_id = writer.write(app.state.cfg, kind, body.content, body.expected_sha256)
        except KeyError:
            raise HTTPException(404, f'artifact {kind} is not managed by the agent')
        except writer.Conflict as exc:
            raise HTTPException(409, str(exc))
        except OSError as exc:
            raise HTTPException(500, f'writing the artifact failed: {exc}')
        return WriteOut(kind=kind, sha256=sha, backup_id=backup_id, size=len(body.content.encode()))

    @app.get('/api/v1/backups', response_model=list[BackupOut], tags=['artifacts'])
    def list_backups(kind: str | None = None, _: str = Depends(current_client)):
        return [BackupOut(**b.__dict__) for b in writer.list_backups(app.state.cfg, kind)]

    @app.post('/api/v1/artifacts/restore', response_model=WriteOut, tags=['artifacts'])
    def restore_artifact(body: RestoreIn, _: str = Depends(current_client)):
        """Restores an artifact from a backup (used by the Executor when verification fails)."""
        try:
            kind, sha = writer.restore(app.state.cfg, body.backup_id)
        except FileNotFoundError:
            raise HTTPException(404, f'backup {body.backup_id} not found')
        size = app.state.cfg.path_of(kind).stat().st_size
        return WriteOut(kind=kind, sha256=sha, backup_id=body.backup_id, size=size)

    @app.post('/api/v1/backups/prune', response_model=PruneOut, tags=['artifacts'])
    def prune_backups(keep: int = 20, _: str = Depends(current_client)):
        """Keeps a number of the newest backups per artifact kind."""
        keep = max(1, min(keep, 200))
        return PruneOut(removed=writer.prune(app.state.cfg, keep=keep), keep=keep)

    @app.post('/api/v1/reload', response_model=ReloadOut, tags=['operations'])
    def reload(_: str = Depends(current_client)):
        """Reloads Ontop and waits until the SPARQL endpoint answers again (ADR-0001)."""
        result = reload_ontop(app.state.cfg, docker_client=app.state.docker,
                              container_name=active_instance())
        return ReloadOut(**result.__dict__)

    @app.post('/api/v1/validate', response_model=CommandOut, tags=['validation'])
    def validate(body: ValidateIn | None = None, client: str = Depends(current_client)):
        """Runs `ontop validate` against the artifacts installed on the Ontop host."""
        result = app.state.runner.validate(body.db_url if body else None,
                                           container_name=active_instance())
        return CommandOut(**result.__dict__,
                          facts={'db_url': (body.db_url if body else None), 'client': client})

    # ── blue-green OBDA layer (ADR-0022) ───────────────────────────────────────
    @app.get('/api/v1/bluegreen', tags=['blue-green'])
    def bluegreen_state(_: str = Depends(current_client)):
        """Active and standby colours, the VDB version of each instance, and their direct SPARQL URLs."""
        return guarded(bg().describe)

    @app.post('/api/v1/bluegreen/prepare', response_model=StepOut, tags=['blue-green'])
    def bluegreen_prepare(body: PrepareIn, _: str = Depends(current_client)):
        """Writes ℳ′ and 𝒯′ to the standby slot, with the JDBC URL bound to the new VDB version."""
        result = guarded(bg().prepare, body.r2rml, body.ontology, body.vdb_version,
                         {'r2rml': body.expected_r2rml_sha256,
                          'ontology': body.expected_ontology_sha256})
        return StepOut(**as_dict(result))

    @app.post('/api/v1/bluegreen/validate', response_model=CommandOut, tags=['blue-green'])
    def bluegreen_validate(body: ValidateIn | None = None, client: str = Depends(current_client)):
        """`ontop validate` over the artifacts of the standby slot."""
        color, result = guarded(bg().validate, body.db_url if body else None)
        return CommandOut(**result.__dict__, facts={'color': color, 'client': client,
                                                    'db_url': body.db_url if body else None})

    @app.post('/api/v1/bluegreen/start', response_model=StepOut, tags=['blue-green'])
    def bluegreen_start(_: str = Depends(current_client)):
        """Starts the standby instance and waits until its SPARQL endpoint answers."""
        return StepOut(**as_dict(guarded(bg().start)))

    @app.post('/api/v1/bluegreen/switch', response_model=StepOut, tags=['blue-green'])
    def bluegreen_switch(_: str = Depends(current_client)):
        """The proxy is switched to the standby instance; its artifacts are promoted; the old instance is stopped."""
        return StepOut(**as_dict(guarded(bg().switch)))

    @app.post('/api/v1/bluegreen/discard', response_model=StepOut, tags=['blue-green'])
    def bluegreen_discard(_: str = Depends(current_client)):
        """Stops the standby instance without switching traffic."""
        return StepOut(**as_dict(guarded(bg().discard)))

    @app.post('/api/v1/bluegreen/reset', response_model=StepOut, tags=['blue-green'])
    def bluegreen_reset(body: ResetIn | None = None, _: str = Depends(current_client)):
        """The active instance is rebuilt from the canonical artifacts (used by the evaluation harness)."""
        return StepOut(**as_dict(guarded(bg().reset, (body or ResetIn()).vdb_version)))

    return app
