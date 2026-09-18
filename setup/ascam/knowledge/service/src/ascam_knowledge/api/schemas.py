"""Skema permintaan/respons API (Pydantic v2). Rahasia tidak pernah muncul di respons."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

Name = Field(pattern=r'^[a-z0-9][a-z0-9_.-]{0,62}$', description='huruf kecil, angka, _ . -')
Dbms = Literal['postgresql', 'mysql']
TargetKind = Literal['teiid_mgmt', 'teiid_odbc', 'ontop_sparql', 'ontop_agent', 'kafka']
IdentifierCase = Literal['lower', 'preserve', 'insensitive']
DEFAULT_IDENTIFIER_CASE = {'postgresql': 'lower', 'mysql': 'insensitive'}


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── OBDF ─────────────────────────────────────────────────────────────────────────
class ObdfIn(BaseModel):
    name: str = Name
    description: str | None = None


class ObdfOut(Out):
    id: int
    name: str
    description: str | None
    created_at: datetime
    active_version_no: int | None = None


# ── sumber data ──────────────────────────────────────────────────────────────────
class SourceIn(BaseModel):
    logical_name: str = Name
    dbms: Dbms
    database_name: str
    kafka_topic: str | None = None
    identifier_case: IdentifierCase | None = None
    default_schema: str | None = Field(
        default=None, description='skema bawaan koneksi; kosong = public (PostgreSQL) / nama basis data (MySQL)')

    def resolved_case(self) -> str:
        return self.identifier_case or DEFAULT_IDENTIFIER_CASE[self.dbms]

    def resolved_schema(self) -> str:
        if self.default_schema:
            return self.default_schema
        return 'public' if self.dbms == 'postgresql' else self.database_name


class SourceOut(Out):
    id: int
    logical_name: str
    dbms: str
    database_name: str
    kafka_topic: str | None
    identifier_case: str
    default_schema: str | None


# ── kredensial ───────────────────────────────────────────────────────────────────
class CredentialIn(BaseModel):
    name: str = Name
    username: str
    secret: SecretStr


class SecretIn(BaseModel):
    secret: SecretStr


class CredentialOut(Out):
    id: int
    name: str
    username: str
    key_version: int
    created_at: datetime
    rotated_at: datetime | None


# ── target ───────────────────────────────────────────────────────────────────────
class Endpoint(BaseModel):
    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)
    path: str | None = None
    tls: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


REQUIRES_CREDENTIAL = {'teiid_mgmt', 'teiid_odbc', 'ontop_agent'}


class TargetIn(BaseModel):
    kind: TargetKind
    name: str = Name
    endpoint: Endpoint
    credential_id: int | None = None
    enabled: bool = True

    @model_validator(mode='after')
    def _check(self):
        if self.kind in REQUIRES_CREDENTIAL and self.credential_id is None:
            raise ValueError(f'target {self.kind} memerlukan credential_id')
        if self.kind == 'teiid_odbc' and not self.endpoint.options.get('vdb'):
            raise ValueError('target teiid_odbc memerlukan endpoint.options.vdb')
        return self


class TargetPatch(BaseModel):
    endpoint: Endpoint | None = None
    credential_id: int | None = None
    enabled: bool | None = None


class TargetOut(Out):
    id: int
    kind: str
    name: str
    endpoint: dict[str, Any]
    credential_id: int | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


# ── kebijakan ────────────────────────────────────────────────────────────────────
class SettingIn(BaseModel):
    value: Any


class SettingOut(Out):
    key: str
    value: Any
    updated_at: datetime


class NamingPolicyIn(BaseModel):
    property_iri_template: str = Field(
        description='placeholder: {namespace}, {column}, {column_camel}, {class_local}')
    on_collision: Literal['qualify_with_class', 'hitl'] = 'hitl'
    label_language: str = 'id'
    namespace: str | None = Field(default=None, description='disimpan ke setting ontology.namespace')


class NamingPolicyOut(Out):
    property_iri_template: str
    on_collision: str
    label_language: str
    updated_at: datetime


class TypeMappingIn(BaseModel):
    dbms: Dbms
    native_type: str = Field(min_length=1)
    teiid_type: str
    xsd_datatype: str
    owl2ql_compatible: bool | None = Field(default=None, description='dihitung bila kosong')


class TypeMappingOut(Out):
    id: int
    dbms: str
    native_type: str
    teiid_type: str
    xsd_datatype: str
    owl2ql_compatible: bool


# ── audit ────────────────────────────────────────────────────────────────────────
class AuditOut(Out):
    id: int
    at: datetime
    actor: str
    action: str
    object_kind: str | None
    object_ref: str | None
    detail: dict[str, Any]


# ── konfigurasi deklaratif (D5) ──────────────────────────────────────────────────
class ConfigCredential(BaseModel):
    name: str = Name
    username: str
    secret: SecretStr | None = None
    secret_file: str | None = None

    @model_validator(mode='after')
    def _one_source(self):
        if (self.secret is None) == (self.secret_file is None):
            raise ValueError('isi tepat satu dari secret atau secret_file')
        return self


class ConfigTarget(BaseModel):
    kind: TargetKind
    name: str = Name
    endpoint: Endpoint
    credential: str | None = Field(default=None, description='nama kredensial')
    enabled: bool = True


class ConfigDocument(BaseModel):
    version: Literal[1] = 1
    obdf: ObdfIn
    sources: list[SourceIn] = Field(default_factory=list)
    credentials: list[ConfigCredential] = Field(default_factory=list)
    targets: list[ConfigTarget] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    naming_policy: NamingPolicyIn | None = None
    type_mappings: list[TypeMappingIn] = Field(default_factory=list)


class ApplySummary(BaseModel):
    obdf_id: int
    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)


# ── uji koneksi ──────────────────────────────────────────────────────────────────
class CheckOut(Out):
    id: int
    target_id: int
    checked_at: datetime
    ok: bool
    latency_ms: int | None
    detail: dict[str, Any]
    actor: str | None


class TargetStatusOut(BaseModel):
    target: TargetOut
    last_check: CheckOut | None


# ── versi spesifikasi dan sync ───────────────────────────────────────────────────
class SpecVersionOut(Out):
    id: int
    version_no: int
    parent_id: int | None
    origin: str
    status: str
    teiid_vdb_name: str | None
    teiid_vdb_version: str | None
    teiid_connection_type: str | None
    content_digest: str | None
    created_at: datetime
    sealed_at: datetime | None


class ArtifactOut(Out):
    id: int
    kind: str
    name: str
    media_type: str
    sha256: str


class VersionDetailOut(BaseModel):
    version: SpecVersionOut
    counts: dict[str, int]
    artifacts: list[ArtifactOut]
    issues: list[dict[str, Any]]


class SyncResultOut(BaseModel):
    sync_run_id: int
    changed: bool
    spec_version_id: int | None
    version_no: int | None
    drift_detected: bool
    counts: dict[str, int]
    issues: list[dict[str, Any]]


class SyncRunOut(Out):
    id: int
    trigger: str
    status: str
    produced_spec_version_id: int | None
    drift_detected: bool | None
    drift_summary: dict[str, Any] | None
    error: dict[str, Any] | None
    started_at: datetime
    finished_at: datetime | None


# ── analisis dampak (D11) ────────────────────────────────────────────────────────
class ImpactIn(BaseModel):
    operation: Literal['add', 'drop', 'rename']
    source: str = Field(description='logical_name sumber terdaftar')
    schema_name: str | None = Field(default=None, alias='schema',
                                    description='kosong = skema bawaan sumber')
    table: str
    column: str | None = None
    new_column: str | None = Field(default=None, description='untuk operasi rename')
    column_type: str | None = Field(default=None, description='tipe asli sumber untuk operasi add')

    model_config = ConfigDict(populate_by_name=True)

    def model_dump(self, **kwargs):
        data = super().model_dump(**kwargs)
        data['schema'] = data.pop('schema_name', None)
        return data


class TargetOutcome(BaseModel):
    column_id: int
    model: str
    table: str
    column: str
    source_column: str | None
    in_primary_key: bool
    usages: list[dict[str, Any]]


class ImpactOut(BaseModel):
    obdf_id: int
    spec_version_id: int | None
    operation: str
    pattern: str | None
    decision: str
    reasons: list[str]
    actions: list[dict[str, Any]]
    targets: list[TargetOutcome]
