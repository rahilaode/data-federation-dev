# Architecture Decision Records (ADR) — ASCAM

Setiap keputusan arsitektur dicatat bersama konteks, bukti uji, dan konsekuensinya.
Format mengikuti pola MADR (https://adr.github.io/madr/).

| No | Judul | Fase MAPE-K | Uji |
|---|---|---|---|
| [0001](0001-reload-ontop.md) | Mekanisme reload Ontop (init + `exec java`) | Execute | F0.1 |
| [0002](0002-perubahan-vdb-dengan-alter.md) | Pembaruan Σ_S dengan ALTER yang ditambahkan (D8) | Plan, Execute | F0.2 |
| [0003](0003-sync-sigma-s-dari-sistem-teiid.md) | Sync Σ_S dari tabel SYS Teiid lewat ODBC | Knowledge, Execute | F0.3 |
| [0004](0004-effector-teiid-blue-green.md) | Effector Teiid: management API dan blue-green versi VDB | Execute | F0.4, F0.4b |
| [0005](0005-mapping-r2rml-dan-validasi.md) | Mapping R2RML dan validasi sebelum aktivasi | Plan, Execute, Knowledge | F0.5 |
| [0006](0006-dependensi-view-teiid.md) | Dependensi view Teiid dari SYSADMIN.Usage dan SYSADMIN.Views | Knowledge, Analyze | F0.7 |
| [0007](0007-implementasi-skema-knowledge.md) | Implementasi skema Knowledge dan integritas di basis data | Knowledge | F1.2 |
| [0008](0008-knowledge-service-api.md) | Knowledge Service: API registry, autentikasi, enkripsi kredensial, konfigurasi deklaratif | Knowledge | F1.3 |
| [0009](0009-sumber-artefak-sigma-s.md) | Sumber pengambilan artefak dan spesifikasi Σ_S untuk sync | Knowledge, Execute | F1.4a, F1.4b |
| [0010](0010-migrasi-r2rml-dan-pensiun-iterasi-1.md) | Mapping runtime beralih ke R2RML; mesin iterasi 1 dipensiunkan | – | F0.5 |
| [0011](0011-ontop-agent.md) | Ontop Agent sebagai satu-satunya jalur ke artefak OBDA | Knowledge, Execute | F1.4d |
| [0012](0012-sync-dan-versioning-spesifikasi.md) | Proses sync dan pembentukan versi spesifikasi | Knowledge | F1.5a |
| [0013](0013-penguraian-mapping-dan-ontologi.md) | Penguraian ℳ dan 𝒯 menjadi struktur di Knowledge | Knowledge, Analyze | F1.5b |
| [0014](0014-lineage-kolom.md) | Lineage kolom sebagai dasar analisis dampak | Knowledge, Analyze, Plan | F1.5c |
| [0015](0015-analisis-dampak-d11.md) | Analisis dampak dan keputusan D11 di Knowledge | Analyze, Plan | F1.6 |
| [0016](0016-event-dan-siklus-rencana.md) | Penerimaan event skema dan siklus hidup rencana adaptasi | Monitor, Analyze, Plan | F3a |
| [0017](0017-orchestrator.md) | Orchestrator sebagai penormal event, bukan pengambil keputusan | Monitor, Analyze | F3b |
| [0018](0018-penulisan-artefak-dan-reload.md) | Penulisan artefak OBDA, pencadangan, dan muat ulang Ontop | Execute | F4a |
| [0019](0019-penyuntingan-artefak.md) | Cara menyunting VDB, ontologi, dan mapping | Execute | F4b |

Skrip uji kelayakan berada di `experiments/f0/`; hasil mentah disimpan lokal di
`results/f0/` (tidak di-commit).
