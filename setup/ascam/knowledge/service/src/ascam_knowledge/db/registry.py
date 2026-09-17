"""Skema `registry`: OBDF yang dikelola, target, kredensial, sumber, dan kebijakan."""
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, DateTime, ForeignKey, Integer,
                        LargeBinary, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, one_of, pk_id

S = {'schema': 'registry'}


class ObdfInstance(Base):
    __tablename__ = 'obdf_instance'
    __table_args__ = S
    id: Mapped[int] = pk_id()
    name: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class Credential(Base):
    __tablename__ = 'credential'
    __table_args__ = (UniqueConstraint('obdf_id', 'name'), S)
    id: Mapped[int] = pk_id()
    obdf_id: Mapped[int] = mapped_column(ForeignKey('registry.obdf_instance.id', ondelete='CASCADE'), index=True)
    name: Mapped[str] = mapped_column(Text, comment='nama rujukan pada konfigurasi dan UI')
    username: Mapped[str] = mapped_column(Text)
    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, server_default='1')
    created_at: Mapped[datetime] = created_at()
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


TARGET_KINDS = ('teiid_mgmt', 'teiid_odbc', 'ontop_sparql', 'ontop_agent', 'kafka')


class Target(Base):
    __tablename__ = 'target'
    __table_args__ = (UniqueConstraint('obdf_id', 'name'), one_of('kind', TARGET_KINDS), S)
    id: Mapped[int] = pk_id()
    obdf_id: Mapped[int] = mapped_column(ForeignKey('registry.obdf_instance.id', ondelete='CASCADE'), index=True)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    endpoint: Mapped[dict] = mapped_column(JSONB, comment='host, port, path, tls')
    credential_id: Mapped[int | None] = mapped_column(
        ForeignKey('registry.credential.id', ondelete='RESTRICT'), index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default='true')
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class SourceSystem(Base):
    __tablename__ = 'source_system'
    __table_args__ = (UniqueConstraint('obdf_id', 'logical_name'),
                      one_of('dbms', ('postgresql', 'mysql')),
                      one_of('identifier_case', ('lower', 'preserve', 'insensitive')), S)
    id: Mapped[int] = pk_id()
    obdf_id: Mapped[int] = mapped_column(ForeignKey('registry.obdf_instance.id', ondelete='CASCADE'), index=True)
    logical_name: Mapped[str] = mapped_column(Text, comment='nilai field source pada event')
    dbms: Mapped[str] = mapped_column(Text)
    database_name: Mapped[str] = mapped_column(Text)
    kafka_topic: Mapped[str | None] = mapped_column(Text)
    identifier_case: Mapped[str] = mapped_column(Text, comment='aturan normalisasi nama identifier')


class Setting(Base):
    __tablename__ = 'setting'
    __table_args__ = S
    obdf_id: Mapped[int] = mapped_column(ForeignKey('registry.obdf_instance.id', ondelete='CASCADE'),
                                         primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class NamingPolicy(Base):
    __tablename__ = 'naming_policy'
    __table_args__ = (one_of('on_collision', ('qualify_with_class', 'hitl')), S)
    obdf_id: Mapped[int] = mapped_column(ForeignKey('registry.obdf_instance.id', ondelete='CASCADE'),
                                         primary_key=True)
    property_iri_template: Mapped[str] = mapped_column(Text)
    on_collision: Mapped[str] = mapped_column(Text, server_default='hitl')
    label_language: Mapped[str] = mapped_column(Text, server_default='id')
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class TypeMapping(Base):
    __tablename__ = 'type_mapping'
    __table_args__ = (UniqueConstraint('obdf_id', 'dbms', 'native_type'),
                      one_of('dbms', ('postgresql', 'mysql')), S)
    id: Mapped[int] = pk_id()
    obdf_id: Mapped[int] = mapped_column(ForeignKey('registry.obdf_instance.id', ondelete='CASCADE'), index=True)
    dbms: Mapped[str] = mapped_column(Text)
    native_type: Mapped[str] = mapped_column(Text)
    teiid_type: Mapped[str] = mapped_column(Text)
    xsd_datatype: Mapped[str] = mapped_column(Text)
    owl2ql_compatible: Mapped[bool] = mapped_column(Boolean)


class ConnectionCheck(Base):
    __tablename__ = 'connection_check'
    __table_args__ = S
    id: Mapped[int] = pk_id()
    target_id: Mapped[int] = mapped_column(ForeignKey('registry.target.id', ondelete='CASCADE'), index=True)
    checked_at: Mapped[datetime] = created_at()
    ok: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)
