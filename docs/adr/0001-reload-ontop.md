# ADR-0001: Mekanisme reload Ontop setelah adaptasi

- **Status:** Diterima
- **Tanggal:** 2026-09-17
- **Fase MAPE-K:** Execute (effector untuk 𝒯′ dan ℳ′)
- **Format:** mengikuti pola MADR (https://adr.github.io/madr/)

## Konteks

Ontop membaca ontologi, mapping, dan metadata basis data (dari Teiid) saat
inisialisasi. Setelah ASCAM menulis 𝒯′ dan ℳ′, Ontop harus dimuat ulang.
Pada eksperimen awal, reload Ontop menyumbang ±68–80% dari Δt_adapt
(±19 s dari ±22–28 s).

Ontop 4.1.1 tidak menyediakan API *hot-reload* untuk produksi. Opsi yang
tersedia (dokumentasi CLI Ontop dan source tag `ontop-4.1.1`):

- restart kontainer;
- *development mode* (`--dev`): pemantau berkas dan `POST /ontop/restart` yang
  menutup lalu menjalankan ulang Spring context di JVM yang sama
  (`AutoRestartController.java`, `OntopEndpointApplication.restart()`); hanya aktif
  bila `dev=true` dan tanpa autentikasi;
- `--lazy` (inisialisasi ditunda sampai kueri pertama);
- `--db-metadata` (metadata basis data dari berkas JSON, stabil sejak 4.1.0).

## Temuan (uji F0.1)

**Dekomposisi restart kontainer (konfigurasi awal, n=3, dari log berstempel waktu):**

| Fase | Durasi |
|---|---|
| Penghentian kontainer lama | 11,33–11,84 s |
| JVM + Spring + Tomcat siap | 1,50–2,37 s |
| Inisialisasi Ontop (ontologi, mapping, metadata) | 3,09–3,34 s |
| Tomcat start → kueri pertama dijawab | 1,07–1,59 s |
| **Total** | 17,24–18,71 s |

**Akar masalah.** `wait-for-it.sh` meng-`exec` `/opt/ontop/entrypoint.sh`, sehingga
bash menjadi PID 1; `entrypoint.sh` Ontop 4.1.1 menjalankan `java` **tanpa** `exec`
(Java berjalan sebagai PID 13/14). Proses PID 1 tanpa handler sinyal tidak
dihentikan oleh SIGTERM, dan shell tidak meneruskan sinyal ke proses anaknya,
sehingga Docker menunggu batas waktu 10 s lalu mengirim SIGKILL.

**Perbandingan varian (n=3 per varian, `docker stop -t 10` lalu `docker start`, polling 0,2 s):**

| Varian | Konfigurasi | Hierarki proses | Stop | Start → siap | Total | Shutdown Spring rapi |
|---|---|---|---|---|---|---|
| A | awal | bash → java | 10,58–11,03 s | 9,40–12,74 s | 19,98–23,77 s | 0/3 |
| B | `init: true` | init → bash → java | 0,51–0,60 s | 9,07–9,63 s | 9,67–10,18 s | 0/3 |
| C | B + `TINI_KILL_PROCESS_GROUP=1` | init → bash → java | 0,51–0,55 s | 8,80–10,30 s | 9,31–10,85 s | 0/3 |
| **D** | `init: true` + `exec java` | **init → java** | 0,80–0,93 s | 8,86–9,49 s | 9,68–10,42 s | **3/3** (exit code 0) |

Indikator shutdown rapi adalah baris log `Shutting down ExecutorService`, yang pada
Spring Framework 5.2.8 (dipakai Spring Boot 2.3.2 pada Ontop 4.1.1) dicatat di level
INFO, sama dengan `Initializing ExecutorService` yang muncul saat start
(`ExecutorConfigurationSupport.java`, baris 180–181 dan 217–218).

## Opsi yang dipertimbangkan

1. **R1 – restart kontainer**, dengan konfigurasi A, B, C, atau D.
2. **R2 – restart di dalam JVM** lewat `POST /ontop/restart` (mode dev). Ditolak:
   fitur pengembangan tanpa autentikasi; setelah perbaikan D, keuntungan waktunya
   tinggal pada fase start Docker/JVM.
3. **R3 – blue-green** (instance baru disiapkan, trafik dipindah lewat proxy).
   Ditunda: menghilangkan downtime untuk kueri yang tidak terdampak, tetapi
   membutuhkan reverse proxy; dicatat sebagai saran penelitian lanjutan.

## Keputusan

Menggunakan **R1 dengan konfigurasi D**: `init: true` pada layanan Ontop dan
entrypoint Ontop 4.1.1 yang meng-`exec` Java
(`setup/vkg-system/entrypoint/entrypoint-4.1.1-exec.sh`).

## Konsekuensi

- **Positif:** waktu restart turun ±53% (rerata 21,37 s → 9,99 s pada n=3);
  JVM berhenti secara rapi; tidak bergantung pada fitur pengembangan Ontop.
- **Negatif:** repository merawat salinan entrypoint yang terikat versi 4.1.1.
  Mitigasi: perubahan hanya satu baris, cara membuat ulang didokumentasikan, dan
  Ontop 5.0.0 sudah memakai `exec java` sehingga salinan ini dapat dihapus saat upgrade.
- **Tetap berlaku:** selama restart (±9–10 s) seluruh kueri ke endpoint gagal.
  Fase start (Docker, JVM, Spring, inisialisasi Ontop) kini menjadi komponen terbesar.
- **Untuk evaluasi:** eksperimen utama harus dijalankan ulang dengan konfigurasi ini;
  angka n=3 di atas hanya bukti kelayakan.

## Referensi

- Ontop CLI: https://ontop-vkg.org/guide/cli
- Entrypoint Ontop 4.1.1: https://github.com/ontop/ontop/blob/ontop-4.1.1/client/docker/entrypoint.sh
- Entrypoint Ontop 5.0.0 (`exec java`, baris 222): https://github.com/ontop/ontop/blob/ontop-5.0.0/client/docker/entrypoint.sh
- Restart dalam JVM (mode dev): https://github.com/ontop/ontop/blob/ontop-4.1.1/client/endpoint/src/main/java/it/unibz/inf/ontop/endpoint/controllers/AutoRestartController.java
- Penanganan sinyal PID 1 dan init: https://github.com/Yelp/dumb-init
- Spring `ExecutorConfigurationSupport` 5.2.8: https://github.com/spring-projects/spring-framework/blob/v5.2.8.RELEASE/spring-context/src/main/java/org/springframework/scheduling/concurrent/ExecutorConfigurationSupport.java
