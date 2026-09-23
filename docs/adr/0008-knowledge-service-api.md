# ADR-0008: Knowledge Service — API registry, autentikasi, enkripsi kredensial, konfigurasi deklaratif

- **Status:** Diterima
- **Tanggal:** 2026-09-17
- **Fase MAPE-K:** Knowledge (akses bersama oleh Orchestrator, Executor, dan UI)
- **Terkait:** D1, D4, D5, ADR-0007

## Konteks

D1 menetapkan satu layanan sebagai satu-satunya pengakses basis data Knowledge. Layanan itu
harus menerima registrasi OBDF dan target (termasuk yang berada di host lain), menyimpan
kredensial dengan aman, dapat dikonfigurasi secara deklaratif saat start, dan mencatat setiap
perubahan untuk audit.

## Keputusan

1. **FastAPI + SQLAlchemy (sinkron)**. Endpoint sinkron dijalankan FastAPI di threadpool; satu
   transaksi per permintaan (commit bila sukses, rollback bila gagal). Endpoint `/health`
   (liveness) dan `/ready` (basis data terjangkau dan revisi skema = revisi migrasi terbaru).
2. **Autentikasi antarlayanan dengan bearer token** per klien (`ui`, `orchestrator`, `executor`)
   dari Docker secret. Token dibandingkan dengan waktu konstan atas digest SHA-256. Aktor audit =
   nama klien, ditambah pengguna UI dari header `X-ASCAM-User`. Autentikasi pengguna akhir UI
   (mis. OIDC) di luar cakupan tahap ini.
3. **Kredensial dienkripsi di aplikasi** dengan Fernet (AES-128-CBC + HMAC-SHA256). Kunci dari
   Docker secret, satu per baris dengan kunci terbaru di baris pertama; dekripsi memakai seluruh
   kunci (MultiFernet) sehingga rotasi kunci tidak memutus kredensial lama. Rahasia tidak pernah
   dikembalikan API maupun dicatat di audit.
4. **Kredensial bernama** (migrasi 0002) agar konfigurasi dan UI dapat merujuknya.
5. **Konfigurasi deklaratif (D5)**: dokumen YAML/JSON versi 1 (OBDF, sumber, kredensial,
   target, pengaturan, kebijakan penamaan, pemetaan tipe) diterapkan **idempoten** lewat
   `POST /api/v1/config/apply`, CLI `ascam-knowledge apply-config`, atau otomatis saat start
   (`ASCAM_KNOWLEDGE_BOOTSTRAP_CONFIG`). Rahasia dalam dokumen hanya boleh dirujuk dengan
   `secret_file`; penerapan bersifat non-destruktif (objek yang tidak disebut tidak dihapus) dan
   atomik (gagal sebagian → seluruhnya di-rollback).
6. **Aturan per jenis target** divalidasi di API: `teiid_mgmt` dan `teiid_odbc` wajib memiliki
   kredensial; `teiid_odbc` wajib menyebut nama VDB; kredensial harus milik OBDF yang sama.
7. **Penanda kompatibilitas OWL 2 QL** pada pemetaan tipe dihitung dari daftar datatype OWL 2 QL
   (W3C OWL 2 Profiles, §3.2.3) dan tidak dapat diisi bertentangan dengan daftar tersebut.
8. Layanan berjalan sebagai role `ascam_app` (DML saja); migrasi 0002 memberi role ini hak baca
   `alembic_version` agar `/ready` berfungsi.
9. **Uji koneksi target** dijalankan di dalam layanan dengan kredensial terdekripsi dan dicatat di
   `registry.connection_check` (hasil terstruktur JSONB, migrasi 0003) beserta aktornya:
   `teiid_mgmt` (Digest; `server-state`, versi produk, daftar VDB dan status/connection type),
   `teiid_odbc` (`SYS.VirtualDatabases`), `ontop_sparql` (kueri `ASK`), `kafka` (metadata broker dan
   keberadaan topik setiap sumber), `ontop_agent` (`/health`). Target nonaktif tidak dihubungi.
   Pesan galat disaring dari rahasia sebelum disimpan. Endpoint: uji satu target, uji semua target
   aktif, riwayat, dan status terkini per target (dasbor UI).

