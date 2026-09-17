# ASCAM Ontop Agent

Agen yang berjalan **di host Ontop**. Ia satu-satunya komponen ASCAM yang menyentuh berkas
artefak OBDA, sehingga Knowledge Service, Orchestrator, dan Executor tidak memerlukan akses
berkas ke host tersebut (D9). Keputusan dan buktinya: ADR-0011.

## Kemampuan saat ini

| Endpoint | Keterangan |
|---|---|
| `GET /health` | Tanpa token; status agen dan keberadaan artefak |
| `GET /api/v1/artifacts` | Daftar ℳ dan 𝒯 beserta SHA-256, ukuran, dan waktu ubah |
| `GET /api/v1/artifacts/{kind}` | Isi artefak (`r2rml`, `ontology`) |
| `POST /api/v1/validate` | Menjalankan `ontop validate`; `db_url` opsional untuk menguji terhadap versi VDB tertentu (ADR-0005) |

Penerapan artefak dan reload Ontop dibangun bersama Executor (F4).

**Berkas properti Ontop tidak pernah diekspos**, karena memuat kredensial JDBC.

## Menjalankan

```bash
setup/ascam/knowledge/scripts/init-secrets.sh          # membuat token agen bila belum ada
docker compose -f setup/ascam/ontop-agent/docker-compose.yaml up -d --build
curl -s http://127.0.0.1:18100/health
```

## Menguji

```bash
cd setup/ascam/ontop-agent/service && python -m pytest -q      # butuh dependensi paket
```

Uji tidak memerlukan Docker maupun Ontop: pembungkus Docker disuntik dengan tiruan.
