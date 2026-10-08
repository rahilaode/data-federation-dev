# ADR-0023: Penyesuaian view Teiid saat kolom sumber dihapus

- **Status:** Diterima; F0.8 lulus (2026-10-09), skenario A007–A009 belum dievaluasi
- **Tanggal:** 2026-10-08
- **Fase MAPE-K:** Analyze & Plan, Execute
- **Terkait:** ADR-0002, ADR-0016, ADR-0022

## Konteks

Sebelum keputusan ini, ASCAM hanya mengubah foreign table. Lineage menelusuri jalur lewat view
dan menandai jalur `expression`/`predicate` untuk persetujuan, tetapi penghapusan kolom yang
diteruskan view apa adanya diputuskan otomatis tanpa menyesuaikan view; VDB versi baru lalu
memuat view yang merujuk kolom yang sudah tidak ada. Kolom yang hanya dipakai view tanpa
dibaca mapping bahkan tidak terlihat di lineage.

Teiid mengizinkan `ALTER VIEW name AS queryExpression`, tetapi tidak mengizinkan perubahan
informasi kolom; kolom view yang tidak dideklarasikan diturunkan dari kueri, sedangkan kolom yang
dideklarasikan inline hanya dapat diubah propertinya (teiid-documents, *Schema object DDL*,
hlm. 356–357; *DDL commands → Alter view*, hlm. 495).

## Keputusan

1. **Penghapusan kolom dari view diperlakukan sebagai drop(v, c)** dan dirambatkan sepanjang
   `TeiidDependency`, termasuk view bertingkat dan view yang tidak dibaca mapping mana pun
   (`ascam_knowledge/views.py`).
2. **Otomatis hanya bila proyeksi mengecil**: kolom view yang meneruskan kolom terdampak apa
   adanya dibuang dari daftar proyeksi lewat `ALTER VIEW`, dan kolom view itu dirambatkan ke
   tingkat berikutnya. View `SELECT *` tidak ditulis ulang tetapi tetap dirambatkan.
3. **Persetujuan administrator** bila kolom dipakai di WHERE/JOIN/GROUP BY/HAVING/ORDER BY view,
   dipakai di dalam ekspresi kolom view, diteruskan oleh view berkolom inline, view materialisasi,
   view yang definisinya tidak dapat diurai, atau view yang akan kehilangan seluruh kolomnya.
4. **Definisi baru dibentuk dengan membuang butir proyeksi dari teks asli**; FROM, WHERE, dan
   fungsi Teiid tidak ditulis ulang oleh pengurai, sehingga dialek Teiid tetap utuh.
5. Proyeksi eksplisit pada logical table mapping yang menyebut **kolom view** yang ikut hilang
   ditulis ulang seperti kolom foreign table (P-002).
6. Executor menambahkan `ALTER VIEW` ke blok metadata model view, mengikuti prinsip ADR-0002
   (hanya menambahkan pernyataan). Versi VDB baru divalidasi Teiid saat deploy; definisi yang
   ditolak menghentikan eksekusi sebelum peralihan (ADR-0022).
7. Verifikasi P-002 menuntut **semua** predikat yang di-deprecate hilang dan hanya predikat itu
   yang hilang, karena satu kolom dapat diekspos beberapa predikat lewat view.

8. **Bentuk pernyataan dapat dipilih** (`ASCAM_EXEC_VIEW_STATEMENT`): `alter` (bawaan) atau
   `recreate` (`DROP VIEW` lalu `CREATE VIEW`, BNF *drop table*, hlm. 819). Keduanya tetap hanya
   menambahkan pernyataan. Pilihan ditetapkan dari uji kelayakan F0.8, bukan diasumsikan.
9. Fungsi penulisan ulang teks (`viewsql.py`) dipisahkan dari perambatan (`views.py`) agar uji
   kelayakan memakai kode yang sama persis dengan Knowledge.

## Uji kelayakan F0.8 (results/f0/f0_8_20261008T194940, lokal)

Skrip: `experiments/f0/f0_8.sh` (memanggil `f0_8_teiid_alter_view.py` di kontainer sementara).
VDB uji `f0av` berisi foreign table `penerima_manfaat`, view pass-through dengan `WHERE`, view
bertingkat di atasnya, dan (terpisah) view berkolom inline. Definisi baru dibentuk dari `Body`
di `SYSADMIN.Views` dengan `viewsql.remove_projection`.

| Versi | Isi | Harapan |
|---|---|---|
| v1 | VDB dasar | ACTIVE |
| v2 | DROP kolom tanpa penyesuaian (kontrol, mengulang F0.7) | FAILED |
| v3 | DROP + `ALTER VIEW` pada kedua view | pertanyaan utama |
| v4 | DROP + `DROP VIEW`/`CREATE VIEW` pada kedua view | cadangan |
| v5 | DROP + `ALTER VIEW` hanya view dasar | FAILED (perambatan diperlukan) |
| v6–v7 | view berkolom inline, lalu DROP + `ALTER VIEW` | ACTIVE, lalu FAILED |

Ketujuh kasus sesuai harapan. v3 ACTIVE dengan kolom view tinggal `penerima_id` dan 10 baris
terbaca; v4 juga ACTIVE. v5 gagal pada view bertingkat (`TEIID31118`), sehingga perambatan wajib.
v7 gagal dengan `TEIID30066 … does not have the correct number of projected symbols. Expected 2,
but was 1`. Jadi larangan mengubah informasi kolom pada Reference Guide berlaku untuk view berkolom
inline, bukan view yang kolomnya diturunkan dari kueri. `ASCAM_EXEC_VIEW_STATEMENT` tetap `alter`.

## Studi kasus dan evaluasi

Model virtual `layanan` (tiga view) dan TriplesMap `MapPenerimaAktif`, `MapRiwayat`,
`MapProgramBerakhir` ditambahkan ke artefak dasar, beserta skenario A007–A009 (lihat
`docs/evaluasi/README.md`). `tests/test_studi_kasus_view.py` memeriksa keputusan dan rencana
ketiga skenario atas artefak nyata di repositori, serta bahwa A002 dan A005 tidak menyentuh view.

## Konsekuensi

- View tanpa kunci membuat Ontop 4.1.1 menambahkan `SELECT DISTINCT`, dan Teiid menolak
  `ORDER BY` atas ekspresi IRI pada kueri semacam itu (`TEIID30088`). Kunci hanya dapat
  dideklarasikan bersama kolom inline, yang justru tidak dapat dikecilkan dengan `ALTER VIEW`.
  Ini kompromi rancangan yang dicatat, bukan diselesaikan, oleh ADR ini.

- Penghapusan kolom yang diteruskan view kini teradaptasi otomatis; kasus yang mengubah baris
  atau arti nilai tetap memerlukan keputusan manusia.
- Penerimaan `ALTER VIEW` di dalam metadata DDL VDB oleh Teiid 16 belum diuji pada lingkungan
  nyata; skenario evaluasi dengan view diperlukan sebelum klaim ini masuk paper.
