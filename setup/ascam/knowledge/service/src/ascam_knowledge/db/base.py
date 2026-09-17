"""
Basis deklaratif dan utilitas kolom.

Konvensi:
- Nilai enumerasi disimpan sebagai TEXT + CHECK (bukan tipe ENUM PostgreSQL) agar mudah
  dievolusikan lewat migrasi.
- Nama constraint mengikuti naming convention tetap agar migrasi Alembic deterministik.
- Setiap tabel di skema `spec` membawa `spec_version_id`; referensi antartabel `spec`
  memakai foreign key komposit (spec_version_id, id) sehingga rujukan lintas versi
  ditolak oleh basis data.
"""
from datetime import datetime

from sqlalchemy import (BigInteger, CheckConstraint, DateTime, ForeignKey,
                        ForeignKeyConstraint, Identity, MetaData, Text, func)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMAS = ('registry', 'spec', 'ops')

NAMING = {
    'ix': 'ix_%(table_name)s_%(column_0_N_name)s',
    'uq': 'uq_%(table_name)s_%(column_0_N_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def pk_id() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(always=False), primary_key=True)


def created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def one_of(column: str, values: tuple[str, ...], name: str | None = None) -> CheckConstraint:
    allowed = ', '.join(f"'{v}'" for v in values)
    return CheckConstraint(f'{column} IN ({allowed})', name=name or column)


def spec_version_fk() -> Mapped[int]:
    return mapped_column(BigInteger, ForeignKey('spec.spec_version.id', ondelete='CASCADE'),
                         nullable=False, index=True)


def same_version_fk(columns: list[str], parent: str) -> ForeignKeyConstraint:
    """FK komposit (spec_version_id, <kolom>) -> <parent>(spec_version_id, id)."""
    return ForeignKeyConstraint(['spec_version_id', *columns],
                                [f'{parent}.spec_version_id', f'{parent}.id'],
                                ondelete='CASCADE')


MANAGED_BY = ('human', 'ascam')
