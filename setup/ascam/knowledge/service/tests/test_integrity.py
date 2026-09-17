"""Perilaku integritas: segel versi, rujukan lintas versi, transisi status, append-only."""
import uuid

import pytest
from sqlalchemy import select, text

from ascam_knowledge.db import ops, registry, spec
from conftest import expect_db_error

SEALED = '55000'          # object_not_in_prerequisite_state (RAISE pada trigger)
FK = '23503'
UNIQUE = '23505'
CHECK = '23514'


def add(session, obj):
    session.add(obj)
    session.flush()
    return obj


@pytest.fixture
def obdf(session):
    return add(session, registry.ObdfInstance(name=f'uji-{uuid.uuid4().hex[:8]}'))


def new_version(session, obdf, no, origin='sync', **kw):
    return add(session, spec.SpecVersion(obdf_id=obdf.id, version_no=no, origin=origin, **kw))


def model_table_column(session, v, column='nik'):
    m = add(session, spec.TeiidModel(spec_version_id=v.id, name='dukcapil', model_type='physical'))
    t = add(session, spec.TeiidTable(spec_version_id=v.id, model_id=m.id, name='master_penduduk', kind='foreign'))
    c = add(session, spec.TeiidColumn(spec_version_id=v.id, table_id=t.id, name=column,
                                      position=1, data_type='string'))
    return m, t, c


def test_candidate_is_editable_and_sealing_freezes_content(session, obdf):
    v = new_version(session, obdf, 1)
    _, t, c = model_table_column(session, v)
    c.nullable = False
    session.flush()                                        # boleh: masih candidate

    v.status = 'active'
    session.flush()
    session.refresh(v)
    assert v.sealed_at is not None                         # diisi trigger

    def insert_after_seal():
        session.add(spec.TeiidColumn(spec_version_id=v.id, table_id=t.id, name='nama',
                                     position=2, data_type='string'))
    expect_db_error(session, SEALED, insert_after_seal)

    def update_after_seal():
        session.execute(text('UPDATE spec.teiid_column SET name = :n WHERE id = :i'), {'n': 'x', 'i': c.id})
    expect_db_error(session, SEALED, update_after_seal)

    def delete_after_seal():
        session.execute(text('DELETE FROM spec.teiid_column WHERE id = :i'), {'i': c.id})
    expect_db_error(session, SEALED, delete_after_seal)


def test_cross_version_reference_is_rejected(session, obdf):
    v1 = new_version(session, obdf, 1)
    _, t1, _ = model_table_column(session, v1)
    v2 = new_version(session, obdf, 2, parent_id=v1.id)

    def column_in_v2_pointing_to_v1_table():
        session.add(spec.TeiidColumn(spec_version_id=v2.id, table_id=t1.id, name='nik',
                                     position=1, data_type='string'))
    expect_db_error(session, FK, column_in_v2_pointing_to_v1_table)


def test_only_one_active_version_per_obdf(session, obdf):
    v1 = new_version(session, obdf, 1)
    v1.status = 'active'
    session.flush()
    v2 = new_version(session, obdf, 2, parent_id=v1.id)

    def second_active():
        session.execute(text("UPDATE spec.spec_version SET status = 'active' WHERE id = :i"), {'i': v2.id})
    expect_db_error(session, UNIQUE, second_active)

    session.execute(text("UPDATE spec.spec_version SET status = 'superseded' WHERE id = :i"), {'i': v1.id})
    session.execute(text("UPDATE spec.spec_version SET status = 'active' WHERE id = :i"), {'i': v2.id})
    active = session.execute(select(spec.SpecVersion.id).where(
        spec.SpecVersion.obdf_id == obdf.id, spec.SpecVersion.status == 'active')).scalars().all()
    assert active == [v2.id]


@pytest.mark.parametrize('path, allowed', [
    (['active', 'superseded', 'active'], True),       # rollback ke versi sebelumnya
    (['active', 'rolled_back'], True),
    (['rejected'], True),
    (['superseded'], False),                         # candidate -> superseded
    (['rejected', 'active'], False),
    (['active', 'candidate'], False),
])
def test_status_transitions(session, obdf, path, allowed):
    v = new_version(session, obdf, 1)

    def walk():
        for status in path:
            session.execute(text('UPDATE spec.spec_version SET status = :s WHERE id = :i'),
                            {'s': status, 'i': v.id})
    if allowed:
        walk()
    else:
        expect_db_error(session, SEALED, walk)


