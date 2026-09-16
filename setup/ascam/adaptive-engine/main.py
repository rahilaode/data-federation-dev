"""
ASCAM — Adaptive Mapping Engine
Entry point: Kafka consumer + Orchestrator.

Pembagian peran menurut MAPE-K:
  Analyze : parse DDL -> klasifikasi SMO -> identifikasi artefak terdampak
  Plan    : pilih pola P-00x -> hitung T', M', Σ'_S di memori
            -> susun AdaptationPlan beserta kueri verifikasi
  Execute : diserahkan ke executor.AdaptationExecutor
            (redeploy Σ'_S -> tulis M', T' -> reload Ontop -> verifikasi)
"""

import json
import logging
import time
from datetime import datetime

from kafka import KafkaConsumer

from config.settings import (KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPICS,
                             KAFKA_GROUP_ID, KAFKA_RETRY_ATTEMPTS,
                             KAFKA_RETRY_DELAY, OBDA_PATH, TTL_PATH,
                             VDB_PATH, TABLE_TO_MAPPING_ID,
                             TABLE_TO_CLASS, TABLE_TO_VDB_MODEL,
                             ONTOLOGY_BASE, SNAPSHOT_DIR, ADAPTATION_LOG,
                             ONTOP_CONTAINER_NAME, ONTOP_SPARQL_URL,
                             TEIID_DEPLOY_TIMEOUT, TEIID_SCAN_POLL,
                             ONTOP_READY_TIMEOUT, VERIFY_TIMEOUT,
                             VERIFY_ENABLED)
from pattern_library.patterns   import get_pattern, sql_type_to_xsd, column_to_property
from pattern_library.ddl_parser import parse_ddl, extract_event
from mapping_engine.obda_updater import OBDAUpdater
from mapping_engine.ttl_updater  import TTLUpdater
from vdb_engine.vdb_updater      import VDBUpdater
from executor import (AdaptationExecutor, AdaptationPlan,
                      ArtifactChange, VerificationCheck)
from executor.verifier import count_class, count_property

logging.basicConfig(
    level   = logging.INFO,
    format  = '%(asctime)s [%(levelname)s] %(name)s — %(message)s',
    datefmt = '%Y-%m-%dT%H:%M:%S',
)
log = logging.getLogger('ascam.main')

EXECUTOR = AdaptationExecutor(
    snapshot_dir       = SNAPSHOT_DIR,
    adaptation_log     = ADAPTATION_LOG,
    all_artifact_paths = [VDB_PATH, OBDA_PATH, TTL_PATH],
    ontop_container    = ONTOP_CONTAINER_NAME,
    sparql_url         = ONTOP_SPARQL_URL,
    teiid_timeout      = TEIID_DEPLOY_TIMEOUT,
    teiid_poll         = TEIID_SCAN_POLL,
    ontop_timeout      = ONTOP_READY_TIMEOUT,
    verify_timeout     = VERIFY_TIMEOUT,
    verify_enabled     = VERIFY_ENABLED,
)


# ─────────────────────────────────────────────────────────────
# ANALYZE + PLAN
# ─────────────────────────────────────────────────────────────

