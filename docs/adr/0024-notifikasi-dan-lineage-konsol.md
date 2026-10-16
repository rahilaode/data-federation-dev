# ADR-0024: Persetujuan hanya untuk ADD, notifikasi untuk DROP/RENAME tak aman, lineage di konsol

- **Status:** Diterima; uji Knowledge dan konsol lulus (2026-10-16), A001, A004, A009 perlu dijalankan ulang
- **Tanggal:** 2026-10-16
- **Fase MAPE-K:** Plan (keputusan), Knowledge (lineage), Monitor (pemberitahuan)
- **Terkait:** ADR-0015, ADR-0016, ADR-0021, ADR-0023

## Konteks

Sebelum keputusan ini, keputusan `hitl` dipakai untuk dua hal yang berbeda sifatnya:

1. **ADD.** Rencana lengkap dan dapat dieksekusi; yang belum pasti hanya nama dan domain
   property baru, yaitu keputusan pemodelan.
2. **DROP/RENAME yang tidak terbukti aman** (kolom kunci primer, template IRI, kunci join,
   predikat SQL, jalur yang melewati ekspresi view). ASCAM tetap menyusun tindakan, padahal
   tindakan itu dapat tidak lengkap. Contohnya A009: view `layanan.v_program_berakhir` harus
   didefinisikan ulang manusia. Administrator hanya dapat menyetujui atau menolak, tidak dapat
   menyunting rencana. Menyetujui rencana yang tidak lengkap berisiko, sehingga pada evaluasi F6
   rencana A009 selalu ditolak.

Administrator juga tidak memiliki tampilan untuk melihat elemen OBDF mana yang bergantung pada
sebuah kolom.

## Keputusan

1. **Tiga keputusan.**
   - `auto`: tidak berubah.
   - `hitl`: hanya untuk ADD, dan selalu berlaku. Kebijakan `adaptation.add_column` tidak lagi
     dapat membuat ADD otomatis.
   - `notify`: baru, untuk DROP/RENAME yang memenuhi kondisi tidak aman, operasi yang tidak
     didukung, dan RENAME tanpa nama baru.
2. **`notify` tidak memuat tindakan.** Rencana disimpan dengan status `notified` dan tanpa baris
   `plan_action`, sehingga tidak ada yang dapat disetujui atau dieksekusi; Executor hanya
   mengambil rencana `approved`. Dampak 𝒟(c, ℱ) disimpan pada `impact.affected` per lapisan:
   kolom, view (penutupan transitif atas `teiid_dependency`), TriplesMap, dan property.
   Notifikasi menyebut elemen tersebut, alasannya, dan langkah berikutnya: perbaikan manual
   lalu sinkronisasi.
3. **Revisi rencana ADD saat persetujuan.**
   - Formulir konsol mengirim `property_name` dan `domain`.
   - Knowledge memeriksa:
     - pola nama lokal;
     - domain harus salah satu kelas TriplesMap pengekspos;
     - bentrok IRI dengan entitas pada versi dasar, memakai aturan yang sama dengan analisis.
   - Bila pilihan sama dengan usulan, rencana asal disetujui.
   - Bila berbeda, karena baris `ops` bersifat append-only, revisi menjadi rencana baru
     (`revision_of`, status `approved`) dan rencana asal berstatus `revised`.
   - Pemetaan tidak ditempelkan ke TriplesMap yang subjeknya berkelas lain selain domain.
4. **Lineage kolom di konsol.**
   - `GET /obdf/{id}/lineage` membangun graf baca-saja dari `column_usage` versi aktif:
     kolom sumber ⊳ foreign table ⊳ view ⊳ TriplesMap ⊳ property. Endpoint ini memakai
     lineage yang sama dengan analisis dampak.
   - Konsol menyorot semua jalur yang melewati sebuah simpul.
   - Setiap notifikasi menaut ke lineage kolom yang berubah.
5. **Migrasi 0007** memperluas batasan `decision` dan `status` serta menambah `revision_of`.

## Bukti

- **Knowledge (158 uji).**
  - ADD selalu `hitl`, juga bila kebijakan lama `auto`.
  - DROP/RENAME tak aman menjadi `notify` tanpa tindakan, tidak dapat disetujui, ditolak, atau
    dieksekusi.
  - Notifikasi menyebut TriplesMap dan langkah sinkronisasi.
  - Revisi ADD menghasilkan rencana baru dan rencana asal `revised`.
  - Pilihan tidak sah ditolak 422 dengan tiga kasus: nama, domain di luar kelas, dan domain
    berbeda dari property yang ada.
  - Lineage A009 melewati `layanan.v_program_berakhir` dengan tepi `expression` sampai
    `tahunBerakhir`.
- **Pembanding kode lama dan baru pada artefak studi kasus** (analisis dampak yang sama):
  - Rencana A001 dan A004 identik byte demi byte.
  - Keputusan, tindakan, dan alasan A002, A003, A005–A008 identik.
  - Hanya A009 yang berubah: dari `hitl` dengan 3 tindakan menjadi `notify` tanpa tindakan,
    dengan alasan yang sama.
- **Konsol (25 uji).** Daftar izin meneruskan lineage dan pilihan property. Tampilan diperiksa
  dengan tangkapan layar:
  - persetujuan dengan formulir property;
  - revisi;
  - notifikasi;
  - lineage dengan sorotan A009.

## Konsekuensi terhadap evaluasi

- Ekspektasi A009 di harness berubah menjadi `notify`. Run dicatat `dinotifikasi`, dengan
  harapan:
  - tanpa tindakan;
  - notifikasi menyebut view;
  - tidak dieksekusi;
  - versi tidak berubah;
  - kueri terdampak tetap gagal.
- N_manual keputusan A009 menjadi 0, karena tidak ada keputusan yang diminta. Perbaikannya
  tetap manual.
- Hasil lama yang memuat `ditolak` tetap terbaca oleh `analisis.py` dan `ekstrak.py`.
- A001, A004, dan A009 perlu dijalankan ulang agar hasil yang dilaporkan berasal dari kode ini.
