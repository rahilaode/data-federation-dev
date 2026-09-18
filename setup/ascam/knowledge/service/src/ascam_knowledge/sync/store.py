"""Menyimpan struktur ℳ dan 𝒯 hasil penguraian ke dalam satu versi spesifikasi."""
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..db import spec
from . import mapping as r2rml
from . import ontology as owl_parser
from . import sql_analysis


@dataclass
class SigmaIndex:
    """Indeks Σ_S versi berjalan untuk menyelesaikan nama tabel dan kolom."""
    tables: dict[str, int] = field(default_factory=dict)          # "model.tabel" -> id
    columns: dict[str, int] = field(default_factory=dict)         # "model.tabel.kolom" -> id
    table_columns: dict[str, list[str]] = field(default_factory=dict)

    def table(self, reference: str) -> int | None:
        key = reference.replace('"', '').replace('`', '').strip().lower()
        return self.tables.get(key)

    def column(self, table_key: str, column: str) -> int | None:
        return self.columns.get(f'{table_key}.{column}'.lower())


def build_index(db: Session, version_id: int) -> SigmaIndex:
    index = SigmaIndex()
    rows = db.execute(
        spec.TeiidColumn.__table__.select().with_only_columns(
            spec.TeiidColumn.id, spec.TeiidColumn.name, spec.TeiidColumn.table_id)
        .where(spec.TeiidColumn.spec_version_id == version_id)).all()
    tables = db.execute(
        spec.TeiidTable.__table__.select().with_only_columns(
            spec.TeiidTable.id, spec.TeiidTable.name, spec.TeiidTable.model_id)
        .where(spec.TeiidTable.spec_version_id == version_id)).all()
    models = dict(db.execute(
        spec.TeiidModel.__table__.select().with_only_columns(spec.TeiidModel.id, spec.TeiidModel.name)
        .where(spec.TeiidModel.spec_version_id == version_id)).all())
    table_key_by_id = {}
    for table_id, name, model_id in tables:
        key = f'{models[model_id]}.{name}'.lower()
        index.tables[key] = table_id
        index.tables[name.lower()] = index.tables.get(name.lower(), table_id)   # tanpa nama model
        table_key_by_id[table_id] = key
        index.table_columns[key] = []
    for column_id, name, table_id in rows:
        key = table_key_by_id.get(table_id)
        if key:
            index.columns[f'{key}.{name}'.lower()] = column_id
            index.table_columns[key].append(name)
    return index


