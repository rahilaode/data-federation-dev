# Konsol administrator ASCAM

Antarmuka web untuk memantau siklus adaptasi dan **memutuskan usulan perubahan** yang tidak
dijalankan otomatis, terutama penambahan kolom (ADR-0021).

| Halaman | Isi |
|---|---|
| Ringkasan | Keadaan siklus MAPE-K, kesehatan komponen, perubahan dan eksekusi terakhir |
| Persetujuan | Usulan yang menunggu keputusan: apa yang berubah di sumber, apa yang akan dilakukan pada VDB, mapping, dan ontologi, serta alasannya |
| Perubahan skema | Setiap DDL yang ditangkap monitor beserta hasil analisisnya |
| Rencana dan eksekusi | Rencana, keputusan, dan langkah penerapan beserta durasinya |
| Versi spesifikasi | Riwayat versi dan perbandingan artefak antarversi |
| Analisis dampak | Simulasi perubahan skema tanpa mengubah apa pun |
| Konfigurasi | Sumber, komponen OBDF (dengan pemeriksaan koneksi), dan kebijakan penambahan kolom |
| Jejak audit | Seluruh tindakan beserta pelakunya |

## Menjalankan

```bash
setup/ascam/knowledge/scripts/init-secrets.sh      # membuat kata sandi dan kunci sesi konsol
docker compose -f setup/ascam/ui/docker-compose.yaml up -d --build
```

Buka <http://127.0.0.1:18400>, masuk sebagai `admin` dengan kata sandi di
`setup/ascam/knowledge/secrets/ui_admin_password`.

## Keamanan

- Peramban tidak pernah menerima token layanan; konsol meneruskan permintaan ke Knowledge
  lewat daftar izin (pembacaan, keputusan rencana, notifikasi, pemeriksaan koneksi, sync,
  analisis dampak, kebijakan adaptasi).
- Sesi disimpan pada cookie `HttpOnly`, `SameSite=Strict`, bertanda tangan.
- Permintaan yang mengubah keadaan wajib membawa header `X-ASCAM-UI`.
- Nama administrator diteruskan ke Knowledge, sehingga jejak audit mencatat siapa yang
  menyetujui atau menolak.
- Port hanya terikat ke `127.0.0.1`.

## Menguji

```bash
docker run --rm -v "$PWD/setup/ascam/ui/service:/src:ro" python:3.12-slim sh -c \
  'cp -r /src /w && cd /w && pip install -q ".[test]" && python -m pytest -q'
```
