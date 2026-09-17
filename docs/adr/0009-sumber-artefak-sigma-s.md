# ADR-0009: Sumber pengambilan artefak dan spesifikasi Σ_S untuk sync Knowledge

- **Status:** Diterima
- **Tanggal:** 2026-09-17
- **Fase MAPE-K:** Knowledge (sync), Execute (verifikasi)
- **Terkait:** D3, D9, ADR-0002, ADR-0003, ADR-0004, ADR-0006

## Konteks

Sync Knowledge memerlukan dua hal: **isi artefak** VDB (untuk versioning dan backup, D3) dan
**spesifikasi terstruktur** Σ_S. Knowledge Service tidak boleh bergantung pada folder bersama
dengan host Teiid (D9).

## Uji kelayakan F1.4a dan F1.4b

Semua nama operasi dan parameter ditanyakan ke server (`read-operation-names`,
`read-operation-description`), bukan diambil dari dokumentasi.

| Kandidat | Hasil |
|---|---|
| `SYSADMIN.VDBResources` | **Kosong** untuk VDB berbentuk XML (hanya terisi untuk arsip `.vdb`) |
| `/deployment=<nama>:read-content` + `useStreamAsResponse` | **Isi berkas asli**, 3 030 byte, identik byte-per-byte dengan berkas di disk; parameter URL dan header memberi hasil sama |
| Atribut deployment | `managed`, `enabled`, `status`, pemilik (`deployment-scanner`), dan **hash konten**; hash terbukti **SHA-1** dan cocok dengan isi unduhan |
| `get-vdb` | status VDB, connection type, model (nama, tipe, visibilitas), pemetaan sumber (`source-name`, `jndi-name`, `translator-name`), DDL mentah per model, `metadata-status`, `validity-errors` |
| `get-schema` | DDL hasil normalisasi Teiid per model (mis. `varchar(100)` → `string(100)`, PK dipisah) |
| `SYS.*` dan `SYSADMIN.*` lewat ODBC | Σ_S efektif: skema, tabel, kolom, `NameInSource`, kunci, dependensi view, definisi view (ADR-0003, ADR-0006) |
| `read-content` dengan parameter `path` | Gagal (`path` hanya untuk deployment *exploded*) |

## Keputusan

Sync Σ_S mengambil, seluruhnya lewat jaringan:

1. **Artefak `vdb_xml`**: `read-content` pada deployment VDB dengan `useStreamAsResponse`.
   Keutuhan diverifikasi dengan membandingkan SHA-1 isi unduhan terhadap hash yang dilaporkan
   atribut deployment; ketidakcocokan menghasilkan masalah konsistensi, bukan versi baru.
   Nama deployment diperoleh dari `get-vdb` (`properties.deployment-name`).
2. **Struktur Σ_S** (model, tabel, kolom, `NameInSource`, kunci, view, dependensi, routine):
   tabel sistem `SYS.*` dan `SYSADMIN.*` lewat transport ODBC — keadaan **efektif** runtime,
   yaitu setelah seluruh pernyataan `ALTER` diterapkan (ADR-0002).
3. **Status dan pemetaan sumber**: `get-vdb` (status, connection type, `metadata-status`,
   `validity-errors`, dan `source-mappings` per model). Pemetaan `source-name → source_system`
   inilah yang menautkan event skema ke model Teiid.
4. **DDL ternormalisasi** dari `get-schema` disimpan sebagai informasi tambahan pada Σ_S
   (untuk tampilan dan diff di UI), bukan sebagai artefak.
5. Bila `read-content` tidak tersedia (mis. deployment tidak dikelola server), artefak
   direkonstruksi dari `get-vdb` dan ditandai sebagai rekonstruksi, disertai masalah
   konsistensi agar terlihat di UI.

## Konsekuensi

- Sync tidak memerlukan akses berkas di host Teiid; cukup HTTP management API dan ODBC.
- Hash SHA-1 dapat dipakai untuk **deteksi drift**: perubahan berkas VDB di luar ASCAM
  terlihat dari perubahan hash tanpa perlu mengunduh isinya.
- Bila VDB berbentuk arsip `.vdb` (bukan XML), jalur artefak harus ditinjau ulang; pada
  batasan penelitian, artefak Teiid dibatasi pada berkas XML.
- Pengambilan ℳ dan 𝒯 dari host Ontop belum tercakup di sini; ditetapkan bersama rancangan
  Ontop Agent.

## Referensi

- WildFly Core 11.1.1.Final, `DomainUtil.java` (parameter `useStreamAsResponse` dan header
  `org.wildfly.useStreamAsResponse`): https://github.com/wildfly/wildfly-core/blob/11.1.1.Final/domain-http/interface/src/main/java/org/jboss/as/domain/http/server/DomainUtil.java
- Teiid Reference Guide: "Teiid Management CLI" (`get-vdb`, `get-schema`), "System schema".
- Hasil uji: `results/f1/f1_4a_20260917T171142.json`, `results/f1/f1_4b_20260917T171644.json` (lokal).