def test_new_version_must_start_as_candidate(session, obdf):
    def insert_active():
        session.add(spec.SpecVersion(obdf_id=obdf.id, version_no=1, origin='sync', status='active'))
    expect_db_error(session, SEALED, insert_active)


def test_sealed_version_attributes_are_immutable_but_note_is_not(session, obdf):
    v = new_version(session, obdf, 1)
    v.status = 'active'
    session.flush()

    def change_number():
        session.execute(text('UPDATE spec.spec_version SET version_no = 9 WHERE id = :i'), {'i': v.id})
    expect_db_error(session, SEALED, change_number)

    session.execute(text("UPDATE spec.spec_version SET note = 'catatan', "
                         "teiid_connection_type = 'ANY' WHERE id = :i"), {'i': v.id})


def test_version_rows_are_never_deleted(session, obdf):
    v = new_version(session, obdf, 1)
    expect_db_error(session, SEALED, lambda: session.execute(
        text('DELETE FROM spec.spec_version WHERE id = :i'), {'i': v.id}))


def test_purge_removes_content_but_keeps_tombstone(session, obdf):
    v1 = new_version(session, obdf, 1)
    model_table_column(session, v1)
    v1.status = 'active'
    session.flush()
    v2 = new_version(session, obdf, 2, parent_id=v1.id)
    model_table_column(session, v2)
    purge = text('SELECT spec.purge_version(:i)')

    expect_db_error(session, SEALED, lambda: session.execute(purge, {'i': v1.id}))   # masih active

    session.execute(text("UPDATE spec.spec_version SET status = 'superseded' WHERE id = :i"), {'i': v1.id})
    session.execute(text("UPDATE spec.spec_version SET status = 'active' WHERE id = :i"), {'i': v2.id})
    expect_db_error(session, SEALED, lambda: session.execute(purge, {'i': v1.id}))   # tanpa izin

    def purge_by_status_update():
        session.execute(text("UPDATE spec.spec_version SET status = 'purged' WHERE id = :i"), {'i': v1.id})
    expect_db_error(session, SEALED, purge_by_status_update)                         # jalan pintas ditolak

    session.execute(text("SET LOCAL ascam.allow_purge = 'on'"))
    removed = session.execute(purge, {'i': v1.id}).scalar()
    assert removed == 3                                              # model + tabel + kolom
    left_v1 = session.execute(text(
        'SELECT (SELECT count(*) FROM spec.teiid_model WHERE spec_version_id = :i) + '
        '(SELECT count(*) FROM spec.teiid_table WHERE spec_version_id = :i) + '
        '(SELECT count(*) FROM spec.teiid_column WHERE spec_version_id = :i)'), {'i': v1.id}).scalar()
    assert left_v1 == 0
    status, parent_of_v2 = session.execute(text(
        'SELECT (SELECT status FROM spec.spec_version WHERE id = :a), '
        '(SELECT parent_id FROM spec.spec_version WHERE id = :b)'), {'a': v1.id, 'b': v2.id}).one()
    assert (status, parent_of_v2) == ('purged', v1.id)              # tombstone dan rujukan tetap
    left_v2 = session.execute(text('SELECT count(*) FROM spec.teiid_column WHERE spec_version_id = :i'),
                              {'i': v2.id}).scalar()
    assert left_v2 == 1                                              # versi lain tidak tersentuh


def test_ops_is_append_only_and_audit_is_immutable(session, obdf):
    ev = add(session, ops.SchemaEvent(event_uid=uuid.uuid4(), obdf_id=obdf.id, raw_message={'a': 1}))
    ev.status = 'ignored'
    session.flush()                                        # status boleh berpindah

    def delete_event():
        session.execute(text('DELETE FROM ops.schema_event WHERE id = :i'), {'i': ev.id})
    expect_db_error(session, SEALED, delete_event)

    log = add(session, ops.AuditLog(actor='admin', action='uji'))

    def update_audit():
        session.execute(text("UPDATE ops.audit_log SET actor = 'lain' WHERE id = :i"), {'i': log.id})
    expect_db_error(session, SEALED, update_audit)


