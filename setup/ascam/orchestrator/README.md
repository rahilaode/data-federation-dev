# ASCAM Orchestrator

Menghubungkan monitor skema (Kafka) dengan Knowledge: mengonsumsi pesan `ddl_event_log`,
menormalkannya menjadi event terformalisasi, lalu mengirimkannya ke Knowledge yang menganalisis
dampak dan membentuk rencana adaptasi (ADR-0016, ADR-0017).

Konfigurasi runtime diambil dari Knowledge: daftar sumber dan topiknya, serta alamat broker
Kafka. Yang perlu diset hanya URL Knowledge, berkas token, dan nama OBDF.

## Menjalankan

```bash
docker compose -f setup/ascam/orchestrator/docker-compose.yaml up -d --build
curl -s http://127.0.0.1:18200/health
```

`/health` menampilkan status pekerja, daftar topik, serta jumlah pesan, event terkirim,
rencana, event yang diabaikan, duplikat, dan kegagalan.

## Menguji

```bash
docker run --rm -v "$PWD/setup/ascam/orchestrator/service:/src:ro" python:3.12-slim sh -c \
  'cp -r /src /w && cd /w && pip install -q ".[test]" && python -m pytest -q'
```

Uji tidak memerlukan Kafka maupun Knowledge: konsumen dan klien Knowledge disuntik dengan tiruan.
