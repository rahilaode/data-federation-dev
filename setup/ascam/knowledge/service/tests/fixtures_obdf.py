"""Data tiruan yang meniru struktur metadata OBDF studi kasus (Teiid 16 + agen Ontop)."""
from datetime import datetime

MAPPING_TTL = """@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix bansos: <http://bansos.go.id/ontology/> .

<#MapPenduduk> a rr:TriplesMap ;
    rr:logicalTable [ rr:sqlQuery "SELECT p.nik, p.tanggal_lahir, UCASE(p.nik) AS nik_upper FROM dukcapil.master_penduduk AS p WHERE p.tanggal_lahir IS NOT NULL" ] ;
    rr:subjectMap [ rr:template "http://bansos.go.id/resource/penduduk/{nik}" ; rr:class bansos:Penduduk ] ;
    rr:predicateObjectMap [ rr:predicate bansos:tanggalLahir ;
        rr:objectMap [ rr:column "tanggal_lahir" ; rr:datatype xsd:date ] ] ;
    rr:predicateObjectMap [ rr:predicate bansos:nikUpper ; rr:objectMap [ rr:column "nik_upper" ] ] .

<#MapPenerima> a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "kemensos.penerima_manfaat" ] ;
    rr:subjectMap [ rr:template "http://bansos.go.id/resource/penerima/{penerima_id}" ;
        rr:class bansos:PenerimaBansos ] ;
    rr:predicateObjectMap [ rr:predicate bansos:nik ; rr:objectMap [ rr:column "nik" ] ] ;
    rr:predicateObjectMap [ rr:predicate bansos:memilikDataKependudukan ;
        rr:objectMap [ rr:parentTriplesMap <#MapPenduduk> ;
                       rr:joinCondition [ rr:child "nik" ; rr:parent "nik" ] ] ] .

<#MapView> a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "v.penduduk_ringkas" ] ;
    rr:subjectMap [ rr:template "http://bansos.go.id/resource/vp/{nik}" ] ;
    rr:predicateObjectMap [ rr:predicate bansos:nik ; rr:objectMap [ rr:column "nik" ] ] .

<#MapRingkas> a rr:TriplesMap ;
    rr:logicalTable [ rr:sqlQuery "SELECT * FROM kemensos.penerima_manfaat" ] ;
    rr:subjectMap [ rr:template "http://bansos.go.id/resource/ringkas/{penerima_id}/{nik}" ] ;
    rr:predicateObjectMap [ rr:predicate bansos:statusEkonomi ;
        rr:objectMap [ rr:column "status_ekonomi" ] ] .
"""

ONTOLOGY_TTL = """@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix bansos: <http://bansos.go.id/ontology/> .

<http://bansos.go.id/ontology> a owl:Ontology ; owl:versionInfo "1.0.1" .
bansos:Penduduk a owl:Class .
bansos:PenerimaBansos a owl:Class .
bansos:nik a owl:DatatypeProperty ; rdfs:domain bansos:Penduduk ; rdfs:range xsd:string ;
    rdfs:label "NIK"@id .
bansos:tanggalLahir a owl:DatatypeProperty ; rdfs:domain bansos:Penduduk ; rdfs:range xsd:date .
bansos:statusEkonomi a owl:DatatypeProperty ; rdfs:domain bansos:PenerimaBansos ;
    rdfs:range xsd:string ; owl:deprecated true .
bansos:nikLama a owl:DatatypeProperty ; rdfs:subPropertyOf bansos:nik ; rdfs:range xsd:string .
bansos:memilikDataKependudukan a owl:ObjectProperty ; rdfs:range bansos:Penduduk .
"""


def vdb_info(validity_errors=()):
    return {
        'vdb-name': 'government', 'vdb-version': '1', 'status': 'ACTIVE',
        'connection-type': 'BY_VERSION',
        'properties': [{'property-name': 'deployment-name', 'property-value': 'government-vdb.xml'}],
        'models': [
            {'model-name': 'dukcapil', 'model-type': 'PHYSICAL', 'visible': True,
             'metadata-status': 'LOADED',
             'source-mappings': [{'source-name': 'dukcapil', 'jndi-name': 'java:/mysql-dukcapil',
                                  'translator-name': 'mysql5'}],
             'validity-errors': list(validity_errors)},
            {'model-name': 'kemensos', 'model-type': 'PHYSICAL', 'visible': True,
             'metadata-status': 'LOADED',
             'source-mappings': [{'source-name': 'kemensos', 'jndi-name': 'java:/pgsql-kemensos',
                                  'translator-name': 'postgresql'}],
             'validity-errors': []},
            {'model-name': 'v', 'model-type': 'VIRTUAL', 'visible': True, 'source-mappings': [],
             'validity-errors': []},
        ],
    }


