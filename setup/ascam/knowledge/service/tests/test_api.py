"""Knowledge Service: autentikasi, registry, kredensial terenkripsi, konfigurasi deklaratif, audit."""
import uuid

import pytest
import yaml
from sqlalchemy import select, text

from ascam_knowledge.db import ops, registry
from ascam_knowledge.security.crypto import SecretBox, generate_key


def uniq(prefix='uji'):
    return f'{prefix}-{uuid.uuid4().hex[:8]}'


def new_obdf(api):
    r = api.post('/api/v1/obdf', json={'name': uniq(), 'description': 'uji'})
    assert r.status_code == 201, r.text
    return r.json()


def test_health_and_ready(api, alembic_cfg):
    from alembic.script import ScriptDirectory
    head = ScriptDirectory.from_config(alembic_cfg).get_current_head()
    assert api.get('/health').json() == {'status': 'ok'}
    body = api.get('/ready').json()
    assert body['status'] == 'ok' and body['schema_revision'] == body['expected_revision'] == head


@pytest.mark.parametrize('header', [None, 'Bearer salah', 'Basic abc'])
def test_authentication_required(api, header):
    from fastapi.testclient import TestClient
    anonymous = TestClient(api.app_ref)                     # tanpa header bawaan fixture
    headers = {'Authorization': header} if header else {}
    assert anonymous.get('/api/v1/obdf', headers=headers).status_code == 401
    assert anonymous.get('/health').status_code == 200       # liveness tetap terbuka


def test_obdf_create_list_and_conflict(api):
    o = new_obdf(api)
    assert o['active_version_no'] is None
    assert any(x['id'] == o['id'] for x in api.get('/api/v1/obdf').json())
    dup = api.post('/api/v1/obdf', json={'name': o['name']})
    assert dup.status_code == 409 and dup.json()['constraint'] == 'uq_obdf_instance_name'
    assert api.post('/api/v1/obdf', json={'name': 'Nama Tidak Valid'}).status_code == 422
    assert api.get('/api/v1/obdf/999999').status_code == 404


def test_source_default_identifier_case(api):
    o = new_obdf(api)
    pg = api.post(f"/api/v1/obdf/{o['id']}/sources",
                  json={'logical_name': 'kemensos', 'dbms': 'postgresql', 'database_name': 'kemensos'}).json()
    my = api.post(f"/api/v1/obdf/{o['id']}/sources",
                  json={'logical_name': 'dukcapil', 'dbms': 'mysql', 'database_name': 'dukcapil'}).json()
    assert (pg['identifier_case'], my['identifier_case']) == ('lower', 'insensitive')
    assert api.post(f"/api/v1/obdf/{o['id']}/sources",
                    json={'logical_name': 'x', 'dbms': 'oracle', 'database_name': 'x'}).status_code == 422


def test_credential_is_encrypted_and_never_returned(api, keys):
    o = new_obdf(api)
    secret = 'Rahasia-Uji-12345'
    r = api.post(f"/api/v1/obdf/{o['id']}/credentials",
                 json={'name': 'teiid-admin', 'username': 'admin', 'secret': secret})
    assert r.status_code == 201
    assert secret not in r.text and 'secret' not in r.json()
    listing = api.get(f"/api/v1/obdf/{o['id']}/credentials")
    assert secret not in listing.text

    with api.factory() as db:
        row = db.execute(select(registry.Credential).filter_by(id=r.json()['id'])).scalar_one()
        assert secret.encode() not in bytes(row.secret_ciphertext)
        assert SecretBox(keys).decrypt(row.secret_ciphertext) == secret
        audit_text = ' '.join(str(a.detail) for a in db.execute(
            select(ops.AuditLog).filter_by(obdf_id=o['id'])).scalars())
    assert secret not in audit_text

    rotated = api.put(f"/api/v1/credentials/{r.json()['id']}/secret", json={'secret': 'Baru-678'})
    assert rotated.status_code == 200 and rotated.json()['rotated_at'] is not None


def test_key_rotation_keeps_old_ciphertexts_readable(keys):
    old = SecretBox(keys)
    ciphertext, version = old.encrypt('lama')
    rotated = SecretBox([generate_key(), *keys])           # kunci baru di baris pertama
    assert rotated.decrypt(ciphertext) == 'lama'
    new_ct, new_version = rotated.encrypt('baru')
    assert (version, new_version) == (1, 2)
    with pytest.raises(ValueError):
        SecretBox([generate_key()]).decrypt(new_ct)        # kunci yang salah ditolak