def store_mapping(db: Session, version_id: int, artifact_id: int, content: str,
                  index: SigmaIndex, issues: list[dict]) -> dict[str, int]:
    specs = r2rml.parse(content)
    counts = {'triples_maps': 0, 'logical_columns': 0, 'term_maps': 0, 'poms': 0}

    for tm in specs:
        analysis = (sql_analysis.analyze(tm.sql_query) if tm.logical_table_kind == 'sql_query'
                    else sql_analysis.SqlAnalysis(parse_status='ok'))
        row = spec.TriplesMap(spec_version_id=version_id, artifact_id=artifact_id, iri=tm.iri,
                              logical_table_kind=tm.logical_table_kind, table_name=tm.table_name,
                              sql_query=tm.sql_query,
                              sql_parse_status=analysis.parse_status if tm.sql_query else 'not_applicable')
        db.add(row)
        db.flush()
        counts['triples_maps'] += 1
        if tm.sql_query and analysis.parse_status == 'failed':
            issues.append({'code': 'sql_unparsed', 'severity': 'warning', 'subject_kind': 'triples_map',
                           'subject_ref': tm.iri,
                           'message': 'kueri logical table tidak dapat diurai; perubahan kolom '
                                      'pada tabel yang dirujuk diperlakukan konservatif'})

        # sumber logical table
        alias_to_table: dict[str | None, tuple[str, int | None]] = {}
        references = ([{'table': tm.table_name, 'alias': None}] if tm.logical_table_kind == 'table'
                      else analysis.sources)
        for source in references:
            reference = source['table'] or ''
            table_id = index.table(reference)
            key = reference.replace('"', '').replace('`', '').strip().lower()
            alias_to_table[source['alias']] = (key, table_id)
            if table_id is not None and None not in alias_to_table:
                alias_to_table[None] = (key, table_id)
            db.add(spec.LogicalSource(spec_version_id=version_id, triples_map_id=row.id,
                                      teiid_table_id=table_id, reference_name=reference,
                                      alias=source['alias']))
            if table_id is None:
                issues.append({'code': 'logical_source_unresolved', 'severity': 'warning',
                               'subject_kind': 'triples_map', 'subject_ref': tm.iri,
                               'message': f'tabel {reference} pada logical table tidak ditemukan di Σ_S'})
        if len(alias_to_table) == 1:
            alias_to_table[None] = next(iter(alias_to_table.values()))

        def resolve(alias, column):
            key, table_id = alias_to_table.get(alias) or alias_to_table.get(None) or (None, None)
            return index.column(key, column) if key else None

        # kolom logical table
        logical_ids: dict[str, int] = {}
        projections = analysis.projections
        if tm.logical_table_kind == 'table':
            key, table_id = alias_to_table.get(None, (None, None))
            projections = [sql_analysis.Projection(name=name, kind='passthrough', sources=[(None, name)])
                           for name in index.table_columns.get(key or '', [])]
        expanded = []
        for projection in projections:
            if projection.kind == 'star':
                key, table_id = alias_to_table.get(None, (None, None))
                names = index.table_columns.get(key or '', [])
                expanded += [sql_analysis.Projection(name=name, kind='star', sources=[(None, name)])
                             for name in names]
                if not names:
                    issues.append({'code': 'logical_column_unresolved', 'severity': 'warning',
                                   'subject_kind': 'triples_map', 'subject_ref': tm.iri,
                                   'message': 'SELECT * tidak dapat diperluas: tabel sumber tidak dikenal'})
            else:
                expanded.append(projection)
        for projection in expanded:
            logical = spec.LogicalColumn(spec_version_id=version_id, triples_map_id=row.id,
                                         name=projection.name, expression_kind=projection.kind,
                                         expression_sql=projection.expression_sql)
            db.add(logical)
            db.flush()
            logical_ids[projection.name] = logical.id
            counts['logical_columns'] += 1
            for alias, column in projection.sources or [(None, projection.name)]:
                column_id = resolve(alias, column)
                if column_id:
                    db.add(spec.LogicalColumnSource(spec_version_id=version_id,
                                                    logical_column_id=logical.id,
                                                    teiid_column_id=column_id))

        for reference in analysis.references:
            column_id = resolve(reference['alias'], reference['column'])
            if column_id:
                db.add(spec.SqlReference(spec_version_id=version_id, triples_map_id=row.id,
                                         teiid_column_id=column_id, clause=reference['clause']))

        def add_term_map(term: r2rml.TermMapSpec, pom_id: int | None):
            term_row = spec.TermMap(spec_version_id=version_id, triples_map_id=row.id, pom_id=pom_id,
                                    position=term.position, value_kind=term.value_kind,
                                    value=term.value, term_type=term.term_type,
                                    datatype=term.datatype, language=term.language)
            db.add(term_row)
            db.flush()
            counts['term_maps'] += 1
            for position, column in enumerate(term.columns, start=1):
                logical_id = logical_ids.get(column)
                if logical_id is None:
                    issues.append({'code': 'logical_column_unresolved', 'severity': 'error',
                                   'subject_kind': 'term_map', 'subject_ref': f'{tm.iri} {term.value}',
                                   'message': f'kolom {column} tidak ada pada logical table'})
                    continue
                db.add(spec.TermMapColumn(spec_version_id=version_id, term_map_id=term_row.id,
                                          logical_column_id=logical_id,
                                          template_position=position if term.value_kind == 'template' else None))
            for join in term.joins:
                db.add(spec.JoinCondition(spec_version_id=version_id, term_map_id=term_row.id,
                                          child_column=join['child'], parent_column=join['parent'],
                                          parent_triples_map_iri=term.value))
            return term_row

        if tm.subject:
            add_term_map(tm.subject, None)
        for graph_map in tm.graphs:
            add_term_map(graph_map, None)
        for class_iri in tm.classes:
            db.add(spec.SubjectClass(spec_version_id=version_id, triples_map_id=row.id,
                                     class_iri=class_iri))
        for pom in tm.poms:
            pom_row = spec.PredicateObjectMap(spec_version_id=version_id, triples_map_id=row.id,
                                              signature=pom.signature)
            db.add(pom_row)
            db.flush()
            counts['poms'] += 1
            for term in pom.predicates + pom.objects + pom.graphs:
                add_term_map(term, pom_row.id)
    db.flush()
    return counts


def store_ontology(db: Session, version_id: int, artifact_id: int, content: str,
                   issues: list[dict]) -> dict[str, int]:
    parsed = owl_parser.parse(content)
    ontology_row = spec.Ontology(spec_version_id=version_id, artifact_id=artifact_id,
                                 iri=parsed.iri or 'urn:ascam:ontology',
                                 version_iri=parsed.version_iri, version_info=parsed.version_info)
    db.add(ontology_row)
    db.flush()
    for imported in parsed.imports:
        db.add(spec.OntImport(spec_version_id=version_id, ontology_id=ontology_row.id,
                              imported_iri=imported))

    for entity in parsed.entities:
        row = spec.OntEntity(spec_version_id=version_id, ontology_id=ontology_row.id, iri=entity.iri,
                             kind=entity.kind, declared_in='local', deprecated=entity.deprecated,
                             functional=entity.functional)
        db.add(row)
        db.flush()
        for expression, simple in entity.domains:
            db.add(spec.OntDomain(spec_version_id=version_id, entity_id=row.id,
                                  class_expression=expression, is_simple=simple))
            if not simple:
                issues.append({'code': 'owl2ql_profile_violation', 'severity': 'warning',
                               'subject_kind': 'ont_entity', 'subject_ref': entity.iri,
                               'message': 'domain berupa ekspresi kelas kompleks'})
        for range_iri, compatible in entity.ranges:
            db.add(spec.OntRange(spec_version_id=version_id, entity_id=row.id, range_iri=range_iri,
                                 owl2ql_compatible=compatible))
            if compatible is False:
                issues.append({'code': 'owl2ql_profile_violation', 'severity': 'warning',
                               'subject_kind': 'ont_entity', 'subject_ref': entity.iri,
                               'message': f'range {range_iri} di luar datatype OWL 2 QL'})
        for relation, other in entity.relations:
            db.add(spec.OntPropertyRelation(spec_version_id=version_id, entity_id=row.id,
                                            relation=relation, other_iri=other))
        for annotation in entity.annotations:
            db.add(spec.OntAnnotation(spec_version_id=version_id, entity_id=row.id, **annotation))
    db.flush()
    return {'ontologies': 1, 'entities': len(parsed.entities), 'imports': len(parsed.imports)}
