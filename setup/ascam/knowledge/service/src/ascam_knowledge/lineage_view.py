"""
Lineage kolom untuk ditampilkan di konsol (ADR-0024): dari kolom di sumber sampai property
ontologi pada satu versi spesifikasi ℱ.

Graf disusun dari `spec.column_usage` yang dibangun saat sync (sync/lineage.py), sehingga sama
persis dengan lineage yang dipakai analisis dampak. Setiap jalur berbentuk

    kolom sumber ⊳ kolom foreign table ⊳ kolom view* ⊳ TriplesMap ⊳ property

dan dikembalikan utuh (`paths`) agar konsol dapat menyorot 𝒟(c, ℱ) tanpa menebak dari tepi.
Kolom foreign yang tidak dipakai mapping mana pun tetap ditampilkan; kolom seperti itu tidak
terlihat dari SPARQL. Modul ini hanya membaca.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import registry, spec


def _pendek(iri: str | None) -> str:
    if not iri:
        return '-'
    return iri.rsplit('#', 1)[-1].rsplit('/', 1)[-1]


def build(db: Session, obdf_id: int, version: spec.SpecVersion, source: str | None = None,
          table: str | None = None, column: str | None = None) -> dict:
    vid = version.id
    sumber_model: dict[int, str] = {}
    for model_id, nama in db.execute(
            select(spec.TeiidModelSource.model_id, registry.SourceSystem.logical_name)
            .join(registry.SourceSystem,
                  registry.SourceSystem.id == spec.TeiidModelSource.source_system_id)
            .where(spec.TeiidModelSource.spec_version_id == vid)).all():
        sumber_model.setdefault(model_id, nama)

    kolom: dict[int, dict] = {}
    for c, t, m in db.execute(
            select(spec.TeiidColumn, spec.TeiidTable, spec.TeiidModel)
            .join(spec.TeiidTable, spec.TeiidTable.id == spec.TeiidColumn.table_id)
            .join(spec.TeiidModel, spec.TeiidModel.id == spec.TeiidTable.model_id)
            .where(spec.TeiidColumn.spec_version_id == vid)).all():
        kolom[c.id] = {'column': c, 'table': t, 'model': m}

    def cocok(info: dict) -> bool:
        t = info['table']
        if source and sumber_model.get(t.model_id) != source:
            return False
        if table and (t.source_table or '').lower() != table.lower():
            return False
        if column and (info['column'].source_column or info['column'].name).lower() != column.lower():
            return False
        return True

    foreign = {cid: info for cid, info in kolom.items()
               if info['table'].kind == 'foreign' and cocok(info)}

    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str], dict] = {}
    paths: list[dict] = []

    def simpul(nid: str, **data) -> str:
        nodes.setdefault(nid, {'id': nid, **data})
        return nid

    def tepi(a: str, b: str, kind: str) -> None:
        edges.setdefault((a, b), {'from': a, 'to': b, 'kind': kind})

    def simpul_kolom(cid: int) -> str:
        info = kolom[cid]
        c, t, m = info['column'], info['table'], info['model']
        return simpul(f'col:{cid}', layer='sigma', kind='view_column' if t.kind != 'foreign'
                      else 'foreign_column', label=f'{m.name}.{t.name}.{c.name}',
                      table=f'{m.name}.{t.name}', column=c.name, data_type=c.data_type)

    awal: dict[int, list[str]] = {}
    for cid, info in foreign.items():
        c, t = info['column'], info['table']
        nama_sumber = sumber_model.get(t.model_id, '?')
        objek = '.'.join(x for x in (t.source_schema, t.source_table, c.source_column or c.name) if x)
        src = simpul(f'src:{nama_sumber}:{objek}', layer='source', kind='source_column',
                     label=f'{nama_sumber}:{objek}', source=nama_sumber,
                     table=t.source_table, column=c.source_column or c.name)
        fc = simpul_kolom(cid)
        tepi(src, fc, 'name_in_source' if c.name_in_source else 'direct')
        awal[cid] = [src, fc]

    triples_maps = dict(db.execute(select(spec.TriplesMap.id, spec.TriplesMap.iri)
                                   .where(spec.TriplesMap.spec_version_id == vid)).all())
    entitas = {iri: (kind, deprecated) for iri, kind, deprecated in db.execute(
        select(spec.OntEntity.iri, spec.OntEntity.kind, spec.OntEntity.deprecated)
        .where(spec.OntEntity.spec_version_id == vid)).all()}

    dipakai: set[int] = set()
    usages = db.execute(select(spec.ColumnUsage).where(
        spec.ColumnUsage.spec_version_id == vid,
        spec.ColumnUsage.foreign_column_id.in_(list(foreign) or [-1]))
        .order_by(spec.ColumnUsage.id)).scalars().all()
    for u in usages:
        dipakai.add(u.foreign_column_id)
        jalur = list(awal[u.foreign_column_id])
        # `path` berurutan dari kolom view yang dibaca mapping ke arah sumber; dibalik di sini
        for hop in reversed(u.path or []):
            if hop.get('column_id') not in kolom:
                continue
            nid = simpul_kolom(hop['column_id'])
            tepi(jalur[-1], nid, hop.get('kind') or 'passthrough')
            jalur.append(nid)
        tm_iri = triples_maps.get(u.triples_map_id)
        tm = simpul(f'tm:{u.triples_map_id}', layer='mapping', kind='triples_map',
                    label=_pendek(tm_iri), iri=tm_iri)
        tepi(jalur[-1], tm, u.role)
        jalur.append(tm)
        if u.predicate_iri:
            kind, deprecated = entitas.get(u.predicate_iri, (None, False))
            prop = simpul(f'prop:{u.predicate_iri}', layer='ontology', kind='property',
                          label=_pendek(u.predicate_iri), iri=u.predicate_iri,
                          entity_kind=kind, deprecated=bool(deprecated), declared=kind is not None)
            tepi(tm, prop, 'maps_to')
            jalur.append(prop)
        paths.append({'nodes': jalur, 'role': u.role, 'weakest_link': u.weakest_link,
                      'foreign_column_id': u.foreign_column_id})

    for cid in foreign:
        if cid not in dipakai:
            paths.append({'nodes': awal[cid], 'role': None, 'weakest_link': None,
                          'foreign_column_id': cid})

    return {
        'obdf_id': obdf_id, 'spec_version_id': vid, 'version_no': version.version_no,
        'filter': {'source': source, 'table': table, 'column': column},
        'nodes': list(nodes.values()), 'edges': list(edges.values()), 'paths': paths,
        'unused_columns': sorted(nodes[f'col:{cid}']['label'] for cid in foreign if cid not in dipakai),
    }

