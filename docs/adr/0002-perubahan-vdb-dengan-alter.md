# ADR-0002: Pembaruan Σ_S dengan pernyataan ALTER yang ditambahkan ke metadata VDB

- **Status:** Diterima
- **Tanggal:** 2026-09-17
- **Fase MAPE-K:** Plan (pembangkitan Σ′_S) dan Execute (effector Teiid)
- **Terkait:** keputusan D8 (RENAME diserap di lapisan Teiid), aturan "tanpa regex"

## Konteks

Σ_S didefinisikan sebagai DDL (`CREATE FOREIGN TABLE ...`) di dalam elemen
`<metadata type="DDL">` pada berkas VDB XML. Implementasi awal ASCAM mengubah DDL
tersebut dengan regex. Library parser SQL umum (`sqlglot` 30.x) tidak mengenali
dialek DDL Teiid (`CREATE FOREIGN TABLE ... OPTIONS`) dan hanya memperlakukannya
sebagai teks perintah, sehingga tidak dapat dipakai untuk menulis ulang DDL tersebut.

Teiid Reference Guide ("Schema object DDL", bagian ALTER TABLE; "BNF for SQL grammar",
produksi `ALTER TABLE`, `ADD column`, `DROP column`, `alter column options`,
`rename column options`) menyatakan bahwa DDL Teiid mendukung penambahan,
penghapusan, dan penggantian nama kolom serta perubahan OPTIONS kolom.

## Uji kelayakan F0.2

Skrip: `experiments/f0/f0_2_teiid_alter.sh`. Tabel sementara `f0_uji` di MySQL dan
PostgreSQL; VDB uji terpisah dari VDB `government`; kueri lewat transport ODBC.

| Uji | Pernyataan yang ditambahkan setelah `CREATE FOREIGN TABLE` | Hasil |
|---|---|---|
| Baseline sesudah perubahan fisik | (tidak ada) | Gagal di sumber: MySQL 1054 untuk `tgl`; PostgreSQL untuk `kode` |
| 3a | `ALTER FOREIGN TABLE f0_uji ALTER COLUMN tgl OPTIONS (SET NAMEINSOURCE 'tgl_baru');` | VDB ACTIVE; kolom Teiid tetap `tgl`, `SYS.Columns.NameInSource = tgl_baru`; data terbaca |
| 3b | `ALTER FOREIGN TABLE f0_uji RENAME COLUMN tgl TO tgl_baru;` | VDB ACTIVE; kolom Teiid menjadi `tgl_baru`; data terbaca |
| 3c | `ALTER FOREIGN TABLE f0_uji ADD COLUMN email varchar(100);` dan `... DROP COLUMN kode;` | VDB ACTIVE; `email` terbaca; `SELECT kode` ditolak Teiid (`TEIID31118`) sebelum dikirim ke sumber |

Waktu dari "added to the repository" sampai `ACTIVE`: ±2–35 ms per VDB uji.
Jarak antar-aktivasi (±5 s) konsisten dengan `scan-interval` bawaan deployment scanner.

## Keputusan

1. ASCAM **tidak mem-parse dan tidak menulis ulang** DDL Teiid yang ada. Setiap
   adaptasi **menambahkan** pernyataan `ALTER FOREIGN TABLE` yang dibangkitkan dari
   event terstruktur, di akhir elemen metadata model yang bersangkutan. Struktur XML
   dimanipulasi dengan parser XML (CDATA dipertahankan).
2. Pemetaan operasi:
   - ADD COLUMN → `ALTER FOREIGN TABLE t ADD COLUMN c <tipe>`
   - DROP COLUMN → `ALTER FOREIGN TABLE t DROP COLUMN <nama kolom Teiid>`
   - RENAME COLUMN → `ALTER FOREIGN TABLE t ALTER COLUMN <nama kolom Teiid> OPTIONS (SET NAMEINSOURCE '<nama baru di sumber>')` (D8); nama kolom Teiid tidak berubah, sehingga mapping, join, template IRI, dan view yang merujuk kolom tersebut tetap valid.
3. Σ_S untuk Knowledge dibaca dari tabel sistem Teiid (`SYS.Tables`, `SYS.Columns`,
   `SYS.KeyColumns`) saat runtime, bukan dari teks berkas (lihat F0.3).

## Konsekuensi

- **Positif:** tanpa regex dan tanpa parser DDL Teiid; perubahan minimal dan dapat
  diaudit (blok ALTER menjadi jejak perubahan); RENAME tidak menyentuh mapping.
- **Knowledge wajib menyimpan nama Teiid dan nama di sumber secara terpisah.**
  Nama efektif di sumber = `NameInSource` bila terisi, selain itu `Name`.
- **Potensi bentrok nama** akibat D8: bila sumber mengganti `a → b` lalu menambah
  kolom baru bernama `a`, kolom Teiid `a` sudah ada. Fase Plan harus memilih nama
  Teiid lain (misalnya `a_2`) dengan `NAMEINSOURCE 'a'`. Wajib diuji saat implementasi.
- **`Position` kolom tidak berurutan setelah DROP** (pada uji 3c, `email` di posisi 4).
- **DDL VDB bertambah panjang** seiring adaptasi. Pemadatan (membangkitkan ulang
  `CREATE` dari Knowledge) dicatat sebagai pekerjaan lanjutan.
- **Belum diuji:** D8 pada PostgreSQL, nama kolom peka huruf besar/kecil atau
  memerlukan tanda kutip, serta kolom yang direferensikan view Teiid.

## Referensi

- Teiid Reference Guide (`teiid-documents.pdf`): "Schema object DDL" (hlm. 352–356),
  "BNF for SQL grammar" (hlm. 818–825), "System schema" (hlm. 555–563).
- Hasil uji: `results/f0/f0_2_20260917T090245/output.txt` (lokal, tidak di-commit).
