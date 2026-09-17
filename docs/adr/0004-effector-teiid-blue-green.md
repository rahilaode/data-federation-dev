# ADR-0004: Effector Teiid — deploy lewat management API dan penggantian Σ_S secara blue-green

- **Status:** Diterima
- **Tanggal:** 2026-09-17
- **Fase MAPE-K:** Execute (effector Σ′_S) dan verifikasi
- **Terkait:** D9 (target di host lain), D3 (versioning spesifikasi), ADR-0002, ADR-0003
- **Menggantikan:** mekanisme deployment scanner pada `executor/teiid_deployer.py`

## Konteks

Implementasi awal menulis VDB ke folder deployment bersama dan memicu redeploy
dengan marker `.dodeploy`. Cara ini (a) mensyaratkan ASCAM dan Teiid berbagi
filesystem, (b) bergantung pada siklus pindai 5 s sehingga waktu redeploy bervariasi
1,5–5 s, dan (c) mengganti VDB versi yang sama secara langsung: bila versi baru
rusak, OBDF berhenti melayani sampai revert selesai. README deployment scanner
WildFly sendiri menyarankan management API untuk sistem produksi.

## Uji kelayakan

**F0.4** (`experiments/f0/f0_4_wildfly_mgmt.py`) — WildFly HTTP management API
(`/management`, `/management/add-content`; autentikasi Digest ManagementRealm):

| Operasi | Hasil |
|---|---|
| Kredensial salah | HTTP 401 |
| Deploy VDB baru (`add-content` + `add`) | 0,019 s + 0,079 s; `ACTIVE` 0,012 s kemudian |
| Ganti isi (`full-replace-deployment`) | 0,008 s + 0,085 s; `ACTIVE` 0,006 s kemudian; kolom hasil ALTER langsung terlihat; `ActiveTimestamp` bertambah |
| VDB dengan DDL tidak valid | **Operasi WildFly `success`, status VDB `FAILED`** |

**F0.4b** (`experiments/f0/f0_4b_teiid_versioning.py`) — versi VDB dan connection type
(Teiid Reference Guide, "VDB Versioning"; "Teiid Management CLI"):

| Kondisi | Koneksi baru tanpa nomor versi dilayani oleh |
|---|---|
| v1 ACTIVE (BY_VERSION), v2 FAILED | v1 |
| v1, v2 ACTIVE, keduanya BY_VERSION | v1 (paling awal); `vdb.2` dapat diakses langsung lewat ODBC |
| v2 diberi `ANY` (0,023 s) | v2; koneksi yang sudah terbuka ke v1 tetap berjalan |
| v1 diberi `NONE` | koneksi baru ke `vdb.1` ditolak (`TEIID40048`); koneksi lama tetap berjalan |
| v1 di-undeploy | koneksi lama ke v1 diputus (`TEIID40042`) |
| v2 dan v3 sama-sama `ANY` | **v3 (versi tertinggi)** — perilaku ini tidak tertulis di dokumentasi, diamati empiris |
| Rollback: v3 `NONE`, v2 `ANY` (±0,008 s per operasi) | v2 |

Penyebab kegagalan VDB tersedia pada hasil `/subsystem=teiid:get-vdb` di
`result.models[].validity-errors[]` (entri `severity` = `ERROR`, contoh:
`TEIID31259 ... line 3 column 43 ... Group does not exist: tabel_tidak_ada`),
bersama `models[].metadata-status`.

## Keputusan

Effector Teiid memakai **WildFly HTTP management API** dan menerapkan Σ′_S sebagai
**versi VDB baru** di samping versi yang sedang melayani (blue-green):

1. Unggah VDB versi N+1 (nama deployment unik, mis. `government-<N+1>-vdb.xml`,
   atribut `version` pada XML) dan `add` dengan connection type bawaan (BY_VERSION).
2. Tunggu status dari `get-vdb`. Bila `FAILED`: ambil entri `validity-errors` ber-`severity`
   `ERROR`, hapus versi N+1, eskalasikan ke HITL. **Versi N tidak tersentuh.**
3. Bila `ACTIVE`: verifikasi Σ′_S lewat ODBC ke `vdb.N+1` (`SYS.Columns` memuat
   perubahan yang direncanakan).
4. Validasi ℳ′ dan 𝒯′ terhadap versi N+1 sebelum dipakai (lihat F0.5).
5. Set versi N+1 ke `ANY`, muat ulang Ontop (ADR-0001), jalankan verifikasi SPARQL.
6. Bila verifikasi gagal: rollback dengan versi N+1 → `NONE` dan versi N → `ANY`,
   muat ulang Ontop, eskalasikan ke HITL.
7. Bila berhasil: versi N → `NONE`. Versi lama dipertahankan sebanyak K versi untuk
   rollback cepat, lalu di-undeploy. ASCAM **hanya menghapus deployment yang ia buat
   sendiri** (tercatat di Knowledge).

Keberhasilan tidak pernah dinilai dari `outcome` operasi WildFly saja; status VDB
dari Teiid wajib diperiksa.

## Konsekuensi

- **Positif:** OBDF tetap melayani selama Σ′_S disiapkan dan saat Σ′_S rusak;
  redeploy ±0,1 s dan deterministik (tanpa siklus pindai); rollback ±0,02 s;
  ASCAM dapat berada di host lain; nomor versi VDB menjadi jejak versioning
  spesifikasi (D3) yang dicatat di Knowledge.
- **Syarat:** klien OBDA (Ontop) terhubung **tanpa** nomor versi (URL
  `jdbc:teiid:government@mm://...` sudah memenuhi). Versi pertama yang dikelola
  ASCAM perlu diberi `ANY` agar pemilihan versi deterministik.
- **Syarat keamanan:** ASCAM memerlukan akun ManagementRealm. Management API
  bawaan berjalan tanpa TLS; untuk host berbeda perlu jaringan privat atau TLS
  (keterbatasan lingkungan riset).
- **Perilaku yang perlu dijaga:** koneksi yang sudah terbuka tetap di versi lama sampai
  versi itu di-undeploy; karena itu Ontop tetap di-reload setelah pemindahan.
- **Koeksistensi dengan scanner:** VDB awal pada lingkungan eksperimen di-deploy
  scanner. ASCAM memperlakukannya sebagai versi N dan tidak meng-undeploy-nya.
  Lingkungan eksperimen akan dipindahkan ke deploy lewat management API agar
  seluruh versi dikelola dengan satu mekanisme.
- **Keterbatasan uji:** VDB uji hanya memiliki satu model dan satu tabel; pemilihan
  versi tertinggi di antara beberapa `ANY` diamati pada satu kasus.

## Referensi

- Teiid Reference Guide (`teiid-documents.pdf`): "VDB Versioning" (hlm. 62–63),
  "Teiid Management CLI" (hlm. 93–95), catatan rilis status VDB (hlm. 318).
- WildFly Core 11.1.1.Final: `DomainApiCheckHandler.java` (`/management`,
  `/management/add-content`), `ModelDescriptionConstants.java`
  (`full-replace-deployment`, `undeploy`, `hash`):
  https://github.com/wildfly/wildfly-core/tree/11.1.1.Final/domain-http/interface
- Hasil uji: `results/f0/f0_4_20260917T022406/`, `results/f0/f0_4b_20260917T023354/`
  (lokal, tidak di-commit).