## Bukti

38 uji (`setup/ascam/knowledge/service/tests/`), termasuk: token hilang/salah/skema lain ditolak;
OBDF duplikat → 409 dengan nama constraint; kredensial tidak muncul di respons, daftar, maupun
audit dan tersimpan terenkripsi; rotasi kunci; aturan target (termasuk PATCH yang melanggar →
422 dan kredensial OBDF lain → 404); penanda OWL 2 QL; penerapan konfigurasi idempoten,
mendeteksi perubahan rahasia, dan di-rollback bila gagal; bootstrap saat start; `/ready` dan DML
saat berjalan sebagai `ascam_app` (kegagalan `/ready` pada role ini ditemukan oleh uji tersebut
dan diperbaiki di migrasi 0002). Uji CLI terhadap konfigurasi OBDF bansos: 46 objek dibuat,
penerapan kedua 46 objek tidak berubah. Uji koneksi (10 uji tambahan, total 48): jalur sukses
dan penolakan kredensial Digest, status server bukan `running`, galat yang memuat kata sandi
disaring, soket tak terjangkau, endpoint SPARQL tidak tersedia, topik Kafka yang hilang,
target nonaktif, pencatatan riwayat, status terkini, dan audit. Migrasi 0003 mempertahankan
teks hasil lama saat dinaikkan dan diturunkan.

## Konsekuensi

- Orchestrator, Executor, dan UI tidak memerlukan akses basis data maupun kunci enkripsi.
- Token antarlayanan dan kunci enkripsi harus dirotasi sebagai bagian operasi rutin.
- Uji koneksi memerlukan jaringan dari Knowledge Service ke setiap target; untuk target di host
  lain, jalur jaringan dan TLS (`endpoint.tls`) perlu disiapkan.
- Uji koneksi dijalankan sinkron di dalam permintaan (timeout 5 s per target); penjadwalan
  berkala dicatat sebagai pekerjaan lanjutan.

## Referensi

- FastAPI — Security (HTTP Bearer): https://fastapi.tiangolo.com/tutorial/security/
- cryptography — Fernet dan MultiFernet: https://cryptography.io/en/latest/fernet/
- W3C (2013). SPARQL 1.1 Protocol. https://www.w3.org/TR/sparql11-protocol/
- W3C (2012). OWL 2 Web Ontology Language Profiles (Second Edition), §3.2.3. https://www.w3.org/TR/owl2-profiles/

## Revisi F6: commit sebelum respons

**Temuan.** Transaksi semula di-commit di penutup dependensi `get_db`. Pada FastAPI modern,
penutup ini berjalan *setelah* respons diterima klien (terukur: respons 56 ms, commit 556 ms
pada uji dengan commit tiruan yang lambat). Executor memanggil `/sync` lalu segera `/finish`
dengan `candidate_spec_version_id` versi yang baru dibuat; bila `/finish` mendahului commit,
kunci asingnya dilanggar dan Knowledge menjawab 409. Pada evaluasi F6, 5 dari 60 run
perlakuan tertinggal berstatus `running` karena hal ini, padahal adaptasinya sendiri selesai.
Cacat yang sama berlaku bagi setiap klien yang langsung memakai hasil sebuah penulisan,
termasuk Orchestrator yang meng-commit offset Kafka setelah event "diterima".

**Keputusan.** Seluruh router memakai `TransactionalRoute`, yang meng-commit setelah handler
selesai tetapi sebelum respons dikembalikan, dan melakukan rollback bila handler atau commit
gagal; galat integritas saat commit tetap dijawab 409. `get_db` hanya menutup sesi, dengan
commit cadangan disertai peringatan bila sebuah rute tidak memakai kelas tersebut.

**Bukti.** Uji `test_transaksi.py`: urutan commit terhadap `http.response.start` (gagal pada
kode lama dengan urutan `['respons', 'commit']`), permintaan gagal tidak di-commit, galat
integritas saat commit dijawab 409, dan setiap rute aplikasi memakai `TransactionalRoute`
(termasuk router yang disimpan sebagai `_IncludedRouter` pada FastAPI baru). 125 uji lolos.
