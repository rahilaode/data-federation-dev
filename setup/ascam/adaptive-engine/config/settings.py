"""
ASCAM — Konfigurasi Terpusat
Semua konstanta dan env variable ada di sini.
Modul lain import dari sini, tidak hardcode sendiri.
"""

import os

# ── Kafka ────────────────────────────────────────────────────
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9092')
KAFKA_TOPICS = [
    'kemensos.schema_monitor.ddl_event_log',
    'dukcapil.dukcapil.ddl_event_log',
]
KAFKA_GROUP_ID       = os.getenv('KAFKA_GROUP_ID', 'ascam-adaptive-engine')
KAFKA_RETRY_ATTEMPTS = int(os.getenv('KAFKA_RETRY_ATTEMPTS', '15'))
KAFKA_RETRY_DELAY    = int(os.getenv('KAFKA_RETRY_DELAY', '10'))

# ── File paths (di dalam container) ─────────────────────────
OBDA_PATH = os.getenv('OBDA_PATH', '/opt/ontop/input/mapping.obda')
TTL_PATH  = os.getenv('TTL_PATH',  '/opt/ontop/input/ontology_file.ttl')
VDB_PATH  = os.getenv('VDB_PATH',  '/opt/teiid/data-federation/government-vdb.xml')

# ── Ontop & Teiid ────────────────────────────────────────────
ONTOP_CONTAINER_NAME  = os.getenv('ONTOP_CONTAINER_NAME',  'vkg-system-ontop-teiid')
TEIID_CONTAINER_NAME  = os.getenv('TEIID_CONTAINER_NAME',  'data-federation-teiid')
DOCKER_SOCK           = '/var/run/docker.sock'

# ── Ontologi ─────────────────────────────────────────────────
ONTOLOGY_PREFIX = 'bansos'
ONTOLOGY_BASE   = 'http://bansos.go.id/ontology/'

# ── Mapping: tabel → mappingId di .obda ─────────────────────
TABLE_TO_MAPPING_ID = {
    'master_penduduk'  : 'MAP-PENDUDUK',
    'master_keluarga'  : 'MAP-KELUARGA',
    'master_wilayah'   : 'MAP-WILAYAH',
    'penerima_manfaat' : 'MAP-PENERIMA',
    'program_bansos'   : 'MAP-PROGRAM',
    'transaksi_bansos' : 'MAP-TRANSAKSI',
    'eligibility_check': 'MAP-ELIGIBILITY',
}

# ── Mapping: tabel → class ontologi ─────────────────────────
TABLE_TO_CLASS = {
    'master_penduduk'  : 'Penduduk',
    'master_keluarga'  : 'KeluargaKependudukan',
    'master_wilayah'   : 'Wilayah',
    'penerima_manfaat' : 'PenerimaBansos',
    'program_bansos'   : 'ProgramBansos',
    'transaksi_bansos' : 'TransaksiBansos',
    'eligibility_check': 'EligibilityCheck',
}

# ── Mapping: tabel → model VDB (nama <model> di government-vdb.xml) ──
TABLE_TO_VDB_MODEL = {
    'master_penduduk'  : 'dukcapil',
    'master_keluarga'  : 'dukcapil',
    'master_wilayah'   : 'dukcapil',
    'penerima_manfaat' : 'kemensos',
    'program_bansos'   : 'kemensos',
    'transaksi_bansos' : 'kemensos',
    'eligibility_check': 'kemensos',
}

# ── Mapping: tipe SQL → tipe XSD ────────────────────────────
SQL_TO_XSD = {
    'varchar'  : 'xsd:string',
    'char'     : 'xsd:string',
    'text'     : 'xsd:string',
    'int'      : 'xsd:integer',
    'integer'  : 'xsd:integer',
    'bigint'   : 'xsd:integer',
    'tinyint'  : 'xsd:integer',
    'smallint' : 'xsd:integer',
    'mediumint': 'xsd:integer',
    'numeric'  : 'xsd:decimal',
    'decimal'  : 'xsd:decimal',
    'float'    : 'xsd:float',
    'double'   : 'xsd:double',
    'date'     : 'xsd:date',
    'datetime' : 'xsd:dateTime',
    'timestamp': 'xsd:dateTime',
    'boolean'  : 'xsd:boolean',
    'bool'     : 'xsd:boolean',
}

# ── Mapping: tipe SQL → tipe Teiid (untuk VDB DDL) ──────────
# Teiid menggunakan subset tipe ANSI SQL
SQL_TO_TEIID = {
    'varchar'  : 'string',
    'char'     : 'string',
    'text'     : 'string',
    'int'      : 'integer',
    'integer'  : 'integer',
    'bigint'   : 'long',
    'tinyint'  : 'byte',
    'smallint' : 'short',
    'mediumint': 'integer',
    'numeric'  : 'bigdecimal',
    'decimal'  : 'bigdecimal',
    'float'    : 'float',
    'double'   : 'double',
    'date'     : 'date',
    'datetime' : 'timestamp',
    'timestamp': 'timestamp',
    'boolean'  : 'boolean',
    'bool'     : 'boolean',
}