def test_target_rules(api):
    o = new_obdf(api)
    base = f"/api/v1/obdf/{o['id']}/targets"
    ep = {'host': 'data-federation-teiid', 'port': 35432, 'options': {'vdb': 'government'}}
    assert api.post(base, json={'kind': 'teiid_odbc', 'name': 'odbc', 'endpoint': ep}).status_code == 422
    cred = api.post(f"/api/v1/obdf/{o['id']}/credentials",
                    json={'name': 'c', 'username': 'u', 'secret': 's'}).json()
    no_vdb = {**ep, 'options': {}}
    assert api.post(base, json={'kind': 'teiid_odbc', 'name': 'odbc', 'endpoint': no_vdb,
                                'credential_id': cred['id']}).status_code == 422
    assert api.post(base, json={'kind': 'kafka', 'name': 'k',
                                'endpoint': {'host': 'kafka', 'port': 70000}}).status_code == 422
    ok = api.post(base, json={'kind': 'teiid_odbc', 'name': 'odbc', 'endpoint': ep,
                              'credential_id': cred['id']})
    assert ok.status_code == 201

    other = new_obdf(api)
    foreign_cred = api.post(f"/api/v1/obdf/{other['id']}/credentials",
                            json={'name': 'c', 'username': 'u', 'secret': 's'}).json()
    assert api.post(base, json={'kind': 'teiid_odbc', 'name': 'odbc2', 'endpoint': ep,
                                'credential_id': foreign_cred['id']}).status_code == 404

    tid = ok.json()['id']
    assert api.patch(f'/api/v1/targets/{tid}', json={'enabled': False}).json()['enabled'] is False
    assert api.patch(f'/api/v1/targets/{tid}', json={'credential_id': None}).status_code == 422


def test_type_mappings_compute_owl2ql_flag(api):
    o = new_obdf(api)
    body = [
        {'dbms': 'postgresql', 'native_type': 'INTEGER', 'teiid_type': 'integer', 'xsd_datatype': 'xsd:integer'},
        {'dbms': 'postgresql', 'native_type': 'boolean', 'teiid_type': 'boolean', 'xsd_datatype': 'xsd:boolean'},
        {'dbms': 'mysql', 'native_type': 'date', 'teiid_type': 'date',
         'xsd_datatype': 'http://www.w3.org/2001/XMLSchema#date'},
    ]
    rows = api.put(f"/api/v1/obdf/{o['id']}/type-mappings", json=body).json()
    flags = {(r['dbms'], r['native_type']): r['owl2ql_compatible'] for r in rows}
    assert flags == {('postgresql', 'integer'): True, ('postgresql', 'boolean'): False, ('mysql', 'date'): False}
    wrong = [{**body[1], 'owl2ql_compatible': True}]
    assert api.put(f"/api/v1/obdf/{o['id']}/type-mappings", json=wrong).status_code == 422


def test_settings_and_naming_policy(api):
    o = new_obdf(api)
    assert api.get(f"/api/v1/obdf/{o['id']}/naming-policy").status_code == 404
    policy = {'property_iri_template': '{namespace}{column_camel}', 'on_collision': 'qualify_with_class',
              'namespace': 'http://contoh.id/ontology/'}
    assert api.put(f"/api/v1/obdf/{o['id']}/naming-policy", json=policy).status_code == 200
    api.put(f"/api/v1/obdf/{o['id']}/settings/adaptation.add_column",
            json={'value': {'mode': 'auto'}})
    settings = {s['key']: s['value'] for s in api.get(f"/api/v1/obdf/{o['id']}/settings").json()}
    assert settings == {'adaptation.add_column': {'mode': 'auto'},
                        'ontology.namespace': 'http://contoh.id/ontology/'}


CONFIG = """
version: 1
obdf: {{name: {name}, description: OBDF uji}}
sources:
  - {{logical_name: kemensos, dbms: postgresql, database_name: kemensos,
     kafka_topic: kemensos.schema_monitor.ddl_event_log}}
credentials:
  - {{name: teiid-admin, username: admin, secret_file: {secret_file}}}
targets:
  - kind: teiid_mgmt
    name: teiid-mgmt
    endpoint: {{host: data-federation-teiid, port: 9990, path: /management}}
    credential: teiid-admin
  - kind: kafka
    name: kafka
    endpoint: {{host: kafka, port: 9092}}
settings:
  adaptation.add_column: {{mode: auto}}
naming_policy:
  property_iri_template: "{{namespace}}{{column_camel}}"
  on_collision: qualify_with_class
  namespace: http://contoh.id/ontology/
type_mappings:
  - {{dbms: postgresql, native_type: varchar, teiid_type: string, xsd_datatype: "xsd:string"}}
"""


