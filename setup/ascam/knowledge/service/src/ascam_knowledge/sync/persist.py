"""Menyimpan struktur ℳ dan 𝒯 hasil penguraian ke versi spesifikasi."""
from sqlalchemy.orm import Session

from ..db import spec as spec_db
from . import mapping as mapping_parser
from . import ontology as ontology_parser


def _strip(value: str) -> str:
    value = value.strip()
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in '"`' else value


def _table_key(reference: str) -> str:
    return '.'.join(_strip(part) for part in reference.split('.'))


def persist_mapping(db: Session, version_id: int, artifact_id: int, content: str,
                    table_ids: dict[str, int], column_ids: dict[str, int],
                    columns_by_table: dict[str, list[str]], issues: list[dict]) -> dict[str, int]:
    specs = mapping_parser.parse(content)
    counts = {'triples_maps': 0, 'logical_columns': 0, 'term_maps': 0, 'sql_references': 0}

    for tm in specs:
        row = spec_db.TriplesMap(spec_version_id=version_id, artifact_id=artifact_id, iri=tm.iri,
                                 logical_table_kind=tm.logical_table_kind,
                                 table_name=tm.table_name, sql_query=tm.sql_query,
                                 sql_parse_status=tm.sql_parse_status)
        db.add(row)
        db.flush()
        counts['triples_maps'] += 1
        if tm.sql_parse_status == 'failed':
            issues.append({'code': 'sql_unparsed', 'severity': 'warning', 'subject_kind': 'triples_map',
                           'subject_ref': tm.iri,
                           'message': 'kueri logical table tidak dapat diurai; perubahan kolom '
                                      'tabel yang dirujuk diperlakukan konservatif'})

        alias_to_table: dict[str | None, str] = {}
        for source in tm.sources:
            key = _table_key(source['reference_name'])
            teiid_table_id = table_ids.get(key)
            db.add(spec_db.LogicalSource(spec_version_id=version_id, triples_map_id=row.id,
                                         teiid_table_id=teiid_table_id,
                                         reference_name=source['reference_name'],
                                         alias=source.get('alias')))
            if teiid_table_id is None:
                issues.append({'code': 'logical_source_unresolved', 'severity': 'error',
                               'subject_kind': 'triples_map', 'subject_ref': tm.iri,
                               'message': f"tabel {source['reference_name']} tidak ada di Σ_S"})
            else:
                alias_to_table[source.get('alias')] = key
                alias_to_table.setdefault(None, key)

        # kolom logis: hasil proyeksi SQL, atau seluruh kolom tabel bila rr:tableName / SELECT *
        logical_ids: dict[str, int] = {}

        def add_logical(name: str, kind: str, expression: str | None,
                        sources: list[tuple[str | None, str]]):
            column = spec_db.LogicalColumn(spec_version_id=version_id, triples_map_id=row.id,
                                           name=name, expression_kind=kind, expression_sql=expression)
            db.add(column)
            db.flush()
            logical_ids[name] = column.id
            counts['logical_columns'] += 1
            for alias, source_name in sources:
                key = alias_to_table.get(alias) or alias_to_table.get(None)
                teiid_column_id = column_ids.get(f'{key}.{source_name}') if key else None
                if teiid_column_id is None:
                    issues.append({'code': 'logical_column_unresolved', 'severity': 'warning',
                                   'subject_kind': 'logical_column', 'subject_ref': f'{tm.iri}#{name}',
                                   'message': f'kolom {source_name} tidak dapat ditautkan ke Σ_S'})
                    continue
                db.add(spec_db.LogicalColumnSource(spec_version_id=version_id,
                                                   logical_column_id=column.id,
                                                   teiid_column_id=teiid_column_id))

        if tm.logical_table_kind == 'table' or any(c.expression_kind == 'star' for c in tm.logical_columns):
            key = alias_to_table.get(None)
            for column_name in columns_by_table.get(key, []):
                add_logical(column_name, 'star' if tm.logical_table_kind == 'sql_query' else 'passthrough',
                            None, [(None, column_name)])
        for column in tm.logical_columns:
            if column.expression_kind == 'star' or column.name in logical_ids:
                continue
            add_logical(column.name, column.expression_kind, column.expression_sql, column.source_columns)

        for alias, column_name, clause in tm.sql_references:
            key = alias_to_table.get(alias) or alias_to_table.get(None)
            teiid_column_id = column_ids.get(f'{key}.{column_name}') if key else None
            if teiid_column_id is None:
                continue
            db.add(spec_db.SqlReference(spec_version_id=version_id, triples_map_id=row.id,
                                        teiid_column_id=teiid_column_id, clause=clause))
            counts['sql_references'] += 1

        for class_iri in tm.subject_classes:
            db.add(spec_db.SubjectClass(spec_version_id=version_id, triples_map_id=row.id,
                                        class_iri=class_iri))

        def add_term_map(term, pom_id=None):
            term_row = spec_db.TermMap(spec_version_id=version_id, triples_map_id=row.id, pom_id=pom_id,
                                       position=term.position, value_kind=term.value_kind,
                                       value=term.value, term_type=term.term_type,
                                       datatype=term.datatype, language=term.language)
            db.add(term_row)
            db.flush()
            counts['term_maps'] += 1
            for index, column_name in enumerate(term.columns):
                logical_id = logical_ids.get(column_name)
                if logical_id is None:
                    issues.append({'code': 'term_map_column_unresolved', 'severity': 'warning',
                                   'subject_kind': 'term_map', 'subject_ref': f'{tm.iri}#{term.value}',
                                   'message': f'kolom {column_name} tidak ada pada logical table'})
                    continue
                db.add(spec_db.TermMapColumn(spec_version_id=version_id, term_map_id=term_row.id,
                                             logical_column_id=logical_id, template_position=index))
            for join in term.joins:
                db.add(spec_db.JoinCondition(spec_version_id=version_id, term_map_id=term_row.id,
                                             child_column=join['child'], parent_column=join['parent'],
                                             parent_triples_map_iri=term.value))

        for term in tm.term_maps:
            add_term_map(term)
        for pom in tm.poms:
            pom_row = spec_db.PredicateObjectMap(spec_version_id=version_id, triples_map_id=row.id,
                                                 signature=pom['signature'])
            db.add(pom_row)
            db.flush()
            for term in pom['term_maps']:
                add_term_map(term, pom_id=pom_row.id)
    return counts


