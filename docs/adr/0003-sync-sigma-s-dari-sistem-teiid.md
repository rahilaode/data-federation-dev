# ADR-0003: Sinkronisasi Σ_S ke Knowledge dari tabel sistem Teiid lewat ODBC

- **Status:** Diterima
- **Tanggal:** 2026-09-17
- **Fase MAPE-K:** Knowledge (sync spesifikasi) dan Execute (verifikasi aktivasi VDB)
- **Terkait:** D2 (struktur Knowledge), D9 (target di host lain), ADR-0002

## Konteks

Knowledge ASCAM adalah model runtime dari OBDF (models@run.time). Σ_S harus dapat
dibaca dari Teiid yang mungkin berada di host lain, tanpa mem-parse berkas VDB
(ADR-0002) dan dengan library Python. Teiid 16 menyediakan transport ODBC yang
meniru protokol PostgreSQL (port 35432; `ssl mode="disabled"` pada templat
subsystem bawaan) serta tabel sistem `SYS.*` (Teiid Reference Guide, "System schema").

## Uji kelayakan F0.3

Skrip: `experiments/f0/f0_3_teiid_metadata.py`, dijalankan di kontainer Python
sementara pada jaringan `ascam-networks`, terhadap VDB `government`.

| Driver | Koneksi | Sync Σ_S (5 kueri SYS) | Kueri berparameter |
|---|---|---|---|
| psycopg2 2.9 (interpolasi di klien) | 0,019 s | 0,036 s | OK |
| psycopg 3.3 (parameter dikirim ke server) | 0,241 s | 0,036 s | OK |

Σ_S yang terbaca: 2 model fisik (`dukcapil`, `kemensos`), 7 tabel, seluruh kolom
(nama, posisi, `NameInSource`, tipe runtime, nullability, panjang, presisi) dan
kunci primer; status VDB dari `SYS.VirtualDatabases` (`LoadingTimestamp`,
`ActiveTimestamp`). Nama VDB yang salah menghasilkan galat `TEIID40046`.

**Dua kegagalan yang menjadi temuan:**

1. **Dokumentasi tidak sama dengan runtime.** Reference Guide (tabel `SYS.Columns`)
   mencantumkan `ElementLength` dan `sLengthFixed`; Teiid 16 memakai `Length` dan
   `IsLengthFixed`. Daftar kolom aktual ditanyakan ke `SYS.Columns` dengan
   `SchemaName = 'SYS'` dan disimpan pada hasil uji (`drivers.json`).
2. **Nama kolom dapat berupa kata kunci.** `PRECISION` berada di bagian
   "Reserved words" pada grammar Teiid (`engine/src/main/javacc/org/teiid/query/parser/SQLParser.jj`),
   sedangkan `VERSION`, `TYPE`, dan `POSITION` berada di bagian "NonReserved words".
   Identifier bertanda kutip ditulis dengan tanda kutip ganda (`QUOTED_ID`).

## Keputusan

1. Σ_S untuk Knowledge dibaca dari `SYS.VirtualDatabases`, `SYS.Schemas`,
   `SYS.Tables`, `SYS.Columns`, dan `SYS.KeyColumns` lewat transport ODBC.
2. Driver: **psycopg 3** (mendukung `asyncio`, sejalan dengan layanan FastAPI pada D12),
   dengan *connection pool*. psycopg2 dicatat sebagai alternatif yang juga kompatibel.
3. Pembaca metadata **menanyakan daftar kolom tabel sistem terlebih dahulu** dan hanya
   meminta kolom yang tersedia, sehingga toleran terhadap perbedaan versi Teiid.
4. **Seluruh identifier dalam SQL/DDL yang dibangkitkan ASCAM diberi tanda kutip ganda**,
   termasuk pernyataan `ALTER FOREIGN TABLE` pada ADR-0002.
5. Verifikasi aktivasi VDB pada fase Execute memakai status VDB dari Teiid
   (`ActiveTimestamp` bertambah dan/atau status `ACTIVE`), bukan hanya penanda
   deployment WildFly (lihat F0.4).

## Konsekuensi

- Sync Σ_S cepat (puluhan milidetik) dan tidak memerlukan akses berkas di host Teiid.
- ASCAM memerlukan akun Teiid dengan izin membaca metadata. Bila data role dipakai,
  visibilitas metadata terbatas pada objek yang diizinkan (Reference Guide,
  "Metadata visibility"); akun ASCAM perlu dikonfigurasi sesuai.
- Transport ODBC bawaan tidak terenkripsi. Untuk target di host lain, jalur jaringan
  perlu dilindungi (TLS pada transport atau jaringan privat); dicatat sebagai
  keterbatasan lingkungan riset.
- Selisih waktu koneksi antar-driver baru berasal dari satu sampel.

## Referensi

- Teiid Reference Guide (`teiid-documents.pdf`): "System schema" (hlm. 554–563).
- Grammar Teiid: https://github.com/teiid/teiid/blob/master/engine/src/main/javacc/org/teiid/query/parser/SQLParser.jj
- Templat subsystem Teiid (transport ODBC): https://github.com/teiid/teiid/blob/master/wildfly/teiid-feature-pack/wildfly-integration-feature-pack/src/main/resources/subsystem-templates/teiid.xml
- Blair, G., Bencomo, N., France, R. B. (2009). Models@run.time. *Computer*, 42(10), 22–27. https://doi.org/10.1109/MC.2009.326
- Hasil uji: `results/f0/f0_3_20260917T021700/` (lokal, tidak di-commit).
