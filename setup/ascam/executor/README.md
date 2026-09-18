# ASCAM Executor

Menerapkan rencana adaptasi yang berstatus `approved` ke OBDF. Keputusan dan rencananya
dibuat Knowledge (ADR-0015, ADR-0016); Executor hanya menjalankannya.

Tahap ini (F4b) berisi **penyunting artefak** sebagai fungsi murni (ADR-0019):

| Modul | Fungsi |
|---|---|
| `artifacts/vdb.py` | Menambahkan `ALTER FOREIGN TABLE` ke metadata model dan menaikkan versi VDB |
| `artifacts/ontology.py` | Menambahkan DatatypeProperty dan menandai property usang, secara append-only |
| `artifacts/r2rml.py` | Menambahkan dan menghapus predicate-object map |

Orkestrasi penerapan (blue-green Teiid, validasi, muat ulang Ontop, verifikasi, revert)
dibangun pada F4c.

## Menguji

```bash
docker run --rm -v "$PWD/setup/ascam/executor/service:/src:ro" python:3.12-slim sh -c \
  'cp -r /src /w && cd /w && pip install -q ".[test]" && python -m pytest -q'
```

## Pratinjau suntingan terhadap artefak nyata

```bash
docker run --rm -v "$PWD:/repo:ro" python:3.12-slim sh -c \
  'pip install -q "rdflib>=7,<8" && python /repo/experiments/f4/preview_edits.py'
```

Skrip itu tidak menulis apa pun; ia hanya menampilkan diff.
