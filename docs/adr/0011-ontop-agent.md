# ADR-0011: Ontop Agent sebagai satu-satunya jalur ke artefak OBDA

- **Status:** Diterima
- **Tanggal:** 2026-09-18
- **Fase MAPE-K:** Knowledge (sync ℳ dan 𝒯), Execute (penerapan artefak, F4)
- **Terkait:** D9, ADR-0001, ADR-0005, ADR-0008, ADR-0009

## Konteks

Σ_S dapat diambil dan diterapkan sepenuhnya lewat jaringan (ADR-0004, ADR-0009). Untuk ℳ dan
𝒯 tidak ada jalur setara: Ontop membaca keduanya sebagai **berkas**, dan tidak menyediakan API
untuk membaca maupun mengganti berkas tersebut (ADR-0005). Karena itu, komponen ASCAM yang
berada di host lain tidak dapat menyentuh artefak Ontop tanpa berbagi filesystem, yang
bertentangan dengan D9.

## Keputusan

Sebuah **agen** dijalankan berdampingan dengan Ontop (pola *sidecar*), dan menjadi satu-satunya
komponen yang menyentuh berkas artefak:

1. **Antarmuka**: `GET /health` (tanpa token, untuk uji koneksi dan healthcheck),
   `GET /api/v1/artifacts` (daftar + SHA-256 + ukuran + waktu ubah),
   `GET /api/v1/artifacts/{kind}` (isi), `POST /api/v1/validate`.
   Penerapan artefak, backup, dan reload Ontop ditambahkan pada F4.
2. **Autentikasi** bearer token per klien (berkas `<klien>:<token>`), dibandingkan dengan waktu
   konstan. Token disimpan Knowledge sebagai kredensial terenkripsi, sehingga jalur
   Knowledge → agen memakai mekanisme kredensial yang sama dengan target lain.
3. **Artefak yang diekspos dibatasi** pada `r2rml` dan `ontology`. Berkas properti Ontop
   **tidak pernah** dibaca lewat API karena memuat kredensial JDBC; berkas itu hanya dipakai
   di dalam agen ketika menjalankan `ontop validate`. Nama berkas dipetakan dari jenis artefak,
   sehingga jalur berkas sembarang tidak dapat diminta lewat API.
4. **Sidik jari SHA-256** per artefak memungkinkan Knowledge mendeteksi perubahan (drift) tanpa
   mengunduh isi, sejalan dengan pemakaian hash SHA-1 deployment pada Σ_S (ADR-0009).
5. **Menjalankan CLI Ontop**: agen menjalankan kontainer sekali jalan dari image Ontop yang sama
   dengan `volumes_from` kontainer Ontop dan jaringan yang sama. Dengan begitu agen tidak perlu
   mengetahui path host mana pun, dan berkas yang divalidasi dijamin identik dengan yang dipakai
   endpoint.
6. **Uji koneksi** target `ontop_agent` memeriksa `/health` dan, bila kredensial tersedia,
   memastikan token diterima serta merekam sidik jari artefak.

## Konsekuensi

- Knowledge, Orchestrator, dan Executor bekerja sepenuhnya lewat jaringan; hanya agen yang
  memerlukan akses berkas dan Docker socket di host Ontop.
- Docker socket memberi hak istimewa besar pada agen. Untuk lingkungan riset hal ini diterima
  dan dicatat sebagai keterbatasan; alternatifnya (mis. antarmuka orkestrator yang lebih sempit)
  dicatat sebagai pekerjaan lanjutan.
- Volume artefak dipasang **read-only** pada tahap ini; F4 mengubahnya menjadi baca-tulis
  bersamaan dengan penerapan artefak, backup, dan reload.
- Agen menambah satu komponen yang harus dijalankan dan dirawat pada setiap host Ontop.

## Bukti

12 uji (`setup/ascam/ontop-agent/service/tests/`): `/health` terbuka dan melaporkan keberadaan
artefak; ketiga endpoint lain menolak permintaan tanpa token maupun dengan token salah; sidik
jari SHA-256 sesuai isi berkas; berkas properti tidak dapat diminta dengan nama apa pun
(termasuk upaya penelusuran path) dan kredensialnya tidak pernah muncul; artefak hilang
dilaporkan; `validate` meneruskan `db_url`; kegagalan validasi dilaporkan apa adanya; pembungkus
Docker memakai `volumes_from`, jaringan kontainer Ontop, perintah CLI yang benar, dan
membersihkan kontainer sekali jalan. Di sisi Knowledge, 2 uji tambahan (total 50) memastikan
token agen dipakai dan token yang ditolak dilaporkan tanpa membocorkan token.
