# ASCAM Executor

Menerapkan rencana adaptasi yang berstatus `approved` ke OBDF. Keputusan dan rencananya
dibuat Knowledge (ADR-0015, ADR-0016); Executor hanya menjalankannya.

Tahap ini (F4b) berisi **penyunting artefak** sebagai fungsi murni (ADR-0019):

| Modul | Fungsi |
|---|---|
| `artifacts/vdb.py` | Menambahkan `ALTER FOREIGN TABLE` ke metadata model dan menaikkan versi VDB |
| `artifacts/ontology.py` | Menambahkan DatatypeProperty dan menandai property usang, secara append-only |
| `artifacts/r2rml.py` | Menambahkan dan menghapus predicate-object map |

Sejak F4c tersedia pula mesin eksekusinya (ADR-0020):

| Langkah | Isi |
|---|---|
| `deploy_vdb` | Σ′_S di-deploy sebagai **versi VDB baru** di samping versi yang melayani |
| `validate` | ℳ′ dan 𝒯′ ditulis lewat agen, lalu `ontop validate` dijalankan terhadap versi baru |
| `switch` | Koneksi dipindahkan ke versi baru (`connection type ANY`) |
| `reload_ontop` | Ontop dimuat ulang lewat agen |
| `verify` | Jawaban OBDF diperiksa dengan sidik jari graf sesuai pola adaptasi |
| `rollback` | Bila verifikasi gagal: koneksi dikembalikan, artefak dipulihkan, Ontop dimuat ulang |

## Menjalankan

```bash
docker compose -f setup/ascam/executor/docker-compose.yaml up -d --build
curl -s http://127.0.0.1:18300/health
```

Setel `ASCAM_EXEC_ENABLED=false` untuk menjalankan layanan tanpa mengeksekusi rencana.

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