def test_event_uid_is_unique(session, obdf):
    uid = uuid.uuid4()
    add(session, ops.SchemaEvent(event_uid=uid, obdf_id=obdf.id, raw_message={}))
    expect_db_error(session, UNIQUE, lambda: session.add(
        ops.SchemaEvent(event_uid=uid, obdf_id=obdf.id, raw_message={})))


def test_check_constraints(session, obdf):
    expect_db_error(session, CHECK, lambda: session.add(
        registry.Target(obdf_id=obdf.id, kind='ftp', name='x', endpoint={})))
    v = new_version(session, obdf, 1)
    expect_db_error(session, CHECK, lambda: session.add(
        spec.TeiidModel(spec_version_id=v.id, name='m', model_type='hybrid')))


def test_plan_link_and_active_version_view(session, obdf):
    v1 = new_version(session, obdf, 1)
    v1.status = 'active'
    session.flush()
    ev = add(session, ops.SchemaEvent(event_uid=uuid.uuid4(), obdf_id=obdf.id, raw_message={}))
    plan = add(session, ops.AdaptationPlan(event_id=ev.id, base_spec_version_id=v1.id,
                                           pattern='P-003', decision='auto', status='approved'))
    v2 = new_version(session, obdf, 2, parent_id=v1.id, origin='adaptation', plan_id=plan.id)
    assert v2.plan_id == plan.id

    def unknown_plan():
        session.add(spec.SpecVersion(obdf_id=obdf.id, version_no=3, origin='adaptation', plan_id=999999))
    expect_db_error(session, FK, unknown_plan)

    row = session.execute(text('SELECT spec_version_id, version_no FROM spec.active_version '
                               'WHERE obdf_id = :o'), {'o': obdf.id}).one()
    assert tuple(row) == (v1.id, 1)


def test_lineage_row_across_tables_of_same_version(session, obdf):
    v = new_version(session, obdf, 1)
    _, _, col = model_table_column(session, v)
    tm = add(session, spec.TriplesMap(spec_version_id=v.id, iri='urn:tm', logical_table_kind='table',
                                      table_name='dukcapil.master_penduduk', sql_parse_status='not_applicable'))
    ont = add(session, spec.Ontology(spec_version_id=v.id, iri='http://contoh/ont'))
    ent = add(session, spec.OntEntity(spec_version_id=v.id, ontology_id=ont.id, iri='http://contoh/ont/nik',
                                      kind='datatype_property'))
    usage = add(session, spec.ColumnUsage(spec_version_id=v.id, foreign_column_id=col.id,
                                          triples_map_id=tm.id, role='literal_value',
                                          predicate_iri=ent.iri, predicate_entity_id=ent.id,
                                          weakest_link='direct'))
    assert usage.path == []


def test_application_role_cannot_purge_even_with_setting(session, obdf):
    """D4: role aplikasi dapat membaca/menulis, tetapi purge hanya untuk pemilik skema."""
    can_create = session.execute(text(
        'SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user')).scalar()
    if not can_create:
        pytest.skip('role pengujian tidak dapat membuat role')
    v1 = new_version(session, obdf, 1)
    v1.status = 'active'
    session.flush()
    v2 = new_version(session, obdf, 2, parent_id=v1.id)
    session.execute(text("UPDATE spec.spec_version SET status = 'superseded' WHERE id = :i"), {'i': v1.id})
    session.execute(text("UPDATE spec.spec_version SET status = 'active' WHERE id = :i"), {'i': v2.id})

    session.execute(text('CREATE ROLE ascam_app_uji NOLOGIN'))          # transaksional, ikut di-rollback
    session.execute(text('GRANT ascam_app_uji TO CURRENT_USER'))        # PG16: izin SET ROLE eksplisit
    session.execute(text('GRANT USAGE ON SCHEMA registry, spec, ops TO ascam_app_uji'))
    session.execute(text('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA registry, spec, ops '
                         'TO ascam_app_uji'))
    session.execute(text('GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA spec, ops TO ascam_app_uji'))
    session.execute(text('SET LOCAL ROLE ascam_app_uji'))
    session.execute(text("SET LOCAL ascam.allow_purge = 'on'"))
    assert session.execute(text('SELECT count(*) FROM spec.spec_version WHERE id = :i'),
                           {'i': v1.id}).scalar() == 1               # role aplikasi dapat membaca
    expect_db_error(session, SEALED, lambda: session.execute(
        text('SELECT spec.purge_version(:i)'), {'i': v1.id}))
    session.execute(text('RESET ROLE'))