def persist_ontology(db: Session, version_id: int, artifact_id: int, content: str) -> dict[str, int]:
    spec = ontology_parser.parse(content)
    row = spec_db.Ontology(spec_version_id=version_id, artifact_id=artifact_id, iri=spec.iri,
                           version_iri=spec.version_iri, version_info=spec.version_info)
    db.add(row)
    db.flush()
    for imported in spec.imports:
        db.add(spec_db.OntImport(spec_version_id=version_id, ontology_id=row.id, imported_iri=imported))
    for entity in spec.entities:
        entity_row = spec_db.OntEntity(spec_version_id=version_id, ontology_id=row.id, iri=entity.iri,
                                       kind=entity.kind, declared_in='local',
                                       deprecated=entity.deprecated, functional=entity.functional)
        db.add(entity_row)
        db.flush()
        for expression, simple in entity.domains:
            db.add(spec_db.OntDomain(spec_version_id=version_id, entity_id=entity_row.id,
                                     class_expression=expression, is_simple=simple))
        for range_iri, compatible in entity.ranges:
            db.add(spec_db.OntRange(spec_version_id=version_id, entity_id=entity_row.id,
                                    range_iri=range_iri, owl2ql_compatible=compatible))
        for relation, other in entity.relations:
            db.add(spec_db.OntPropertyRelation(spec_version_id=version_id, entity_id=entity_row.id,
                                               relation=relation, other_iri=other))
        for property_iri, value, lang in entity.annotations:
            db.add(spec_db.OntAnnotation(spec_version_id=version_id, entity_id=entity_row.id,
                                         property_iri=property_iri, value=value, lang=lang))
    db.flush()
    return {'ontology_entities': len(spec.entities), 'ontology_imports': len(spec.imports)}
