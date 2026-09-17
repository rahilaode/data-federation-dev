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

Skrip uji kelayakan berada di `experiments/f0/`; hasil mentah disimpan lokal di
`results/f0/` (tidak di-commit).
