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
3. **Mapping: ditulis ulang dari graf RDF.** Menghapus predicate-object map tidak mungkin
   dilakukan secara append-only, sehingga berkas diserialisasi ulang dengan rdflib. Komentar
   pembuka berkas dipertahankan; komentar di dalam badan mapping **hilang**. Konsekuensi ini
   diterima karena: (a) isi lengkap setiap versi tersimpan di Knowledge dan dapat dibandingkan
   (ADR-0012), (b) agen menyimpan cadangan berkas sebelum setiap penulisan (ADR-0018), dan
   (c) ℳ setelah adaptasi pertama memang menjadi artefak yang dikelola mesin (ADR-0010).
   Penyuntingan yang mempertahankan format (mis. lewat concrete syntax tree) dicatat sebagai
   pekerjaan lanjutan.
4. **Penghapusan mapping dapat dibatasi per tabel**, karena satu predikat dapat dipakai beberapa
   TriplesMap. Tanpa pembatasan ini, DROP satu kolom akan menghapus pemetaan di tabel lain.
5. **Penyunting adalah fungsi murni** (teks masuk, teks keluar) tanpa akses berkas maupun
   jaringan, sehingga dapat diuji tanpa OBDF yang berjalan.

## Bukti

18 uji penyunting: penambahan `ALTER` mempertahankan CDATA dan DDL lama, versi dinaikkan satu
kali meski beberapa model disunting, identifier kata kunci tetap dikutip, pemetaan tipe dari
Knowledge dipakai, galat dilaporkan eksplisit (model tak dikenal, XML rusak, tipe tak diketahui,
tindakan tak dikenal, versi non-numerik), dan adaptasi berulang menumpuk pernyataan; ontologi
tetap valid Turtle dengan komentar utuh, prefix ditambahkan hanya bila belum ada, penambahan
property idempoten, deprecation tidak menghapus deklarasi; mapping menambah dan menghapus
predicate-object map dengan benar, penambahan idempoten, penghapusan dapat dibatasi per tabel,
dan sasaran yang tidak ada dilaporkan. Ketiganya juga diuji terhadap artefak OBDF yang
sebenarnya (`experiments/f4/preview_edits.py`).

## Konsekuensi

- Berkas VDB memanjang seiring adaptasi; pemadatan dicatat sebagai pekerjaan lanjutan (ADR-0002).
- Ontologi memuat blok terkelola ASCAM yang tidak boleh disunting manual.
- Perubahan mapping perlu ditinjau lewat diff versi di UI, karena tata letaknya dapat berubah.
