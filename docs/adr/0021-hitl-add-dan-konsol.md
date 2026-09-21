# ADR-0021: Penambahan kolom memerlukan persetujuan administrator lewat konsol web

- **Status:** Diterima
- **Tanggal:** 2026-09-22
- **Fase MAPE-K:** Plan (keputusan), Knowledge (pemantauan)
- **Terkait:** D11, ADR-0008, ADR-0015, ADR-0016, ADR-0020

## Konteks

Di antara tiga operasi yang ditangani ASCAM, hanya ADD yang **menambah komitmen semantik baru**
ke TBox: IRI property baru, domain, range, dan label yang akan dilihat seluruh konsumen hilir.
RENAME tidak mengubah TBox (strategi alias lewat `NAMEINSOURCE`), sedangkan DROP hanya menandai
property usang. Kualitas penamaan kosakata baru adalah keputusan yang pantas diambil manusia.
Administrator juga memerlukan satu tempat untuk memantau seluruh siklus adaptasi.

## Keputusan

1. **Kebijakan `adaptation.add_column` diubah menjadi `hitl`** pada konfigurasi deklaratif.
   Kebijakan tetap dapat diubah kembali ke `auto` lewat konsol.
2. **Rencana HITL harus lengkap.** Analisis dampak tidak lagi berhenti ketika kebijakan
   mewajibkan persetujuan; rencana tetap memuat tindakan VDB, ontologi, dan mapping. *Bug yang
   ditemukan:* sebelumnya rencana HITL hanya berisi tindakan VDB, sehingga setelah disetujui
   property dan pemetaannya tidak pernah dibuat.
3. **Notifikasi yang menjelaskan.** Pesan notifikasi menyebut operasi, sumber, objek, jumlah
   tindakan, dan alasannya; notifikasi dapat ditandai sudah dibaca dan otomatis tertandai saat
   rencana diputuskan.
4. **Konsol web dengan pola backend-for-frontend** (`setup/ascam/ui`). Peramban tidak pernah
   memegang token layanan; permintaan ke Knowledge diteruskan lewat daftar izin yang hanya
   memuat pembacaan, keputusan rencana, notifikasi, pemeriksaan koneksi, sync, analisis dampak,
   dan kebijakan adaptasi. Sesi memakai cookie `HttpOnly`, `SameSite=Strict`, bertanda tangan;
   permintaan yang mengubah keadaan wajib membawa header `X-ASCAM-UI`.
5. **Jejak audit mencatat manusianya.** Konsol meneruskan nama administrator sebagai
   `X-ASCAM-User`; Knowledge hanya menerimanya dari klien `ui` dan hanya dengan format yang
   dibatasi, sehingga keputusan tercatat sebagai `ui:<nama>` dan header tidak dapat dipakai
   klien lain untuk mengaku sebagai administrator.
6. **Warna lapisan konsisten** di seluruh konsol: Σ_S/VDB, ℳ/mapping, dan 𝒯/ontologi memiliki
   warna tetap, sehingga setiap tindakan dalam rencana langsung terbaca lapisannya.

## Konsekuensi terhadap evaluasi

Kriteria C_FMI di proposal (§3.11.2, pers. 3.18) mensyaratkan N_manual = 0. Dengan keputusan
ini, A001 memiliki **N_manual = 1** (satu persetujuan) sedangkan penyuntingan artefak manual
tetap nol. Metrik perlu direvisi agar membedakan **keputusan** dari **perbaikan**, dan
Δt_adapt dilaporkan sebagai waktu mesin (tanpa jeda keputusan manusia) serta waktu ujung ke
ujung. Harness evaluasi menyetujui rencana lewat API yang sama dengan konsol atas nama
`evaluator` dan mencatat keduanya.

## Bukti

Knowledge (121 uji): rencana ADD berkebijakan HITL memuat tindakan VDB, ontologi, dan mapping;
notifikasi HITL menjelaskan perubahan dan dapat ditandai dibaca; persetujuan menandai
notifikasinya; nama administrator tercatat pada keputusan; header pelaku dari klien lain atau
dengan format berbahaya diabaikan. Konsol (23 uji): halaman dan berkas statis tersaji; seluruh
API memerlukan sesi; kata sandi salah ditolak; cookie `HttpOnly` dan `SameSite=Strict`; token
layanan tidak pernah sampai ke peramban; jalur di luar daftar izin ditolak (termasuk registri,
kredensial, `supersede`, penerimaan event, dan namespace ontologi); perubahan tanpa header CSRF
ditolak; persetujuan membawa identitas dan catatan administrator; kesehatan layanan tetap
tersaji meski satu layanan mati; perbandingan artefak antarversi (termasuk regresi pola yang
menolak `r2rml`); kendali jeda dan lanjut Executor. Tampilan diperiksa dengan tangkapan layar
pada lebar desktop dan layar sempit.