def test_config_apply_is_idempotent_and_detects_secret_change(api, tmp_path):
    secret_file = tmp_path / 'teiid_admin'
    secret_file.write_text('rahasia-1\n')
    name = uniq('cfg')
    doc = yaml.safe_load(CONFIG.format(name=name, secret_file=secret_file))

    first = api.post('/api/v1/config/apply', json=doc).json()
    # obdf, source, credential, 2 target, 2 setting (termasuk ontology.namespace), naming_policy, type_mapping
    assert len(first['created']) == 9 and not first['updated']
    second = api.post('/api/v1/config/apply', json=doc).json()
    assert not second['created'] and not second['updated'] and len(second['unchanged']) == 9

    secret_file.write_text('rahasia-2\n')
    doc['targets'][1]['enabled'] = False
    third = api.post('/api/v1/config/apply', json=doc).json()
    assert sorted(third['updated']) == ['credential:teiid-admin', 'target:kafka']

    bad = yaml.safe_load(CONFIG.format(name=uniq('cfg'), secret_file=secret_file))
    bad['targets'][0]['credential'] = 'tidak-ada'
    r = api.post('/api/v1/config/apply', json=bad)
    assert r.status_code == 422
    assert not any(o['name'] == bad['obdf']['name'] for o in api.get('/api/v1/obdf').json())  # di-rollback

    both = yaml.safe_load(CONFIG.format(name=uniq('cfg'), secret_file=secret_file))
    both['credentials'][0]['secret'] = 'x'
    assert api.post('/api/v1/config/apply', json=both).status_code == 422


def test_bootstrap_on_startup(migrated, alembic_cfg, db_url, keys, tmp_path):
    from fastapi.testclient import TestClient
    from ascam_knowledge.api.app import create_app
    from ascam_knowledge.db.session import make_session_factory
    from ascam_knowledge.security.auth import TokenRegistry
    secret_file = tmp_path / 's'
    secret_file.write_text('x')
    name = uniq('boot')
    cfg = tmp_path / 'obdf.yaml'
    cfg.write_text(CONFIG.format(name=name, secret_file=secret_file))
    factory = make_session_factory(db_url)
    app = create_app(session_factory=factory, secret_box=SecretBox(keys),
                     tokens=TokenRegistry(['ui:t']), bootstrap_config=str(cfg))
    with TestClient(app) as client:
        names = [o['name'] for o in client.get('/api/v1/obdf', headers={'Authorization': 'Bearer t'}).json()]
    assert name in names
    with factory() as db:
        actors = db.execute(text("SELECT DISTINCT actor FROM ops.audit_log a "
                                 "JOIN registry.obdf_instance o ON o.id = a.obdf_id WHERE o.name = :n"),
                            {'n': name}).scalars().all()
    assert actors == ['system:bootstrap']
    factory.kw['bind'].dispose()


def test_audit_records_actor(api):
    o = new_obdf(api)
    entries = api.get(f"/api/v1/obdf/{o['id']}/audit").json()
    assert entries[0]['actor'] == 'ui:penguji' and entries[0]['action'] == 'create'


def test_ready_as_application_role(migrated, db_url, alembic_cfg, keys):
    """Layanan berjalan sebagai role DML (D4); /ready harus tetap dapat membaca revisi skema."""
    from alembic import command
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from ascam_knowledge.api.app import create_app
    from ascam_knowledge.db.session import make_session_factory
    from ascam_knowledge.security.auth import TokenRegistry

    admin = create_engine(db_url)
    with admin.connect() as conn:
        can_create = conn.execute(text('SELECT rolsuper OR rolcreaterole FROM pg_roles '
                                       'WHERE rolname = current_user')).scalar()
        if not can_create:
            admin.dispose()
            pytest.skip('role pengujian tidak dapat membuat role')
        exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'ascam_app'")).scalar()
        if not exists:
            conn.execute(text("CREATE ROLE ascam_app LOGIN PASSWORD 'uji-app'"))
        conn.execute(text("ALTER ROLE ascam_app PASSWORD 'uji-app'"))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{make_url(db_url).database}" TO ascam_app'))
        conn.commit()
    admin.dispose()
    command.downgrade(alembic_cfg, 'base')           # hak diberikan saat migrasi, bila role ada
    command.upgrade(alembic_cfg, 'head')

    app_url = make_url(db_url).set(username='ascam_app', password='uji-app')
    factory = make_session_factory(app_url)
    app = create_app(session_factory=factory, secret_box=SecretBox(keys), tokens=TokenRegistry(['ui:t']))
    with TestClient(app) as client:
        assert client.get('/ready').status_code == 200
        auth = {'Authorization': 'Bearer t'}
        created = client.post('/api/v1/obdf', json={'name': uniq('app')}, headers=auth)
        assert created.status_code == 201                       # DML berjalan sebagai ascam_app
    factory.kw['bind'].dispose()
