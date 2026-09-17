"""
Skema `spec`: versi spesifikasi OBDF (snapshot per versi) beserta Σ_S, dependensi Teiid,
ℳ (R2RML), 𝒯 (ontologi), triple generik, lineage, dan masalah konsistensi.

Setiap tabel membawa `spec_version_id`. Tabel yang dirujuk tabel lain memiliki
UNIQUE(spec_version_id, id), dan rujukan memakai FK komposit (lihat base.same_version_fk).
Isi versi hanya dapat diubah selama status versi `candidate` (trigger pada migrasi).
Baris versi tidak pernah dihapus (tombstone): retensi memakai spec.purge_version(), yang
menghapus isi versi dan mengubah statusnya menjadi `purged`, sehingga riwayat dan rujukan
dari skema ops tetap utuh.
"""
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, CHAR, DateTime, ForeignKey, Index, Integer,
                        PrimaryKeyConstraint, Text, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import MANAGED_BY, Base, created_at, one_of, pk_id, same_version_fk, spec_version_fk

SCHEMA = 'spec'
VERSION_STATUS = ('candidate', 'active', 'superseded', 'rejected', 'rolled_back', 'purged')
ORIGINS = ('sync', 'adaptation', 'rollback', 'manual')


def uq_version_id():
    return UniqueConstraint('spec_version_id', 'id')


def managed_by():
    return mapped_column(Text, server_default='human')


def introduced_in():
    return mapped_column(BigInteger, ForeignKey('spec.spec_version.id', ondelete='SET NULL'))


# ── versi dan artefak ───────────────────────────────────────────────────────────
class SpecVersion(Base):
    __tablename__ = 'spec_version'
    __table_args__ = (
        UniqueConstraint('obdf_id', 'version_no'),
        one_of('status', VERSION_STATUS),
        one_of('origin', ORIGINS),
        one_of('teiid_connection_type', ('BY_VERSION', 'ANY', 'NONE')),
        Index('uq_spec_version_one_active', 'obdf_id', unique=True,
              postgresql_where=text("status = 'active'")),
        {'schema': SCHEMA},
    )
    id: Mapped[int] = pk_id()
    obdf_id: Mapped[int] = mapped_column(ForeignKey('registry.obdf_instance.id', ondelete='RESTRICT'), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey('spec.spec_version.id', ondelete='RESTRICT'))
    origin: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default='candidate')
    teiid_vdb_name: Mapped[str | None] = mapped_column(Text)
    teiid_vdb_version: Mapped[str | None] = mapped_column(Text)
    teiid_connection_type: Mapped[str | None] = mapped_column(Text)
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey('ops.adaptation_plan.id', ondelete='RESTRICT', use_alter=True,
                   name='fk_spec_version_plan_id_adaptation_plan'))
    content_digest: Mapped[str | None] = mapped_column(Text, comment='sidik jari isi versi (Σ_S + artefak)')
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(Base):
    __tablename__ = 'artifact'
    __table_args__ = (uq_version_id(), UniqueConstraint('spec_version_id', 'kind', 'name'),
                      one_of('kind', ('vdb_xml', 'r2rml', 'ontology')), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, comment='nama berkas')
    media_type: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(CHAR(64))
    created_at: Mapped[datetime] = created_at()


