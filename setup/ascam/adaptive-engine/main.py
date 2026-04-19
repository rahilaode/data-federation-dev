"""
ASCAM — Adaptive Mapping Engine
Entry point. Hanya berisi Kafka consumer dan orkestrator.
Semua logik ada di modul masing-masing.
"""

import json
import logging
import time

from kafka import KafkaConsumer

from config.settings      import (KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPICS,
                                   KAFKA_GROUP_ID, KAFKA_RETRY_ATTEMPTS,
                                   KAFKA_RETRY_DELAY, OBDA_PATH, TTL_PATH,
                                   VDB_PATH, TABLE_TO_MAPPING_ID,
                                   TABLE_TO_CLASS, TABLE_TO_VDB_MODEL)
from pattern_library.patterns      import (get_pattern, sql_type_to_xsd,
                                   column_to_property)
from pattern_library.ddl_parser import (parse_ddl, extract_event)
from mapping_engine.obda_updater       import OBDAUpdater
from mapping_engine.ttl_updater import TTLUpdater
from vdb_engine.vdb_updater           import VDBUpdater
from vdb_engine.container_restarter import restart_all, restart_ontop_only

logging.basicConfig(
    level   = logging.INFO,
    format  = '%(asctime)s [%(levelname)s] %(name)s — %(message)s',
    datefmt = '%Y-%m-%dT%H:%M:%S',
)
log = logging.getLogger('ascam.main')


# ─────────────────────────────────────────────────────────────
# ORKESTRATOR EVENT
# ─────────────────────────────────────────────────────────────

def process_event(msg_dict: dict):
    """Proses satu event DDL dari Kafka — dilanjutkan dari kode Anda."""

    # Ekstrak payload dari format Debezium
    after = extract_event(msg_dict)
    if after is None:
        return

    # Filter: hanya proses event regulated
    if not bool(after.get('is_regulated', 0)):
        return

    ddl_raw = after.get('ddl_command', '')
    if not ddl_raw:
        return

    log.info('══ Event: %s', ddl_raw[:100])

    # Parse DDL
    parsed = parse_ddl(ddl_raw)
    if parsed is None:
        return

    alter_type = parsed['alter_type']   # 'ADD COLUMN' | 'DROP COLUMN' | 'RENAME COLUMN'
    table_name = parsed['table_name']

    # Lookup pattern
    pattern = get_pattern(alter_type)
    if pattern is None:
        log.warning('Tidak ada pola untuk %s', alter_type)
        return

    # Lookup mapping metadata
    mapping_id     = TABLE_TO_MAPPING_ID.get(table_name)
    ontology_class = TABLE_TO_CLASS.get(table_name, '')
    vdb_model      = TABLE_TO_VDB_MODEL.get(table_name)

    if mapping_id is None:
        log.warning('Tabel %s tidak ada di TABLE_TO_MAPPING_ID, skip.', table_name)
        return

    log.info('Pola %s | mapping=%s | vdb_model=%s | mode=%s',
             pattern['pattern_id'], mapping_id, vdb_model, pattern['auto_mode'])

    # Load semua file
    obda = OBDAUpdater(OBDA_PATH)
    ttl  = TTLUpdater(TTL_PATH)
    vdb  = VDBUpdater(VDB_PATH) if vdb_model else None

    obda_changed = False
    ttl_changed  = False
    vdb_changed  = False

    # ── P-003: RENAME COLUMN ─────────────────────────────────
    if alter_type == 'RENAME COLUMN':
        old_col = parsed['old_column']
        new_col = parsed['new_column']

        obda_changed = obda.alias_column(mapping_id, old_col, new_col)

        if vdb:
            vdb_changed = vdb.rename_column(vdb_model, table_name, old_col, new_col)

    # ── P-002: DROP COLUMN ───────────────────────────────────
    elif alter_type == 'DROP COLUMN':
        col_name      = parsed['column_name']
        property_name = f'{column_to_property(col_name)}'

        obda_changed = obda.remove_property(mapping_id,
                                             f'bansos:{property_name}', col_name)
        ttl_changed  = ttl.deprecate_property(f'bansos:{property_name}')

        if vdb:
            vdb_changed = vdb.drop_column(vdb_model, table_name, col_name)

    # ── P-001: ADD COLUMN ────────────────────────────────────
    elif alter_type == 'ADD COLUMN':
        col_name      = parsed['column_name']
        col_type      = parsed['column_type'] or ''
        xsd_type      = sql_type_to_xsd(col_type)
        property_name = column_to_property(col_name)

        obda_changed = obda.add_property(mapping_id,
                                          col_name, f'bansos:{property_name}', xsd_type)
        ttl_changed  = ttl.add_datatype_property(
            f'bansos:{property_name}',
            f'bansos:{ontology_class}',
            xsd_type,
        )
        if vdb:
            vdb_changed = vdb.add_column(vdb_model, table_name, col_name, col_type)

    # ── Simpan file yang berubah ──────────────────────────────
    if not any([obda_changed, ttl_changed, vdb_changed]):
        log.info('Tidak ada perubahan, skip restart.')
        return

    if obda_changed:
        obda.save()
    if ttl_changed:
        ttl.save()
    if vdb_changed:
        vdb.save()

    # ── Restart containers ───────────────────────────────────
    if vdb_changed:
        # VDB berubah → restart Teiid dulu, lalu Ontop
        restart_all()
    else:
        # Hanya OBDA/TTL berubah → cukup restart Ontop
        restart_ontop_only()


# ─────────────────────────────────────────────────────────────
# KAFKA CONSUMER
# ─────────────────────────────────────────────────────────────

def main():
    log.info('══════════════════════════════════════════')
    log.info('  ASCAM Adaptive Mapping Engine')
    log.info('  OBDA : %s', OBDA_PATH)
    log.info('  TTL  : %s', TTL_PATH)
    log.info('  VDB  : %s', VDB_PATH)
    log.info('══════════════════════════════════════════')

    consumer = None
    for attempt in range(1, KAFKA_RETRY_ATTEMPTS + 1):
        try:
            consumer = KafkaConsumer(
                *KAFKA_TOPICS,
                bootstrap_servers  = KAFKA_BOOTSTRAP_SERVERS,
                auto_offset_reset  = 'earliest',
                enable_auto_commit = False,
                group_id           = KAFKA_GROUP_ID,
                value_deserializer = lambda x: x.decode('utf-8'),
            )
            log.info('Terhubung ke Kafka: %s', KAFKA_BOOTSTRAP_SERVERS)
            break
        except Exception as e:
            log.warning('Kafka belum siap (attempt %d/%d): %s',
                        attempt, KAFKA_RETRY_ATTEMPTS, e)
            time.sleep(KAFKA_RETRY_DELAY)
    else:
        log.error('Gagal terhubung ke Kafka.')
        return

    log.info('Mendengarkan event DDL dari topics: %s', KAFKA_TOPICS)

    for message in consumer:
        try:
            msg_dict = json.loads(message.value)
            process_event(msg_dict)
            consumer.commit()
        except json.JSONDecodeError as e:
            log.error('Gagal parse JSON: %s', e)
        except Exception as e:
            log.exception('Error proses event: %s', e)


if __name__ == '__main__':
    main()