"""API agen Ontop."""
from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import artifacts
from .config import AgentConfig, from_env
from .ontop_cli import OntopRunner
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
               runner: OntopRunner | None = None) -> FastAPI:
    app = FastAPI(title='ASCAM Ontop Agent', version='0.1.0')
    app.state.cfg = cfg or from_env()
    app.state.tokens = tokens or TokenRegistry.from_file(app.state.cfg.tokens_file)
    app.state.runner = runner or OntopRunner(app.state.cfg)

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

    @app.post('/api/v1/validate', response_model=CommandOut, tags=['validasi'])
    def validate(body: ValidateIn | None = None, client: str = Depends(current_client)):
        """Menjalankan `ontop validate` terhadap artefak yang terpasang di host Ontop."""
        result = app.state.runner.validate(body.db_url if body else None)
        return CommandOut(**result.__dict__,
                          facts={'db_url': (body.db_url if body else None), 'client': client})

    return app