def metadata(extra_column=False):
    columns = [
        {'SchemaName': 'dukcapil', 'TableName': 'master_penduduk', 'Name': 'nik', 'Position': 1,
         'NameInSource': None, 'DataType': 'string', 'NullType': 'No Nulls', 'Length': 16,
         'Precision': 0, 'Scale': 0, 'UID': 'c1'},
        {'SchemaName': 'dukcapil', 'TableName': 'master_penduduk', 'Name': 'tanggal_lahir',
         'Position': 2, 'NameInSource': '`tgl_lahir_ktp`', 'DataType': 'date', 'NullType': 'Nullable',
         'Length': 0, 'Precision': 0, 'Scale': 0, 'UID': 'c2'},
        {'SchemaName': 'v', 'TableName': 'penduduk_ringkas', 'Name': 'nik', 'Position': 1,
         'NameInSource': None, 'DataType': 'string', 'NullType': 'No Nulls', 'Length': 16,
         'Precision': 0, 'Scale': 0, 'UID': 'c3'},
        {'SchemaName': 'kemensos', 'TableName': 'penerima_manfaat', 'Name': 'penerima_id',
         'Position': 1, 'NameInSource': None, 'DataType': 'integer', 'NullType': 'No Nulls',
         'Length': 0, 'Precision': 0, 'Scale': 0, 'UID': 'c10'},
        {'SchemaName': 'kemensos', 'TableName': 'penerima_manfaat', 'Name': 'nik', 'Position': 2,
         'NameInSource': None, 'DataType': 'string', 'NullType': 'No Nulls', 'Length': 16,
         'Precision': 0, 'Scale': 0, 'UID': 'c11'},
        {'SchemaName': 'kemensos', 'TableName': 'penerima_manfaat', 'Name': 'status_ekonomi',
         'Position': 3, 'NameInSource': None, 'DataType': 'string', 'NullType': 'Nullable',
         'Length': 50, 'Precision': 0, 'Scale': 0, 'UID': 'c12'},
    ]
    if extra_column:
        columns.append({'SchemaName': 'dukcapil', 'TableName': 'master_penduduk', 'Name': 'email',
                        'Position': 3, 'NameInSource': None, 'DataType': 'string',
                        'NullType': 'Nullable', 'Length': 100, 'Precision': 0, 'Scale': 0, 'UID': 'c4'})
    return {
        'virtual_databases': [{'Name': 'government', 'Version': '1',
                               'LoadingTimestamp': datetime(2026, 9, 17, 1, 24),
                               'ActiveTimestamp': datetime(2026, 9, 17, 1, 24, 1)}],
        'schemas': [{'Name': 'dukcapil', 'IsPhysical': True}, {'Name': 'kemensos', 'IsPhysical': True},
                    {'Name': 'v', 'IsPhysical': False}],
        'tables': [
            {'SchemaName': 'dukcapil', 'Name': 'master_penduduk', 'Type': 'Table',
             'NameInSource': '`dukcapil`.`master_penduduk`', 'IsPhysical': True, 'UID': 't1'},
            {'SchemaName': 'v', 'Name': 'penduduk_ringkas', 'Type': 'Table', 'NameInSource': None,
             'IsPhysical': False, 'UID': 't2'},
            {'SchemaName': 'kemensos', 'Name': 'penerima_manfaat', 'Type': 'Table',
             'NameInSource': None, 'IsPhysical': True, 'UID': 't3'},
        ],
        'columns': columns,
        'keys': [{'SchemaName': 'dukcapil', 'TableName': 'master_penduduk', 'Name': 'nik',
                  'KeyName': 'PK', 'KeyType': 'Primary', 'Position': 1}],
        'views': [{'SchemaName': 'v', 'Name': 'penduduk_ringkas',
                   'Body': 'SELECT p.nik FROM dukcapil.master_penduduk AS p '
                           'WHERE p.tanggal_lahir IS NOT NULL'}],
        'usage': [
            {'UID': 't2', 'object_type': 'View', 'SchemaName': 'v', 'Name': 'penduduk_ringkas',
             'ElementName': None, 'Uses_UID': 't1', 'Uses_object_type': 'Table',
             'Uses_SchemaName': 'dukcapil', 'Uses_Name': 'master_penduduk', 'Uses_ElementName': None},
            {'UID': 't2', 'object_type': 'View', 'SchemaName': 'v', 'Name': 'penduduk_ringkas',
             'ElementName': None, 'Uses_UID': 'c2', 'Uses_object_type': 'Column',
             'Uses_SchemaName': 'dukcapil', 'Uses_Name': 'master_penduduk',
             'Uses_ElementName': 'tanggal_lahir'},
            {'UID': 'c3', 'object_type': 'Column', 'SchemaName': 'v', 'Name': 'penduduk_ringkas',
             'ElementName': 'nik', 'Uses_UID': 'c1', 'Uses_object_type': 'Column',
             'Uses_SchemaName': 'dukcapil', 'Uses_Name': 'master_penduduk', 'Uses_ElementName': 'nik'},
        ],
        'matviews': [], 'procedures': [], 'triggers': [],
    }


class FakeAdmin:
    def __init__(self, info=None, content=b'<?xml version="1.0"?><vdb name="government" version="1"/>',
                 reported_hash=None):
        self._info = info or vdb_info()
        self.content = content
        self._hash = reported_hash

    def get_vdb(self, name, version):
        return self._info

    def deployment_hash(self, deployment):
        import hashlib
        return self._hash or hashlib.sha1(self.content).hexdigest()   # noqa: S324

    def read_deployment_content(self, deployment):
        return self.content


class FakeMetadata:
    def __init__(self, data=None):
        self.data = data or metadata()

    def fetch_all(self):
        return self.data


class FakeAgent:
    def __init__(self, mapping=MAPPING_TTL, ontology=ONTOLOGY_TTL):
        import hashlib
        from ascam_knowledge.sync.clients import AgentArtifact
        self.items = [
            AgentArtifact('r2rml', 'mapping.ttl', 'text/turtle', mapping,
                          hashlib.sha256(mapping.encode()).hexdigest()),
            AgentArtifact('ontology', 'ontology_file.ttl', 'text/turtle', ontology,
                          hashlib.sha256(ontology.encode()).hexdigest()),
        ]

    def artifacts(self):
        return self.items


def clients(admin=None, meta=None, agent=None):
    return {'admin': admin or FakeAdmin(), 'metadata': meta or FakeMetadata(), 'agent': agent or FakeAgent()}
