# Adaptive Engine (iterasi 1) — tidak lagi dijalankan

Komponen ini adalah **iterasi pertama** ASCAM: satu proses yang menggabungkan Analyze, Plan,
dan Execute, memakai mapping Ontop format native `.obda`, dan menyimpan status di berkas.

Kode dipertahankan sebagai bukti iterasi DSRM dan sebagai rujukan hasil eksperimen awal
(A001–A003 dan B001–B003, `results/`), tetapi **tidak dijalankan lagi** oleh `run.sh`, karena:

1. ℳ kini berupa **R2RML (Turtle)**; mesin ini hanya dapat mengubah `.obda` (ADR-0005).
2. Σ′_S kini diterapkan lewat **management API dan versi VDB (blue-green)**, bukan folder
   deployment bersama (ADR-0004).
3. Pengetahuan sistem kini berada di komponen **Knowledge** (ADR-0007, ADR-0008), bukan di
   berkas dan konfigurasi statis.

Penggantinya: Knowledge (`setup/ascam/knowledge/`), lalu Orchestrator dan Executor
(sedang dibangun). Temuan dari iterasi ini yang dibawa ke iterasi kedua tercatat di
ADR-0001 s.d. ADR-0006, termasuk: penulisan artefak yang atomik, verifikasi setelah eksekusi,
revert, penulisan ontologi secara append-only, dan penanganan klausa SELECT eksplisit.
