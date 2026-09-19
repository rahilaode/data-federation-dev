"""Tiruan Knowledge, Teiid, agen, dan endpoint SPARQL untuk menguji mesin Executor."""
import hashlib

VDB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<vdb name="government" version="1">
  <model visible="true" name="kemensos">
    <source name="kemensos" translator-name="postgresql" connection-jndi-name="java:/pgsql"/>
    <metadata type="DDL"><![CDATA[
      CREATE FOREIGN TABLE penerima_manfaat (penerima_id integer not null primary key,
        nik varchar(16) not null, status_ekonomi varchar(50));
    ]]></metadata>
  </model>
</vdb>
"""
MAPPING = """@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix bansos: <http://bansos.go.id/ontology/> .

<#MapPenerima> a rr:TriplesMap ;
    rr:logicalTable [ rr:sqlQuery "SELECT * FROM kemensos.penerima_manfaat" ] ;
    rr:subjectMap [ rr:template "http://bansos.go.id/resource/penerima/{penerima_id}" ] ;
    rr:predicateObjectMap [ rr:predicate bansos:statusEkonomi ;
                            rr:objectMap [ rr:column "status_ekonomi" ] ] .
"""
ONTOLOGY = """@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix bansos: <http://bansos.go.id/ontology/> .

<http://bansos.go.id/ontology> a owl:Ontology .
bansos:PenerimaBansos a owl:Class .
bansos:statusEkonomi a owl:DatatypeProperty ; rdfs:range xsd:string .
"""
STATUS = 'http://bansos.go.id/ontology/statusEkonomi'
EMAIL = 'http://bansos.go.id/ontology/email'
TYPE_MAPPINGS = [
    {'dbms': 'postgresql', 'native_type': 'character varying', 'teiid_type': 'string',
     'xsd_datatype': 'xsd:string', 'owl2ql_compatible': True},
    {'dbms': 'postgresql', 'native_type': 'varchar', 'teiid_type': 'string',
     'xsd_datatype': 'xsd:string', 'owl2ql_compatible': True},
]


def plan_drop(plan_id=1, base=7):
    return {'id': plan_id, 'base_spec_version_id': base, 'pattern': 'P-002', 'decision': 'auto',
            'status': 'approved',
            'impact': {'targets': [{'table': 'penerima_manfaat', 'column': 'status_ekonomi'}]},
            'reasons': [],
            'actions': [
                {'seq': 1, 'artifact': 'vdb', 'operation': 'drop_column',
                 'params': {'model': 'kemensos', 'table': 'penerima_manfaat',
                            'column': 'status_ekonomi'}},
                {'seq': 2, 'artifact': 'r2rml', 'operation': 'remove_predicate_object_map',
                 'params': {'predicate_iri': STATUS}},
                {'seq': 3, 'artifact': 'ontology', 'operation': 'deprecate_property',
                 'params': {'predicate_iri': STATUS}}]}


def plan_add(plan_id=2, base=7):
    return {'id': plan_id, 'base_spec_version_id': base, 'pattern': 'P-001', 'decision': 'auto',
            'status': 'approved', 'impact': {'targets': []}, 'reasons': [],
            'actions': [
                {'seq': 1, 'artifact': 'vdb', 'operation': 'add_column',
                 'params': {'model': 'kemensos', 'table': 'penerima_manfaat', 'column': 'email',
                            'column_type': 'varchar(100)'}},
                {'seq': 2, 'artifact': 'ontology', 'operation': 'add_datatype_property',
                 'params': {'iri': EMAIL, 'column': 'email',
                            'domain': ['http://bansos.go.id/ontology/PenerimaBansos']}},
                {'seq': 3, 'artifact': 'r2rml', 'operation': 'add_predicate_object_map',
                 'params': {'predicate_iri': EMAIL, 'column': 'email',
                            'table': 'penerima_manfaat'}}]}


class FakeKnowledge:
    def __init__(self, version_id=7, vdb_version='1'):
        self.version = {'id': version_id, 'status': 'active', 'teiid_vdb_name': 'government',
                        'teiid_vdb_version': vdb_version, 'version_no': 3}
        self.steps, self.validations, self.finished = [], [], None
        self.synced = 0

    def active_version(self, obdf_id):
        return self.version

    def artifact(self, version_id, kind):
        return {'kind': kind, 'content': VDB_XML, 'sha256': hashlib.sha256(VDB_XML.encode()).hexdigest()}

    def type_mappings(self, obdf_id):
        return TYPE_MAPPINGS

    def start_execution(self, plan_id):
        return {'id': 99, 'plan_id': plan_id, 'status': 'running'}

    def add_step(self, execution_id, **step):
        self.steps.append(step)
        return {}

    def add_validation(self, execution_id, **validation):
        self.validations.append(validation)
        return {}

    def finish_execution(self, execution_id, **body):
        self.finished = body
        return {'id': execution_id, **body}

    def sync(self, obdf_id):
        self.synced += 1
        return {'changed': True, 'spec_version_id': 8, 'version_no': 4}


class FakeAdmin:
    def __init__(self, status='ACTIVE', errors=()):
        self.status, self.errors = status, list(errors)
        self.deployed, self.undeployed, self.connection_types = [], [], []

    def deploy(self, deployment, content):
        self.deployed.append((deployment, content.decode()))

    def undeploy(self, deployment):
        self.undeployed.append(deployment)

    def wait_until_settled(self, name, version, sleep=None):
        return self.status, self.errors

    def set_connection_type(self, name, version, connection_type):
        self.connection_types.append((version, connection_type))


class FakeAgent:
    def __init__(self, validate_ok=True, reload_ok=True):
        self.files = {'r2rml': MAPPING, 'ontology': ONTOLOGY}
        self.backups, self.restored, self.reloads, self.validate_calls = {}, [], 0, []
        self.validate_ok, self.reload_ok = validate_ok, reload_ok

    def artifact(self, kind):
        isi = self.files[kind]
        return {'kind': kind, 'content': isi, 'sha256': hashlib.sha256(isi.encode()).hexdigest()}

    def write(self, kind, content, expected_sha256):
        sekarang = hashlib.sha256(self.files[kind].encode()).hexdigest()
        if expected_sha256 and expected_sha256 != sekarang:
            raise RuntimeError('409 konflik sidik jari')
        backup_id = f'{kind}-cadangan-{len(self.backups)}'
        self.backups[backup_id] = self.files[kind]
        self.files[kind] = content
        return {'kind': kind, 'sha256': hashlib.sha256(content.encode()).hexdigest(),
                'backup_id': backup_id}

    def restore(self, backup_id):
        kind = backup_id.split('-')[0]
        self.files[kind] = self.backups[backup_id]
        self.restored.append(backup_id)
        return {'kind': kind, 'sha256': hashlib.sha256(self.files[kind].encode()).hexdigest()}

    def validate(self, db_url=None):
        self.validate_calls.append(db_url)
        return {'ok': self.validate_ok, 'exit_code': 0 if self.validate_ok else 1,
                'output': 'Validation completed' if self.validate_ok else 'ERROR: mapping ditolak',
                'duration_ms': 5000}

    def prune_backups(self, keep=20):
        self.pruned = keep
        return {'removed': 0, 'keep': keep}

    def reload(self):
        self.reloads += 1
        return {'ok': self.reload_ok, 'stop_ms': 800, 'ready_ms': 8000, 'total_ms': 8800,
                'error': None if self.reload_ok else 'endpoint tidak siap'}


class FakeSparql:
    def __init__(self, urutan):
        self.urutan = list(urutan)
        self.panggilan = 0

    def fingerprint(self):
        hasil = self.urutan[min(self.panggilan, len(self.urutan) - 1)]
        self.panggilan += 1
        return dict(hasil)


BASELINE = {STATUS: 15, 'http://bansos.go.id/ontology/nik': 15}
TANPA_STATUS = {'http://bansos.go.id/ontology/nik': 15}
