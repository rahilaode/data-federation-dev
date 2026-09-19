# ADR-0019: Cara menyunting VDB, ontologi, dan mapping

- **Status:** Diterima
- **Tanggal:** 2026-09-18
- **Fase MAPE-K:** Execute
- **Terkait:** ADR-0002, ADR-0003, ADR-0010, ADR-0013, ADR-0018

## Konteks

Rencana adaptasi berisi tindakan per artefak (ADR-0015). Ketiga artefak berbeda sifatnya:
VDB adalah XML berisi DDL Teiid, ontologi adalah Turtle yang ditulis manusia dan penuh
komentar, sedangkan mapping adalah Turtle yang strukturnya bersarang dan perlu **dihapus**
sebagiannya saat kolom hilang.

## Keputusan

1. **VDB: hanya menambahkan `ALTER`.** DDL yang ada tidak pernah diurai maupun ditulis ulang.
   Struktur XML disunting dengan parser XML sehingga blok CDATA tetap utuh, dan versi VDB
   dinaikkan untuk penerapan blue-green (ADR-0004). Seluruh identifier dikutip ganda karena
   nama kolom sumber dapat berupa kata kunci Teiid (ADR-0003).
2. **Ontologi: append-only dengan blok terkelola.** Triple baru ditulis di akhir berkas di bawah
   penanda blok ASCAM, sehingga komentar dan tata letak bagian tulisan manusia tetap utuh.
   Penambahan property bersifat idempoten (diperiksa dari deklarasi yang sudah ada), dan
   penghapusan diganti dengan `owl:deprecated` (OWL 2) agar kueri lama tidak langsung rusak.
   Metadata memakai kosakata umum: `rdfs:label`, `rdfs:comment`, `dcterms:created`,
   `dcterms:modified`, dan `skos:changeNote`.
3. **Mapping: append-only bila mungkin, tulis ulang bila terpaksa.** Menambahkan
   predicate-object map ditulis sebagai **blok terkelola** di akhir berkas: dalam Turtle, triple
   tambahan untuk subjek yang sama menyatu dengan deklarasi sebelumnya, sehingga TriplesMap yang
   ada bertambah pemetaan tanpa berkasnya ditulis ulang. Setiap blok diapit penanda berisi kunci
   (predikat dan tabel), sehingga ASCAM dapat menghapusnya kembali sebagai teks dan berkas kembali
   persis seperti semula. Pemetaan yang **ditulis manusia** hanya dapat dihapus dengan
   menyerialisasi ulang graf memakai rdflib; dalam kasus itu komentar di badan berkas hilang,
   sedangkan komentar pembuka dan nama prefix asal dipertahankan. Risikonya diredam karena:
   (a) isi lengkap setiap versi tersimpan di Knowledge (ADR-0012), (b) agen mencadangkan berkas
   sebelum setiap penulisan (ADR-0018). Penyuntingan yang mempertahankan format sepenuhnya
   (mis. lewat concrete syntax tree) dicatat sebagai pekerjaan lanjutan.
4. **Sasaran penyuntingan mapping adalah TriplesMap, bukan tabel.** IRI TriplesMap diambil dari
   rencana (ditentukan Knowledge dari lineage); pencocokan nama tabel hanya dipakai sebagai
   cadangan. Satu tabel dapat dibaca beberapa TriplesMap dengan logical table berbeda, sehingga
   penebakan berdasarkan nama tabel pernah menempelkan pemetaan ke TriplesMap yang salah.
   Kunci blok terkelola pun memakai IRI TriplesMap agar penghapusan tepat sasaran.
5. **Kueri logical table dapat ditulis ulang** untuk mengeluarkan kolom yang dihapus dari
   daftar proyeksi `rr:sqlQuery` (memakai sqlglot). Penyuntingan ini menyentuh triple tulisan
   manusia, sehingga berkas ditulis ulang seperti pada penghapusan pemetaan. Penulisan ulang
   ditolak bila kueri tidak dapat diurai atau bila kolom itu satu-satunya yang diproyeksikan.
6. **Penyunting adalah fungsi murni** (teks masuk, teks keluar) tanpa akses berkas maupun
   jaringan, sehingga dapat diuji tanpa OBDF yang berjalan.

## Bukti

30 uji penyunting: penambahan `ALTER` mempertahankan CDATA dan DDL lama, versi dinaikkan satu
kali meski beberapa model disunting, identifier kata kunci tetap dikutip, pemetaan tipe dari
Knowledge dipakai, galat dilaporkan eksplisit (model tak dikenal, XML rusak, tipe tak diketahui,
tindakan tak dikenal, versi non-numerik), dan adaptasi berulang menumpuk pernyataan; ontologi
tetap valid Turtle dengan komentar utuh, prefix ditambahkan hanya bila belum ada, penambahan
property idempoten, deprecation tidak menghapus deklarasi; mapping menambah pemetaan sebagai blok
terkelola tanpa mengubah baris lain dan tetap menyatu ke TriplesMap yang sama, penambahan
idempoten, blok milik ASCAM dihapus sebagai teks sehingga berkas kembali identik, pemetaan
tulisan manusia dihapus lewat penulisan ulang dengan prefix asal dipertahankan, penghapusan
dapat dibatasi per tabel, dan sasaran yang tidak ada dilaporkan; kueri logical table kehilangan kolom yang dihapus tanpa
mengubah kolom lain maupun sumbernya, `SELECT *` tidak disentuh, dan kueri yang akan menjadi
kosong ditolak. Ketiganya juga diuji terhadap artefak OBDF yang
sebenarnya (`experiments/f4/preview_edits.py`).

## Konsekuensi

- Berkas VDB memanjang seiring adaptasi; pemadatan dicatat sebagai pekerjaan lanjutan (ADR-0002).
- Ontologi memuat blok terkelola ASCAM yang tidak boleh disunting manual.
- Diff pada artefak nyata: penambahan kolom menghasilkan 14 baris perubahan pada VDB dan 13 baris
  pada mapping; penghapusan pemetaan tulisan manusia masih menghasilkan ±345 baris karena berkas
  ditulis ulang, sehingga perubahan itu perlu ditinjau lewat diff versi di UI.
