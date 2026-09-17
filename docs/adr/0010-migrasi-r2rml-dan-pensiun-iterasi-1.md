# ADR-0010: Mapping runtime beralih ke R2RML dan mesin adaptasi iterasi 1 dipensiunkan

- **Status:** Diterima
- **Tanggal:** 2026-09-17
- **Terkait:** batasan penelitian (mapping R2RML, Turtle), ADR-0004, ADR-0005, ADR-0007, ADR-0008

## Konteks

Batasan penelitian menetapkan ℳ berupa **R2RML dalam sintaks Turtle**. Sampai tahap ini
endpoint Ontop masih memuat `mapping.obda` (format native Ontop), sementara `mapping.ttl`
sudah tersedia dan kesetaraan jawabannya terbukti pada F0.5: jumlah triple per predikat (36),
jumlah instance per kelas (7), dan kueri skenario A001–A003 identik untuk `.obda`,
`mapping.ttl`, dan hasil konversi `ontop mapping to-r2rml`.

Mesin adaptasi iterasi 1 (`setup/ascam/adaptive-engine`) hanya dapat mengubah `.obda`, menerapkan
Σ′_S lewat folder deployment bersama, dan menyimpan pengetahuannya di berkas serta konstanta
kode. Ketiganya sudah digantikan keputusan arsitektur berikutnya.

## Keputusan

1. Endpoint Ontop memuat **`mapping.ttl`** (`ONTOP_MAPPING_FILE`); `mapping.obda` dihapus dari
   konfigurasi OBDF. Riwayatnya tetap tersimpan di Git.
2. Mesin adaptasi iterasi 1 **tidak dijalankan lagi** oleh `run.sh`. Kodenya dipertahankan
   sebagai bukti iterasi DSRM dan rujukan hasil eksperimen awal, disertai README yang
   menjelaskan status dan penggantinya.
3. Seluruh komponen baru menangani ℳ **hanya** sebagai R2RML; tidak ada jalur kode untuk
   format `.obda`.
4. Skrip `experiments/run_experiment.py` ditandai sebagai perkakas iterasi 1; versi untuk
   arsitektur baru disusun pada fase evaluasi.

## Konsekuensi

- Skenario A001–A003 tidak dapat dijalankan sampai Orchestrator dan Executor baru selesai.
  Hasil iterasi 1 tetap berlaku sebagai bukti tahap sebelumnya dan **wajib dijalankan ulang**
  pada arsitektur final (kesepakatan pada F1.1).
- Validasi ℳ memakai `ontop validate` terhadap versi VDB tertentu (ADR-0005) tetap berlaku.
- Kesetaraan jawaban setelah peralihan diverifikasi ulang terhadap endpoint yang berjalan
  memakai kueri uji dan kueri sidik jari graf.

## Referensi

- W3C (2012). R2RML: RDB to RDF Mapping Language. https://www.w3.org/TR/r2rml/
- ADR-0005 (`docs/adr/0005-mapping-r2rml-dan-validasi.md`), hasil uji F0.5.
