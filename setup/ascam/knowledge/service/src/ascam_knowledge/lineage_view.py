"""
Column lineage shown in the console (ADR-0024): from a source column to an ontology property
in one specification version ℱ.

The graph is built from `spec.column_usage`, which the sync creates (sync/lineage.py), so it is
exactly the lineage that impact analysis uses. Every path has the form

    source column ⊳ foreign-table column ⊳ view column* ⊳ TriplesMap ⊳ property

and is returned whole (`paths`) so that the console can highlight 𝒟(c, ℱ) without guessing
from the edges. Foreign columns that no mapping uses are shown as well; such columns are not
visible through SPARQL. This module only reads.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import registry, spec


def _short(iri: str | None) -> str:
    if not iri:
        return '-'
    return iri.rsplit('#', 1)[-1].rsplit('/', 1)[-1]


def build(db: Session, obdf_id: int, version: spec.SpecVersion, source: str | None = None,
          table: str | None = None, column: str | None = None) -> dict:
    vid = version.id
    model_sources: dict[int, str] = {}
    for model_id, source_name in db.execute(
            select(spec.TeiidModelSource.model_id, registry.SourceSystem.logical_name)
            .join(registry.SourceSystem,
                  registry.SourceSystem.id == spec.TeiidModelSource.source_system_id)
            .where(spec.TeiidModelSource.spec_version_id == vid)).all():
        model_sources.setdefault(model_id, source_name)

    columns: dict[int, dict] = {}
    for c, t, m in db.execute(
            select(spec.TeiidColumn, spec.TeiidTable, spec.TeiidModel)
            .join(spec.TeiidTable, spec.TeiidTable.id == spec.TeiidColumn.table_id)
            .join(spec.TeiidModel, spec.TeiidModel.id == spec.TeiidTable.model_id)
            .where(spec.TeiidColumn.spec_version_id == vid)).all():
        columns[c.id] = {'column': c, 'table': t, 'model': m}

    def matches(info: dict) -> bool:
        t = info['table']
        if source and model_sources.get(t.model_id) != source:
            return False
        if table and (t.source_table or '').lower() != table.lower():
            return False
        if column and (info['column'].source_column or info['column'].name).lower() != column.lower():
            return False
        return True

    foreign = {cid: info for cid, info in columns.items()
               if info['table'].kind == 'foreign' and matches(info)}

    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}
    paths: list[dict] = []

    def node(nid: str, **data) -> str:
        nodes.setdefault(nid, {'id': nid, **data})
        return nid

    def edge(a: str, b: str, kind: str) -> None:
        edges.setdefault((a, b), {'from': a, 'to': b, 'kind': kind})

    def column_node(cid: int) -> str:
        info = columns[cid]
        c, t, m = info['column'], info['table'], info['model']
        return node(f'col:{cid}', layer='sigma', kind='view_column' if t.kind != 'foreign'
                      else 'foreign_column', label=f'{m.name}.{t.name}.{c.name}',
                      table=f'{m.name}.{t.name}', column=c.name, data_type=c.data_type)

    starts: dict[int, list[str]] = {}
    for cid, info in foreign.items():
        c, t = info['column'], info['table']
        src_name = model_sources.get(t.model_id, '?')
        obj = '.'.join(x for x in (t.source_schema, t.source_table, c.source_column or c.name) if x)
        src = node(f'src:{src_name}:{obj}', layer='source', kind='source_column',
                     label=f'{src_name}:{obj}', source=src_name,
                     table=t.source_table, column=c.source_column or c.name)
        fc = column_node(cid)
        edge(src, fc, 'name_in_source' if c.name_in_source else 'direct')
        starts[cid] = [src, fc]

    triples_maps = dict(db.execute(select(spec.TriplesMap.id, spec.TriplesMap.iri)
                                   .where(spec.TriplesMap.spec_version_id == vid)).all())
    entities = {iri: (kind, deprecated) for iri, kind, deprecated in db.execute(
        select(spec.OntEntity.iri, spec.OntEntity.kind, spec.OntEntity.deprecated)
        .where(spec.OntEntity.spec_version_id == vid)).all()}

    used: set[int] = set()
    usages = db.execute(select(spec.ColumnUsage).where(
        spec.ColumnUsage.spec_version_id == vid,
        spec.ColumnUsage.foreign_column_id.in_(list(foreign) or [-1]))
        .order_by(spec.ColumnUsage.id)).scalars().all()
    for u in usages:
        used.add(u.foreign_column_id)
        chain = list(starts[u.foreign_column_id])
        # `path` runs from the view column read by the mapping towards the source; reversed here

        for hop in reversed(u.path or []):
            if hop.get('column_id') not in columns:
                continue
            nid = column_node(hop['column_id'])
            edge(chain[-1], nid, hop.get('kind') or 'passthrough')
            chain.append(nid)
        tm_iri = triples_maps.get(u.triples_map_id)
        tm = node(f'tm:{u.triples_map_id}', layer='mapping', kind='triples_map',
                    label=_short(tm_iri), iri=tm_iri)
        edge(chain[-1], tm, u.role)
        chain.append(tm)
        if u.predicate_iri:
            kind, deprecated = entities.get(u.predicate_iri, (None, False))
            prop = node(f'prop:{u.predicate_iri}', layer='ontology', kind='property',
                          label=_short(u.predicate_iri), iri=u.predicate_iri,
                          entity_kind=kind, deprecated=bool(deprecated), declared=kind is not None)
            edge(tm, prop, 'maps_to')
            chain.append(prop)
        paths.append({'nodes': chain, 'role': u.role, 'weakest_link': u.weakest_link,
                      'foreign_column_id': u.foreign_column_id})

    for cid in foreign:
        if cid not in used:
            paths.append({'nodes': starts[cid], 'role': None, 'weakest_link': None,
                          'foreign_column_id': cid})

    return {
        'obdf_id': obdf_id, 'spec_version_id': vid, 'version_no': version.version_no,
        'filter': {'source': source, 'table': table, 'column': column},
        'nodes': list(nodes.values()), 'edges': list(edges.values()), 'paths': paths,
        'unused_columns': sorted(nodes[f'col:{cid}']['label'] for cid in foreign if cid not in used),
    }

