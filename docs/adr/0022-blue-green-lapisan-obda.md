# ADR-0022: Blue-green pada lapisan OBDA (dua instance Ontop di belakang proxy)

- **Status:** Diterima
- **Tanggal:** 2026-10-08
- **Fase MAPE-K:** Execute
- **Terkait:** ADR-0001, ADR-0004, ADR-0018, ADR-0020

## Konteks

ADR-0020 menerapkan blue-green pada lapisan federasi: versi VDB baru di-deploy di samping versi
yang melayani. Lapisan OBDA tetap satu instance Ontop yang di-restart di tempat (ADR-0001), dengan
tiga akibat yang terlihat pada evaluasi F6 (results/f6/20260930T114250):

1. endpoint SPARQL tidak tersedia selama restart (median 9–16 s per adaptasi);
2. restart adalah sumber ekor distribusi Δt_adapt (satu run 90,8 s, dengan restart 63,4 s);
3. verifikasi baru dapat dilakukan **sesudah** lalu lintas dialihkan, sehingga pengguna dapat
   melihat versi yang kemudian di-rollback.

Ontop tidak memiliki hot-reload untuk produksi (ADR-0001), sehingga artefak baru hanya dapat
dimuat oleh JVM yang baru dinyalakan.

## Keputusan

1. **Proxy menempati endpoint lama.** Kontainer nginx bernama `vkg-system-ontop-teiid` pada port
   8080 meneruskan permintaan ke instance aktif. Knowledge, harness, dan pengguna tidak perlu
   diubah.
2. **Dua instance, dua slot.** `vkg-system-ontop-blue` dan `vkg-system-ontop-green` masing-masing
   membaca `setup/vkg-system/slots/<warna>/` (mapping, ontologi, properti). Hanya instance aktif
   yang berjalan; instance siaga dibuat tetapi tidak dinyalakan (`up.sh`).
3. **Setiap instance dikunci pada satu versi VDB** lewat `;version=N` pada URL JDBC. Dengan
   begitu, peralihan proxy sekaligus mengalihkan lapisan federasi yang dibaca Ontop, dan rollback
   ke instance lama otomatis kembali ke versi VDB lama.
4. **Urutan eksekusi** (Executor, strategi `bluegreen`): deploy VDB N+1 → tulis ℳ′, 𝒯′ ke slot
   siaga dan validasi dengan volume instance siaga → nyalakan siaga → **verifikasi langsung ke
   instance siaga** → alihkan proxy (`nginx -s reload`, graceful) → promosikan artefak ke
   `config/` dan hentikan instance lama → versi N+1 menjadi `ANY`, versi N `NONE`.
5. **Kegagalan sebelum peralihan tidak terlihat pengguna.** Validasi, penyalaan, verifikasi, atau
   peralihan proxy yang gagal menghentikan instance siaga dan menghapus versi VDB baru; proxy,
   artefak kanonik, dan koneksi Teiid tidak berubah. Bila `nginx -s reload` gagal, agen
   mengembalikan berkas upstream lama.
6. **`config/` tetap kanonik** dan selalu mencerminkan instance aktif, sehingga sinkronisasi
   Knowledge membaca ℳ dan 𝒯 dari tempat yang sama seperti sebelumnya; promosi ke `config/`
   tetap memakai penulisan atomik dan cadangan ADR-0018.
7. **t_end** pada evaluasi menjadi akhir langkah `switch` (artefak baru melayani dan sudah
   terverifikasi), bukan akhir verifikasi pasca-restart.
8. Strategi lama tetap tersedia (`ASCAM_EXEC_OBDA_STRATEGY=restart`) untuk perbandingan.

## Konsekuensi

- Endpoint tidak terputus oleh adaptasi; waktu penyalaan instance siaga tetap masuk Δt_adapt,
  tetapi tidak lagi berupa waktu henti.
- Memori puncak bertambah satu JVM Ontop selama adaptasi berlangsung.
- Proxy menambah satu hop pada setiap kueri.
- Agen kini menyalakan dan menghentikan kontainer serta menjalankan `nginx -s reload` lewat
  socket Docker; hak aksesnya masih lebih luas dari yang diperlukan (keterbatasan yang sama
  dengan ADR-0018).