def build_plan(after: dict, t_received: float, kafka_ts_ms: int | None) -> AdaptationPlan | None:
    ddl_raw = after.get('ddl_command', '')
    if not ddl_raw:
        return None
    log.info('══ Event: %s', ddl_raw[:100])

    # ── Analyze ──────────────────────────────────────────────
    parsed = parse_ddl(ddl_raw)
    if parsed is None:
        return None

    alter_type = parsed['alter_type']
    table_name = parsed['table_name']
    pattern    = get_pattern(alter_type)
    if pattern is None:
        log.warning('Tidak ada pola untuk %s', alter_type)
        return None

    mapping_id     = TABLE_TO_MAPPING_ID.get(table_name)
    ontology_class = TABLE_TO_CLASS.get(table_name, '')
    vdb_model      = TABLE_TO_VDB_MODEL.get(table_name)
    if mapping_id is None:
        log.warning('Tabel %s tidak ada di TABLE_TO_MAPPING_ID, skip.', table_name)
        return None

    log.info('Pola %s | mapping=%s | vdb_model=%s | mode=%s',
             pattern['pattern_id'], mapping_id, vdb_model, pattern['auto_mode'])

    # ── Plan: hitung F' di memori ────────────────────────────
    obda = OBDAUpdater(OBDA_PATH)
    ttl  = TTLUpdater(TTL_PATH)
    vdb  = VDBUpdater(VDB_PATH) if vdb_model else None
    obda_changed = ttl_changed = vdb_changed = False

    checks = [VerificationCheck(f'class:{ontology_class}',
                                count_class(ONTOLOGY_BASE, ontology_class),
                                expect='positive')]

    if alter_type == 'RENAME COLUMN':
        old_col, new_col = parsed['old_column'], parsed['new_column']
        prop = obda.property_for_column(mapping_id, old_col)
        all_columns = vdb.get_columns(vdb_model, table_name) if vdb else []
        obda_changed = obda.alias_column(mapping_id, old_col, new_col,
                                         all_columns=all_columns or None)
        if vdb:
            vdb_changed = vdb.rename_column(vdb_model, table_name, old_col, new_col)
        if prop:
            # Semantic preservation: property lama tetap menjawab data
            checks.append(VerificationCheck(f'property:{prop}',
                                            count_property(ONTOLOGY_BASE, prop),
                                            expect='positive'))

    elif alter_type == 'DROP COLUMN':
        col_name = parsed['column_name']
        prop     = obda.property_for_column(mapping_id, col_name) \
                   or f'bansos:{column_to_property(col_name)}'
        obda_changed = obda.remove_property(mapping_id, prop, col_name)
        ttl_changed  = ttl.deprecate_property(prop)
        if vdb:
            vdb_changed = vdb.drop_column(vdb_model, table_name, col_name)
        checks.append(VerificationCheck(f'property:{prop}',
                                        count_property(ONTOLOGY_BASE, prop),
                                        expect='zero'))

    elif alter_type == 'ADD COLUMN':
        col_name = parsed['column_name']
        col_type = parsed['column_type'] or ''
        xsd_type = sql_type_to_xsd(col_type)
        prop     = f'bansos:{column_to_property(col_name)}'
        obda_changed = obda.add_property(mapping_id, col_name, prop, xsd_type)
        ttl_changed  = ttl.add_datatype_property(prop, f'bansos:{ontology_class}', xsd_type)
        if vdb:
            vdb_changed = vdb.add_column(vdb_model, table_name, col_name, col_type)
        # Kolom baru umumnya NULL -> cukup pastikan kueri tereksekusi
        checks.append(VerificationCheck(f'property:{prop}',
                                        count_property(ONTOLOGY_BASE, prop),
                                        expect='ok'))

    if not any([obda_changed, ttl_changed, vdb_changed]):
        log.info('Tidak ada perubahan artefak, skip.')
        return None

    changes = []
    if vdb_changed:
        changes.append(ArtifactChange('vdb', VDB_PATH, vdb.render()))
    if obda_changed:
        changes.append(ArtifactChange('mapping', OBDA_PATH, obda.render()))
    if ttl_changed:
        changes.append(ArtifactChange('ontology', TTL_PATH, ttl.render()))

    timestamps = {'t_received': t_received, 't_planned': time.time()}
    t_source = _parse_captured_at(after.get('captured_at'))
    if t_source is not None:
        timestamps['t_source'] = t_source
    if kafka_ts_ms:
        timestamps['t_kafka'] = kafka_ts_ms / 1000.0

    return AdaptationPlan(
        event_id   = f"{vdb_model or 'src'}-{after.get('id', int(t_received))}",
        source     = vdb_model or '',
        table      = table_name,
        alter_type = alter_type,
        pattern_id = pattern['pattern_id'],
        changes    = changes,
        checks     = checks,
        timestamps = timestamps,
    )


def _parse_captured_at(value) -> float | None:
    """
    Debezium merepresentasikan waktu secara berbeda per konektor:
      - MySQL DATETIME        -> epoch milidetik (int)
      - PostgreSQL TIMESTAMPTZ-> string ISO-8601 (ZonedTimestamp)
    """
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return value / 1000.0
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        log.warning('captured_at tidak terbaca: %r', value)
        return None


def process_event(msg_dict: dict, kafka_ts_ms: int | None = None):
    t_received = time.time()
    after = extract_event(msg_dict)
    if after is None:
        return
    # Filter sementara di Orchestrator; idealnya dipindah ke sisi Monitor.
    if not bool(after.get('is_regulated', 0)):
        return

    plan = build_plan(after, t_received, kafka_ts_ms)
    if plan is None:
        return
    EXECUTOR.run(plan)


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
                value_deserializer = lambda x: x.decode('utf-8') if x else None,
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

    # Event diproses berurutan (satu per satu). Offset di-commit setelah
    # event selesai, termasuk bila gagal (status tercatat di adaptation_log
    # dan ditandai needs_hitl), agar event yang sama tidak diulang terus.
    for message in consumer:
        try:
            if message.value is None:          # tombstone
                continue
            process_event(json.loads(message.value), message.timestamp)
        except json.JSONDecodeError as e:
            log.error('Gagal parse JSON: %s', e)
        except Exception as e:
            log.exception('Error proses event: %s', e)
        finally:
            consumer.commit()


if __name__ == '__main__':
    main()
