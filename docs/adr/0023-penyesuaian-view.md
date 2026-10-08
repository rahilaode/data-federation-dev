# ADR-0023: Penyesuaian view Teiid saat kolom sumber dihapus

- **Status:** Diterima (uji integrasi pada Teiid 16 masih harus dijalankan)
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

## Konsekuensi

- Penghapusan kolom yang diteruskan view kini teradaptasi otomatis; kasus yang mengubah baris
  atau arti nilai tetap memerlukan keputusan manusia.
- Penerimaan `ALTER VIEW` di dalam metadata DDL VDB oleh Teiid 16 belum diuji pada lingkungan
  nyata; skenario evaluasi dengan view diperlukan sebelum klaim ini masuk paper.