class RdfTriple(Base):
    __tablename__ = 'rdf_triple'
    __table_args__ = (same_version_fk(['artifact_id'], 'spec.artifact'),
                      one_of('object_kind', ('iri', 'literal', 'bnode')),
                      Index(None, 'spec_version_id', 'artifact_id'),
                      Index(None, 'predicate'), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    artifact_id: Mapped[int] = mapped_column(BigInteger)
    subject: Mapped[str] = mapped_column(Text)
    predicate: Mapped[str] = mapped_column(Text)
    object: Mapped[str] = mapped_column(Text)
    object_kind: Mapped[str] = mapped_column(Text)
    datatype: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(Text)


class ConsistencyIssue(Base):
    __tablename__ = 'consistency_issue'
    __table_args__ = (one_of('severity', ('error', 'warning', 'info')),
                      one_of('detected_by', ('sync', 'ascam_vocabulary', 'ontop_validate', 'teiid_status')),
                      {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    code: Mapped[str] = mapped_column(Text, comment='kode masalah (docs/knowledge/README.md)')
    severity: Mapped[str] = mapped_column(Text)
    subject_kind: Mapped[str | None] = mapped_column(Text)
    subject_ref: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    detected_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


# ── Σ_S: Teiid ──────────────────────────────────────────────────────────────────
class TeiidModel(Base):
    __tablename__ = 'teiid_model'
    __table_args__ = (uq_version_id(), UniqueConstraint('spec_version_id', 'name'),
                      one_of('model_type', ('physical', 'virtual')), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    name: Mapped[str] = mapped_column(Text)
    model_type: Mapped[str] = mapped_column(Text)
    visible: Mapped[bool] = mapped_column(Boolean, server_default='true')


class TeiidModelSource(Base):
    __tablename__ = 'teiid_model_source'
    __table_args__ = (same_version_fk(['model_id'], 'spec.teiid_model'),
                      UniqueConstraint('spec_version_id', 'model_id', 'source_name'), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    model_id: Mapped[int] = mapped_column(BigInteger)
    source_name: Mapped[str] = mapped_column(Text)
    translator: Mapped[str] = mapped_column(Text)
    jndi_name: Mapped[str | None] = mapped_column(Text)
    source_system_id: Mapped[int | None] = mapped_column(
        ForeignKey('registry.source_system.id', ondelete='RESTRICT'), index=True)


class TeiidTable(Base):
    __tablename__ = 'teiid_table'
    __table_args__ = (uq_version_id(), same_version_fk(['model_id'], 'spec.teiid_model'),
                      UniqueConstraint('spec_version_id', 'model_id', 'name'),
                      one_of('kind', ('foreign', 'view', 'materialized_view')),
                      one_of('managed_by', MANAGED_BY),
                      Index(None, 'spec_version_id', 'source_schema', 'source_table'), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    model_id: Mapped[int] = mapped_column(BigInteger)
    uid: Mapped[str | None] = mapped_column(Text, comment='UID Teiid')
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    name_in_source: Mapped[str | None] = mapped_column(Text)
    source_schema: Mapped[str | None] = mapped_column(Text, comment='hasil normalisasi')
    source_table: Mapped[str | None] = mapped_column(Text, comment='hasil normalisasi')
    managed_by: Mapped[str] = managed_by()
    introduced_in_version: Mapped[int | None] = introduced_in()


class TeiidColumn(Base):
    __tablename__ = 'teiid_column'
    __table_args__ = (uq_version_id(), same_version_fk(['table_id'], 'spec.teiid_table'),
                      UniqueConstraint('spec_version_id', 'table_id', 'name'),
                      one_of('managed_by', MANAGED_BY),
                      Index(None, 'spec_version_id', 'table_id', 'source_column'), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    table_id: Mapped[int] = mapped_column(BigInteger)
    uid: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, comment='nama di Teiid')
    name_in_source: Mapped[str | None] = mapped_column(Text)
    source_column: Mapped[str | None] = mapped_column(Text, comment='nama efektif di sumber, dinormalisasi')
    position: Mapped[int] = mapped_column(Integer, comment='tidak dijamin berurutan')
    data_type: Mapped[str] = mapped_column(Text)
    native_type: Mapped[str | None] = mapped_column(Text)
    nullable: Mapped[bool | None] = mapped_column(Boolean)
    length: Mapped[int | None] = mapped_column(Integer)
    precision: Mapped[int | None] = mapped_column(Integer)
    scale: Mapped[int | None] = mapped_column(Integer)
    in_primary_key: Mapped[bool] = mapped_column(Boolean, server_default='false')
    managed_by: Mapped[str] = managed_by()
    introduced_in_version: Mapped[int | None] = introduced_in()


class TeiidView(Base):
    __tablename__ = 'teiid_view'
    __table_args__ = (PrimaryKeyConstraint('spec_version_id', 'table_id'),
                      same_version_fk(['table_id'], 'spec.teiid_table'),
                      one_of('parse_status', ('ok', 'failed')), {'schema': SCHEMA})
    spec_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('spec.spec_version.id', ondelete='CASCADE'))
    table_id: Mapped[int] = mapped_column(BigInteger)
    body: Mapped[str] = mapped_column(Text, comment='SYSADMIN.Views.Body')
    parse_status: Mapped[str] = mapped_column(Text)
    uses_star: Mapped[bool] = mapped_column(Boolean, server_default='false')


class TeiidViewColumn(Base):
    __tablename__ = 'teiid_view_column'
    __table_args__ = (PrimaryKeyConstraint('spec_version_id', 'column_id'),
                      same_version_fk(['column_id'], 'spec.teiid_column'),
                      one_of('expression_kind', ('passthrough', 'expression', 'star', 'unknown')),
                      {'schema': SCHEMA})
    spec_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('spec.spec_version.id', ondelete='CASCADE'))
    column_id: Mapped[int] = mapped_column(BigInteger)
    expression_kind: Mapped[str] = mapped_column(Text)
    expression_sql: Mapped[str | None] = mapped_column(Text)


class TeiidDependency(Base):
    __tablename__ = 'teiid_dependency'
    __table_args__ = (
        same_version_fk(['dependent_table_id'], 'spec.teiid_table'),
        same_version_fk(['dependent_column_id'], 'spec.teiid_column'),
        same_version_fk(['used_table_id'], 'spec.teiid_table'),
        same_version_fk(['used_column_id'], 'spec.teiid_column'),
        one_of('derived_role', ('projection', 'predicate', 'table')),
        Index(None, 'spec_version_id', 'used_column_id'),
        Index(None, 'spec_version_id', 'used_table_id'),
        {'schema': SCHEMA},
    )
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    dependent_table_id: Mapped[int] = mapped_column(BigInteger)
    dependent_column_id: Mapped[int | None] = mapped_column(BigInteger, comment='kosong = tingkat view')
    used_table_id: Mapped[int] = mapped_column(BigInteger)
    used_column_id: Mapped[int | None] = mapped_column(BigInteger, comment='kosong = tingkat tabel')
    derived_role: Mapped[str] = mapped_column(Text)


class TeiidRoutine(Base):
    __tablename__ = 'teiid_routine'
    __table_args__ = (same_version_fk(['model_id'], 'spec.teiid_model'),
                      one_of('kind', ('stored_procedure', 'trigger')), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    model_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    table_name: Mapped[str | None] = mapped_column(Text, comment='tabel pemicu (trigger)')
    body: Mapped[str | None] = mapped_column(Text)


class TeiidAlter(Base):
    __tablename__ = 'teiid_alter'
    __table_args__ = (same_version_fk(['model_id'], 'spec.teiid_model'),
                      UniqueConstraint('spec_version_id', 'model_id', 'seq'), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    model_id: Mapped[int] = mapped_column(BigInteger)
    seq: Mapped[int] = mapped_column(Integer)
    statement: Mapped[str] = mapped_column(Text, comment='ALTER yang ditambahkan ASCAM (ADR-0002)')
    plan_id: Mapped[int | None] = mapped_column(ForeignKey('ops.adaptation_plan.id', ondelete='RESTRICT'))


# ── ℳ: R2RML ────────────────────────────────────────────────────────────────────
class TriplesMap(Base):
    __tablename__ = 'triples_map'
    __table_args__ = (uq_version_id(), UniqueConstraint('spec_version_id', 'iri'),
                      same_version_fk(['artifact_id'], 'spec.artifact'),
                      one_of('logical_table_kind', ('table', 'sql_query')),
                      one_of('sql_parse_status', ('ok', 'failed', 'not_applicable')),
                      one_of('managed_by', MANAGED_BY), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    artifact_id: Mapped[int | None] = mapped_column(BigInteger)
    iri: Mapped[str] = mapped_column(Text)
    logical_table_kind: Mapped[str] = mapped_column(Text)
    table_name: Mapped[str | None] = mapped_column(Text)
    sql_query: Mapped[str | None] = mapped_column(Text)
    sql_parse_status: Mapped[str] = mapped_column(Text)
    managed_by: Mapped[str] = managed_by()
    introduced_in_version: Mapped[int | None] = introduced_in()


class LogicalSource(Base):
    __tablename__ = 'logical_source'
    __table_args__ = (same_version_fk(['triples_map_id'], 'spec.triples_map'),
                      same_version_fk(['teiid_table_id'], 'spec.teiid_table'), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    triples_map_id: Mapped[int] = mapped_column(BigInteger)
    teiid_table_id: Mapped[int | None] = mapped_column(BigInteger, comment='kosong bila tidak terselesaikan')
    reference_name: Mapped[str] = mapped_column(Text, comment='nama tabel/view sebagaimana tertulis')
    alias: Mapped[str | None] = mapped_column(Text)


class LogicalColumn(Base):
    __tablename__ = 'logical_column'
    __table_args__ = (uq_version_id(), same_version_fk(['triples_map_id'], 'spec.triples_map'),
                      UniqueConstraint('spec_version_id', 'triples_map_id', 'name'),
                      one_of('expression_kind', ('passthrough', 'expression', 'star', 'unknown')),
                      {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    triples_map_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(Text, comment='nama yang dilihat term map')
    expression_kind: Mapped[str] = mapped_column(Text)
    expression_sql: Mapped[str | None] = mapped_column(Text)


class LogicalColumnSource(Base):
    __tablename__ = 'logical_column_source'
    __table_args__ = (PrimaryKeyConstraint('spec_version_id', 'logical_column_id', 'teiid_column_id'),
                      same_version_fk(['logical_column_id'], 'spec.logical_column'),
                      same_version_fk(['teiid_column_id'], 'spec.teiid_column'), {'schema': SCHEMA})
    spec_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('spec.spec_version.id', ondelete='CASCADE'))
    logical_column_id: Mapped[int] = mapped_column(BigInteger)
    teiid_column_id: Mapped[int] = mapped_column(BigInteger)


class SqlReference(Base):
    __tablename__ = 'sql_reference'
    __table_args__ = (same_version_fk(['triples_map_id'], 'spec.triples_map'),
                      same_version_fk(['teiid_column_id'], 'spec.teiid_column'),
                      one_of('clause', ('where', 'join', 'group_by', 'having', 'order_by', 'subquery', 'other')),
                      {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    triples_map_id: Mapped[int] = mapped_column(BigInteger)
    teiid_column_id: Mapped[int] = mapped_column(BigInteger)
    clause: Mapped[str] = mapped_column(Text)


class PredicateObjectMap(Base):
    __tablename__ = 'predicate_object_map'
    __table_args__ = (uq_version_id(), same_version_fk(['triples_map_id'], 'spec.triples_map'),
                      UniqueConstraint('spec_version_id', 'triples_map_id', 'signature'),
                      one_of('managed_by', MANAGED_BY), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    triples_map_id: Mapped[int] = mapped_column(BigInteger)
    signature: Mapped[str] = mapped_column(Text, comment='tanda tangan struktural, pengganti label blank node')
    managed_by: Mapped[str] = managed_by()
    introduced_in_version: Mapped[int | None] = introduced_in()


class TermMap(Base):
    __tablename__ = 'term_map'
    __table_args__ = (uq_version_id(), same_version_fk(['triples_map_id'], 'spec.triples_map'),
                      same_version_fk(['pom_id'], 'spec.predicate_object_map'),
                      one_of('position', ('subject', 'predicate', 'object', 'graph')),
                      one_of('value_kind', ('constant', 'column', 'template', 'parent_triples_map')),
                      one_of('term_type', ('iri', 'literal', 'blank')), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    triples_map_id: Mapped[int] = mapped_column(BigInteger)
    pom_id: Mapped[int | None] = mapped_column(BigInteger, comment='kosong untuk subject map')
    position: Mapped[str] = mapped_column(Text)
    value_kind: Mapped[str] = mapped_column(Text)
    value: Mapped[str] = mapped_column(Text)
    term_type: Mapped[str | None] = mapped_column(Text)
    datatype: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)


class TermMapColumn(Base):
    __tablename__ = 'term_map_column'
    __table_args__ = (PrimaryKeyConstraint('spec_version_id', 'term_map_id', 'logical_column_id'),
                      same_version_fk(['term_map_id'], 'spec.term_map'),
                      same_version_fk(['logical_column_id'], 'spec.logical_column'), {'schema': SCHEMA})
    spec_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('spec.spec_version.id', ondelete='CASCADE'))
    term_map_id: Mapped[int] = mapped_column(BigInteger)
    logical_column_id: Mapped[int] = mapped_column(BigInteger)
    template_position: Mapped[int | None] = mapped_column(Integer)


class JoinCondition(Base):
    __tablename__ = 'join_condition'
    __table_args__ = (same_version_fk(['term_map_id'], 'spec.term_map'), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    term_map_id: Mapped[int] = mapped_column(BigInteger)
    child_column: Mapped[str] = mapped_column(Text)
    parent_column: Mapped[str] = mapped_column(Text)
    parent_triples_map_iri: Mapped[str] = mapped_column(Text)


class SubjectClass(Base):
    __tablename__ = 'subject_class'
    __table_args__ = (PrimaryKeyConstraint('spec_version_id', 'triples_map_id', 'class_iri'),
                      same_version_fk(['triples_map_id'], 'spec.triples_map'), {'schema': SCHEMA})
    spec_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('spec.spec_version.id', ondelete='CASCADE'))
    triples_map_id: Mapped[int] = mapped_column(BigInteger)
    class_iri: Mapped[str] = mapped_column(Text)


# ── 𝒯: ontologi ─────────────────────────────────────────────────────────────────
class Ontology(Base):
    __tablename__ = 'ontology'
    __table_args__ = (uq_version_id(), UniqueConstraint('spec_version_id', 'iri'),
                      same_version_fk(['artifact_id'], 'spec.artifact'), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    artifact_id: Mapped[int | None] = mapped_column(BigInteger)
    iri: Mapped[str] = mapped_column(Text)
    version_iri: Mapped[str | None] = mapped_column(Text)
    version_info: Mapped[str | None] = mapped_column(Text)


class OntImport(Base):
    __tablename__ = 'ont_import'
    __table_args__ = (PrimaryKeyConstraint('spec_version_id', 'ontology_id', 'imported_iri'),
                      same_version_fk(['ontology_id'], 'spec.ontology'), {'schema': SCHEMA})
    spec_version_id: Mapped[int] = mapped_column(BigInteger, ForeignKey('spec.spec_version.id', ondelete='CASCADE'))
    ontology_id: Mapped[int] = mapped_column(BigInteger)
    imported_iri: Mapped[str] = mapped_column(Text)


ENTITY_KINDS = ('class', 'datatype_property', 'object_property', 'annotation_property')


class OntEntity(Base):
    __tablename__ = 'ont_entity'
    __table_args__ = (uq_version_id(), same_version_fk(['ontology_id'], 'spec.ontology'),
                      UniqueConstraint('spec_version_id', 'ontology_id', 'iri', 'kind'),
                      one_of('kind', ENTITY_KINDS), one_of('declared_in', ('local', 'imported')),
                      one_of('managed_by', MANAGED_BY),
                      Index(None, 'spec_version_id', 'iri'), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    ontology_id: Mapped[int] = mapped_column(BigInteger)
    iri: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    declared_in: Mapped[str] = mapped_column(Text, server_default='local')
    deprecated: Mapped[bool] = mapped_column(Boolean, server_default='false')
    functional: Mapped[bool] = mapped_column(Boolean, server_default='false')
    managed_by: Mapped[str] = managed_by()
    introduced_in_version: Mapped[int | None] = introduced_in()


class OntDomain(Base):
    __tablename__ = 'ont_domain'
    __table_args__ = (same_version_fk(['entity_id'], 'spec.ont_entity'), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    entity_id: Mapped[int] = mapped_column(BigInteger)
    class_expression: Mapped[str] = mapped_column(Text, comment='IRI atau bentuk kompleks (mis. union)')
    is_simple: Mapped[bool] = mapped_column(Boolean)


class OntRange(Base):
    __tablename__ = 'ont_range'
    __table_args__ = (same_version_fk(['entity_id'], 'spec.ont_entity'), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    entity_id: Mapped[int] = mapped_column(BigInteger)
    range_iri: Mapped[str] = mapped_column(Text)
    owl2ql_compatible: Mapped[bool | None] = mapped_column(Boolean)


class OntPropertyRelation(Base):
    __tablename__ = 'ont_property_relation'
    __table_args__ = (same_version_fk(['entity_id'], 'spec.ont_entity'),
                      one_of('relation', ('sub_property_of', 'equivalent_property', 'inverse_of', 'disjoint_with')),
                      {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    entity_id: Mapped[int] = mapped_column(BigInteger)
    relation: Mapped[str] = mapped_column(Text)
    other_iri: Mapped[str] = mapped_column(Text)


class OntAnnotation(Base):
    __tablename__ = 'ont_annotation'
    __table_args__ = (same_version_fk(['entity_id'], 'spec.ont_entity'),
                      one_of('managed_by', MANAGED_BY), {'schema': SCHEMA})
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    entity_id: Mapped[int] = mapped_column(BigInteger)
    property_iri: Mapped[str] = mapped_column(Text)
    value: Mapped[str] = mapped_column(Text)
    lang: Mapped[str | None] = mapped_column(Text)
    managed_by: Mapped[str] = managed_by()


# ── lineage ─────────────────────────────────────────────────────────────────────
class ColumnUsage(Base):
    __tablename__ = 'column_usage'
    __table_args__ = (
        same_version_fk(['foreign_column_id'], 'spec.teiid_column'),
        same_version_fk(['triples_map_id'], 'spec.triples_map'),
        same_version_fk(['term_map_id'], 'spec.term_map'),
        same_version_fk(['predicate_entity_id'], 'spec.ont_entity'),
        one_of('role', ('literal_value', 'iri_template', 'join_key', 'sql_predicate',
                        'dynamic_predicate', 'projection_only')),
        one_of('weakest_link', ('direct', 'passthrough', 'star', 'expression', 'predicate')),
        Index(None, 'spec_version_id', 'foreign_column_id'),
        Index(None, 'spec_version_id', 'predicate_iri'),
        {'schema': SCHEMA},
    )
    id: Mapped[int] = pk_id()
    spec_version_id: Mapped[int] = spec_version_fk()
    foreign_column_id: Mapped[int] = mapped_column(BigInteger, comment='kolom foreign table (ujung sumber)')
    triples_map_id: Mapped[int] = mapped_column(BigInteger)
    term_map_id: Mapped[int | None] = mapped_column(BigInteger, comment='kosong bila hanya di SQL')
    role: Mapped[str] = mapped_column(Text)
    predicate_iri: Mapped[str | None] = mapped_column(Text)
    predicate_entity_id: Mapped[int | None] = mapped_column(BigInteger)
    path: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"),
                                       comment='urutan kolom view yang dilalui')
    weakest_link: Mapped[str] = mapped_column(Text)
