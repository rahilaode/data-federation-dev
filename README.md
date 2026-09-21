# data-federation-dev

Laboratorium tesis **ASCAM** (Adaptive Schema-Change Awareness and Mapping Maintenance) untuk
sistem federasi data berbasis ontologi (OBDF): PostgreSQL dan MySQL sebagai sumber, Teiid sebagai
federator, Ontop sebagai endpoint SPARQL, dan komponen ASCAM yang menjaga konsistensinya ketika
skema sumber berubah.

## Menjalankan

| Skrip | Kapan dipakai | Yang direset |
|---|---|---|
| `./jalankan.sh` | Sehari-hari, termasuk sebelum eksperimen | **Hanya ASCAM**: basis data Knowledge dihapus lalu disinkronkan ulang dari OBDF yang berjalan. OBDF, data sumber, dan Kafka tidak diubah |
| `./run.sh` | Membangun ulang lab dari nol | **Semuanya**, termasuk data sumber dan topik Kafka |

```bash
./jalankan.sh                     # menyalakan dan mereset ASCAM
./jalankan.sh --uji-rantai        # + uji rantai event (DDL uji sementara pada sumber)
./jalankan.sh --executor-dijeda   # Executor menyala dalam keadaan dijeda
```

Setelah selesai, buka konsol administrator di <http://127.0.0.1:18400> (pengguna `admin`,
kata sandi di `setup/ascam/knowledge/secrets/ui_admin_password`).

## Struktur

| Lokasi | Isi |
|---|---|
| `setup/data-source`, `setup/data-federation`, `setup/vkg-system` | OBDF studi kasus Bansos |
| `setup/ascam/schema-monitor` | Kafka, Kafka Connect, dan konektor Debezium |
| `setup/ascam/knowledge` | Knowledge Service (model runtime OBDF, analisis dampak, rencana) |
| `setup/ascam/orchestrator` | Konsumsi event DDL dari Kafka |
| `setup/ascam/executor` | Penerapan rencana secara blue-green |
| `setup/ascam/ontop-agent` | Akses artefak mapping dan ontologi di host Ontop |
| `setup/ascam/ui` | Konsol administrator web |
| `docs/adr` | Catatan keputusan arsitektur (ADR-0001 sampai ADR-0021) |
| `docs/evaluasi` | Protokol evaluasi dan ancaman validitas |
| `experiments` | Uji kelayakan, verifikasi, dan harness evaluasi |
