# Modifikasi federasi, mapping, dan ontologi NPD untuk validasi Plan (branch `feature/npd_v2`)

Dokumen ini menjelaskan apa yang ditambahkan ke studi kasus NPD di branch `feature/npd_v2`,
mengapa ditambahkan, dan aturan Plan mana yang diuji oleh setiap elemen. Tujuannya ada dua.
Pertama, setiap aturan Plan di paper bisa dipicu pada tabel dan data NPD yang asli. Kedua,
potongan kecil NPD bisa dipakai sebagai contoh di bagian Preliminaries paper.

Semua elemen dibuat oleh satu skrip, `setup/npd/tools/validation.py`, dan dicatat sebagai
ADR-0034 di `docs/design-decisions.md`. Langkah evaluasi manual per skenario ada di
[`npd-v2-manual-evaluation.md`](npd-v2-manual-evaluation.md).

## 1. Prinsip

1. **Sumber data tidak diubah.** Skema PostgreSQL `reporting` dan MySQL `registry` tetap seperti
   dump NPD. Perubahan skema sumber hanya terjadi saat skenario dijalankan, karena DDL itulah
   yang ditangkap Monitor.
2. **Menambah, bukan mengubah.** Tidak ada foreign table, view, triples map, kelas, atau
   property NPD yang diubah atau dihapus. Semua tambahan ditandai di artefaknya:
   - di VDB, di antara komentar `/* >>> validation views ... */` dan `/* <<< validation views */`;
   - di mapping dan ontologi, di antara `# >>> ASCAM validation layer` dan `# <<< ASCAM validation layer`.
3. **Kosakata sendiri.** Kelas dan property baru memakai namespace
   `npdx: <https://github.com/rahilaode/data-federation-dev/npd-validation#>`. Tidak ada axiom
   yang menghubungkan `npdx` dengan kosakata NPD, dan tidak ada triples map baru yang memakai
   kelas atau property NPD. Akibatnya, triple yang bisa dibaca oleh 31 query NPD tidak berubah.
   Ini argumen konstruksi, bukan hasil pengukuran. Pengukurannya bisa dilakukan dengan
   `setup/npd/tools/compare_endpoints.py` di stack.
4. **Satu pengecualian di namespace NPD.** Dua property deprecated, `npdv:wlbEntryYear` dan
   `npdv:fclStatus`, sengaja diletakkan di namespace NPD. Naming policy studi kasus menulis
   property baru sebagai `{namespace}{column_camel}` dengan namespace NPD (`obdf-npd.yaml`). Reuse
   hanya diusulkan untuk IRI yang dihasilkan policy itu, jadi property yang akan dipakai ulang
   harus ada di namespace tersebut. Keduanya ditulis sebagai blok deprecation ASCAM, seolah-olah
   kolomnya pernah di-drop oleh P-002. Deprecation yang ditulis tangan tidak bisa dicabut ASCAM.

## 2. Perubahan pada federated schema Σ

### 2.1 Foreign column tambahan

| Foreign table | Kolom baru | NAMEINSOURCE | Alasan |
|---|---|---|---|
| `registry.company` | `cmpGroupName string(100)` | `` `cmpGroupName` `` (kolom ini tidak ada di sumber) | Memicu baris *rename* Tabel 1: "Another column of τ̂ already reads c′". Kolom ini mewakili keadaan Σ yang tertinggal dari sumber, misalnya setelah perubahan yang dulu hanya dinotifikasi dan belum diperbaiki. `RENAME cmpGroup TO cmpGroupName` lalu menemukan c′ sudah dibaca kolom lain. |

Kolom ini tidak dibaca oleh mapping mana pun. Query yang menyebut kolom `company` satu per satu
tidak terpengaruh. `SELECT *` atas `company` akan gagal karena kolom sumbernya tidak ada, tetapi
tidak ada mapping atau view yang melakukannya.

### 2.2 View baru di model `services` (Σ_V)

| View | Definisi (diringkas) | Aturan Plan yang diuji | Alasan desain |
|---|---|---|---|
| `v_current_operator` | `SELECT cmpNpdidCompany, cmpOrgNumberBrReg AS orgNumber FROM registry.company WHERE cmpLicenceOperCurrent = 'Y'` | Example 1–3 dan kalimat pembuka Plan | Padanan `v_active` di paper: ada filter di WHERE dan kolom yang diganti nama. `cmpOrgNumberBrReg` tidak dibaca mapping NPD mana pun, sehingga 𝒟(c,𝓕) persis {ĉ, kolom view `orgNumber`, a₂}, sama dengan Example 2. `cmpLicenceOperCurrent` bernilai `'Y'`/`'N'`; mapping NPD sendiri memakai kolom ini di WHERE (Mapping:00414, 00415). |
| `v_wellbore_overview` | `SELECT * FROM reporting.wellbore_npdid_overview` | P-001: "views that select * from τ̂ pass it on" | View `SELECT *` biasa. Ia menerima kolom baru dan **bukan** kondisi notifikasi. Kontrol negatif untuk baris *add* Tabel 1. |
| `v_point_field` | `SELECT wlbField FROM reporting.wlbPoint` | Tabel 1 (drop): "would lose all its columns" | View satu kolom. `wlbField` tidak dibaca mapping NPD, jadi notifikasinya hanya karena view ini. |
| `v_pipeline_operator` | `("pipNpdidPipe" integer, "pipNameCurrentOperator" string) AS SELECT pipNpdidPipe, pipNameCurrentOperator FROM registry.pipLine` | Tabel 1 (drop): "declares its columns inline" | Nama kolom inline sama dengan nama kolom tabel. Kalau namanya berbeda, Knowledge menganggap kolom itu ekspresi dan memberi alasan *expression*, sehingga kondisi *inline* tidak teruji tersendiri. |
| `v_field_text_cache` | `OPTIONS (MATERIALIZED 'TRUE') AS SELECT fldNpdidField, fldDescriptionText FROM registry.field_description` | Tabel 1 (drop): "is materialized" | Materialisasi internal Teiid. `fldDescriptionText` bukan bagian primary key, sehingga alasannya tidak tercampur dengan kondisi *primary key*. |
| `v_field_status` | `SELECT * FROM registry.field_activity_status_hst WHERE fldStatus = 'PRODUCING' UNION ALL SELECT <tujuh kolom> FROM ... WHERE fldStatus <> 'PRODUCING'` | Tabel 1 (add): "uses UNION, EXCEPT, or INTERSECT" | Hanya cabang pertama yang memakai `*`. Kolom baru akan masuk ke cabang pertama saja, sehingga jumlah kolom kedua cabang berbeda. Lihat temuan 6.1 tentang kenapa kedua cabang tidak dibuat `SELECT *`. |

> [GAMBAR] Diagram model `services` dan foreign table yang dibacanya: enam view baru di samping
> tiga view lama (`v_wellbore_mud`, `v_field_investment`, `v_wellbore_core_ft`).

## 3. Perubahan pada mapping ℳ

Sepuluh triples map baru dengan IRI `<Validation:…>`. Gaya penulisannya sama dengan mapping NPD
hasil generator: query dengan identifier ber-delimiter dan template `{"kolom"}`. Kecuali
`FacilityRecord`, semua subjek memakai pola IRI data NPD
(`http://sws.ifi.uio.no/data/npd-v2/...`). Dengan begitu, IRI subjek sama dengan subjek NPD.

| Triples map | Logical table | Subjek, kelas | Property | Untuk |
|---|---|---|---|---|
| `CurrentOperator` | `SELECT * FROM services.v_current_operator` | `company/{cmpNpdidCompany}`, `npdx:CurrentOperator` (a₁) | `npdx:orgNumber ← orgNumber` (a₂) | Example 1–3: map `m` di paper |
| `OverviewWellbore` | `SELECT * FROM reporting.wellbore_npdid_overview` | `wellbore/{wlbNpdidWellbore}`, `npdx:OverviewWellbore` | (tidak ada) | P-001, X ≠ ∅: map berkelas yang membaca seluruh tabel |
| `OverviewWellboreType` | sama | template sama, **tanpa kelas** | `npdx:overviewWellType ← wlbWellType` | P-001, domain aman lewat aturan R2 (ADR-0033) |
| `PointFacility` | `SELECT wlbNpdidWellbore, wlbDrillingFacility, wlbProductionFacility FROM reporting.wlbPoint` | `wellbore/{…}`, tanpa kelas | `npdx:drillingFacility ← wlbDrillingFacility` | P-002 rw_c (`wlbProductionFacility` hanya diproyeksikan) dan P-002 *keep* |
| `ShallowDrillingFacility` | `SELECT wlbNpdidWellbore, wlbDrillingFacility FROM reporting.wellbore_shallow_all` | `wellbore/{…}`, tanpa kelas | `npdx:drillingFacility ← wlbDrillingFacility` | Membuat `npdx:drillingFacility` tetap terisi saat kolom di `wlbPoint` di-drop |
| `PipelineOperator` | `SELECT * FROM services.v_pipeline_operator` | `pipeline/{pipNpdidPipe}` | `npdx:currentOperatorName` | Membuat view inline benar-benar dipakai mapping |
| `FieldDescriptionText` | `SELECT * FROM services.v_field_text_cache` | `field/{fldNpdidField}` | `npdx:descriptionText` | Membuat view materialized dipakai mapping |
| `MoveableFacility` | `SELECT * FROM registry.facility_moveable` | `facility/{fclNpdidFacility}`, `npdx:MoveableFacility` | `npdx:facilityKind ← fclKind` | P-001 dengan beberapa kelas |
| `ForeignFlaggedFacility` | `SELECT * FROM registry.facility_moveable WHERE fclNationName <> 'NORWAY'` | template sama, `npdx:ForeignFlaggedFacility` | `npdx:flagNation ← fclNationName` | Kelas kedua atas tabel yang sama |
| `FacilityRecord` | `SELECT * FROM registry.facility_moveable` | `…/npd-validation/record/facility/{fclNpdidFacility}`, **tanpa kelas** | `npdx:responsibleCompany ← fclCurrentRespCompanyName` | Map tanpa kelas dengan IRI lain: domain **tidak** aman (ADR-0033, kasus r14 di running example) |

Ada dua hal yang perlu diperhatikan:
- **`OverviewWellboreType` dan `FacilityRecord` sama-sama tidak berkelas, tetapi akibatnya
  berbeda.** `OverviewWellboreType` memakai template yang sama dengan map berkelas yang membaca
  seluruh tabel, jadi subjeknya sudah bertipe `npdx:OverviewWellbore`. `FacilityRecord`
  membangun IRI lain, jadi subjeknya tidak bertipe apa pun. Inilah dua sisi aturan "safe" di
  P-001.
- **`ForeignFlaggedFacility` sengaja memakai WHERE.** Map ini tetap termasuk X karena memakai
  `SELECT *`, tetapi tidak membaca seluruh baris. Map seperti ini tidak dipakai R2 untuk
  memberi tipe pada map lain.

> [GAMBAR] Diagram tabel `facility_moveable` dengan tiga triples map di atasnya. Tandai X_C untuk
> kelas usulan `npdx:ForeignFlaggedFacility`: `ForeignFlaggedFacility` dan `FacilityRecord` masuk,
> `MoveableFacility` keluar.

## 4. Perubahan pada ontologi 𝒯

| Elemen | Deklarasi | Alasan |
|---|---|---|
| Kelas `npdx:CurrentOperator`, `npdx:OverviewWellbore`, `npdx:MoveableFacility`, `npdx:ForeignFlaggedFacility` | `owl:Class` dengan `rdfs:label` dan `rdfs:comment` | Kelas subjek triples map baru. Tanpa `rdfs:subClassOf` ke kelas NPD, supaya jawaban query NPD tidak berubah. |
| Delapan property `npdx:orgNumber`, `overviewWellType`, `drillingFacility`, `currentOperatorName`, `descriptionText`, `facilityKind`, `flagNation`, `responsibleCompany` | `owl:DatatypeProperty`, `rdfs:range xsd:string`, **tanpa domain** | Tanpa domain, tidak ada keanggotaan kelas yang disimpulkan dari property ini. |
| `npdv:wlbEntryYear` | domain `npdx:OverviewWellbore`, range `xsd:integer`, deprecated lewat blok ASCAM | Skenario reuse yang aman: ADD `wellbore_npdid_overview.wlbEntryYear INTEGER` |
| `npdv:fclStatus` | domain `npdx:MoveableFacility`, range `xsd:string`, deprecated lewat blok ASCAM | Skenario reuse yang **tidak** aman: ADD `facility_moveable.fclStatus` harus menghasilkan IRI baru |

## 5. Peta paper → skenario

Hasil yang diharapkan di kolom terakhir sudah diperiksa oleh `test_case_study_npd_v2.py` atas
artefak di repo. Skenario lama A001–A009 masuk ke daftar ini dengan kode baru.

| Kode | Perubahan sumber | Bagian paper | Hasil yang diharapkan |
|---|---|---|---|
| v01 | `DROP company.cmpOrgNumberBrReg` | Example 2, P-002 (W, ℳ_c, orphaned) | `auto`: drop ĉ; ALTER VIEW `v_current_operator` (WHERE tetap); hapus POM `npdx:orgNumber` dari `CurrentOperator`; deprecate `npdx:orgNumber` |
| v02 | `RENAME company.cmpOrgNumberBrReg → cmpOrgNumber` | Example 3 (a), P-003 | `auto`: satu `set_name_in_source` |
| v03 | `DROP company.cmpLicenceOperCurrent` | Plan paragraf 1; Tabel 1 (SQL predicate, weakest link predicate) | `notify`: WHERE di `v_current_operator` dan di Mapping:00414/00415 |
| v04 | `DROP company.cmpNpdidCompany` | Plan paragraf 1; Tabel 1 (primary key, IRI template) | `notify` |
| v05 | `RENAME company.cmpGroup → cmpGroupName` | Tabel 1 (rename: c′ sudah dibaca) | `notify` |
| v06 | `DROP wellbore_npdid_overview.wlbWell` | Tabel 1 (IRI template di luar key) | `notify`, tanpa alasan *primary key* |
| v07 | `DROP wellbore_core.wlbCoreIntervalBottom` (A009) | Tabel 1 (weakest link expression) | `notify` |
| v08 | `DROP wlbPoint.wlbField` | Tabel 1 (view kehilangan semua kolom) | `notify` |
| v09 | `DROP pipLine.pipNameCurrentOperator` | Tabel 1 (kolom inline) | `notify` |
| v10 | `DROP field_description.fldDescriptionText` | Tabel 1 (materialized) | `notify` |
| v11 | `ADD field_activity_status_hst.fldStatusNote` | Plan paragraf 1; Tabel 1 (add, UNION) | `notify` |
| v12 | `DROP wlbPoint.wlbProductionFacility` | P-002, rw_c | `auto`: drop ĉ dan tulis ulang logical table `PointFacility` saja |
| v13 | `DROP wlbPoint.wlbDrillingFacility` | P-002, "a property that an assertion of ℳ′ still populates is not [deprecated]" | `auto`: hapus POM di `PointFacility`, `keep_property npdx:drillingFacility` |
| v14 | `DROP wellbore_mud.wlbMudViscosityAtMD` (A007) | P-002, W ≠ ∅ | `auto`: ALTER VIEW `v_wellbore_mud`, deprecate `npdv:mudViscosity` |
| v15 | `DROP field_investment_yearly.prfInvestmentsMillNOK` (A008) | P-002 lewat alias di view yang dibaca `SELECT *` | `auto`: ALTER VIEW, tanpa rewrite logical table |
| v16 | `RENAME wellbore_exploration_all.wlbDrillingOperator` (A003) | P-003 di PostgreSQL | `auto` |
| v17 | `ADD wellbore_npdid_overview.wlbEntryYear INTEGER` | P-001: X ≠ ∅, domain aman (R2), reuse, view `*` meneruskan kolom | `hitl`: POM di dua map; domain `npdx:OverviewWellbore`; reuse `npdv:wlbEntryYear` dan deprecation dicabut |
| v18 | `ADD facility_moveable.fclStatus VARCHAR(20)` | P-001: beberapa kelas, X_C, domain tidak aman, "Otherwise ASCAM proposes another name" | `hitl`: POM di `ForeignFlaggedFacility` dan `FacilityRecord`; tanpa domain; IRI `npdv:fclStatus_ForeignFlaggedFacility` |
| v19 | `ADD wellbore_mud.wlbMudPh` (A001) | P-001: X = ∅, map baru | `hitl`: `add_triples_map`, domain aman |
| v20 | `ADD company.cmpCountryCode` (A004) | P-001: X = ∅ di MySQL | `hitl`: `add_triples_map`, domain `npdv:Company` |
| v21 | `ADD field_description.fldNote` | P-001: X_C = ∅ | `hitl`: hanya `add_column` |

Ada tiga kondisi di baris "all" Tabel 1 yang **tidak** dibuat di `npd_v2`:

- **Model multi-source** butuh sumber kedua dengan skema yang sama, jadi harus mengubah sumber.
- **Trigger atau procedure atas τ̂.** Knowledge membaca kondisi ini dari `SYSADMIN.Triggers`, yaitu
  trigger yang didefinisikan pada τ̂. Menambahkan trigger pada foreign table NPD berisiko membuat
  VDB gagal dimuat, dan risiko itu belum bisa saya periksa tanpa Teiid.
- **Logical table yang tidak bisa di-parse** butuh SQL yang ditolak parser Knowledge tetapi
  diterima Ontop dan Teiid. Saya belum menemukan contoh yang pasti.

Ketiganya diuji oleh tes unit Knowledge (`test_impact.py`), bukan oleh skenario NPD. Ini perlu
disebut sebagai batasan di Bab IV.

## 6. Temuan selama membangun lapisan validasi

### 6.1 Baris *add* Tabel 1 lebih longgar daripada kode

Paper menulis "Such a view uses UNION, EXCEPT, or INTERSECT". Tetapi `views.check_view_adds`
hanya memberi notifikasi bila **ada cabang yang tidak** memilih `*` dari τ̂. Kalau semua cabang
memilih `*` dari τ̂, semua cabang mendapat kolom baru dan jumlah kolomnya tetap sama. Desain awal
`v_field_status` (dua cabang `SELECT *`) memang tidak dinotifikasi. Perilaku kode lebih tepat.
Usulan perbaikan teks Tabel 1:

```latex
Such a view uses \texttt{UNION}, \texttt{EXCEPT}, or \texttt{INTERSECT} with a branch that does
not select \texttt{*} over \(\hat{\tau}\).
```

### 6.2 Dua cacat ASCAM yang ditemukan dan diperbaiki (ADR-0034)

1. **POM baru dari P-001 menulis kolom tanpa delimiter.** Pada kolom mixed-case seperti
   `wlbEntryYear`, nama tanpa delimiter berarti `wlbentryyear` di PostgreSQL. Ontop lalu menolak
   mapping hasil v17 dan v18 dengan pesan "The placeholder(s) wlbEntryYear in the target do(es)
   not occur in source query". Cacat ini tidak muncul sebelumnya karena mapping NPD tidak punya
   map `SELECT *`, sedangkan kolom running example semuanya huruf kecil. Executor sekarang menulis
   `rr:column "\"wlbEntryYear\""`, sama seperti yang sudah dilakukan untuk triples map baru
   (ADR-0028).
2. **Kualifikasi IRI untuk kelas di namespace `#` salah.** Policy `qualify_with_class` mengambil
   nama lokal kelas setelah `/` terakhir, sehingga menghasilkan
   `npdv:fclStatus_npd-validation#ForeignFlaggedFacility`. Sekarang nama lokal diambil setelah `#`
   atau `/`, sehingga hasilnya `npdv:fclStatus_ForeignFlaggedFacility`.

## 7. Usulan contoh Preliminaries dengan NPD

Contoh di paper diganti dengan potongan NPD di atas: tabel `company`, view `v_current_operator`,
dan map `CurrentOperator`. Sebagai F diambil potongan itu saja. Pada NPD lengkap, 𝒟(c,𝓕) untuk
`cmpOrgNumberBrReg` sama persis (v01). Untuk `cmpLicenceOperCurrent`, 𝒟 juga memuat map NPD
00414 dan 00415, tetapi keputusannya tetap notifikasi.

```latex
\begin{example}\label{ex:lineage}
Source \(S_2\) (MySQL) of the NPD federation holds \texttt{company}
(\texttt{cmpNpdidCompany}, \texttt{cmpLicenceOperCurrent},
\texttt{cmpOrgNumberBrReg}, \ldots), exposed as \texttt{registry.company}.
\texttt{cmpLicenceOperCurrent} is \texttt{'Y'} for a company that currently
operates a production licence. The view
\begin{lstlisting}[language=SQL]
CREATE VIEW v_current_operator AS
SELECT cmpNpdidCompany, cmpOrgNumberBrReg AS orgNumber
FROM registry.company WHERE cmpLicenceOperCurrent = 'Y'
\end{lstlisting}
feeds a triples map \(m\) with \(\varphi_m(x) = \) \texttt{SELECT * FROM v\_current\_operator},
\(x = (\texttt{cmpNpdidCompany}, \texttt{orgNumber})\),
\(t_m = \texttt{npd:company/\char`\{cmpNpdidCompany\char`\}}\),
\(\mathcal{C}_m = \{\texttt{npdx:CurrentOperator}\}\), and
\(\mathit{POM}_m = \{(\texttt{npdx:orgNumber}, \texttt{orgNumber})\}\). Thus
\(\mathcal{M} = \{a_1, a_2\}\) with
\begin{align*}
  a_1 &: \varphi_m(x) \rightsquigarrow (t_m(\texttt{cmpNpdidCompany}), \texttt{rdf:type}, \texttt{npdx:CurrentOperator}), \\
  a_2 &: \varphi_m(x) \rightsquigarrow (t_m(\texttt{cmpNpdidCompany}), \texttt{npdx:orgNumber}, \texttt{orgNumber}),
\end{align*}
and \(\mathcal{T}\) declares \texttt{npdx:CurrentOperator} and \texttt{npdx:orgNumber}.
\end{example}

\begin{example}\label{ex:dependents}
Let \(\sigma=\mathit{drop}(\texttt{company},c)\) with \(c=\texttt{cmpOrgNumberBrReg}\). Then
\(\mathcal{D}(c,\mathcal{F})\) contains the foreign column \(\hat{c}\), the view column
\texttt{orgNumber}, and \(a_2\), so \(\mathcal{F}\) is inconsistent and \texttt{npdx:orgNumber} is
orphaned. Through \texttt{WHERE}, all view columns and both assertions also depend on
\texttt{cmpLicenceOperCurrent}.
\end{example}

\begin{example}\label{ex:repairs}
Let \(\sigma=\mathit{rename}(\texttt{company},c,\texttt{cmpOrgNumber})\), with \(c\) and
\(\mathcal{D}(c,\mathcal{F})\) as in Example~\ref{ex:dependents}. Admissible repairs include
(a)~setting the \texttt{NAMEINSOURCE} option of \(\hat{c}\) to \texttt{cmpOrgNumber}; (b)~renaming
\(\hat{c}\) and rewriting the view with \texttt{cmpOrgNumber AS orgNumber}; and (c)~removing
\(\hat{c}\), the view column \texttt{orgNumber}, and \(a_2\), and deprecating \texttt{npdx:orgNumber},
which on \(\mathcal{M}\) only deletes assertions, like a deletion-based repair
\cite{lembo2017mapping}. The elements changed by (a) are a proper subset of those changed by (b)
and (c), so of the three only (a) is minimal. Repairs (a) and (b) keep the organisation numbers
of the current operators, while (c) loses them. ASCAM constructs~(a), leaving the view,
\(\mathcal{M}\), and \(\mathcal{T}\) unchanged.
\end{example}
```

Kalimat pembuka Plan ikut menyesuaikan:

```latex
Dropping \texttt{cmpOrgNumberBrReg} in Example~\ref{ex:lineage} thus runs automatically, while
dropping \texttt{cmpNpdidCompany} (IRI template) or \texttt{cmpLicenceOperCurrent} (in
\texttt{WHERE}) is notified.
```

Ada empat hal yang perlu Anda putuskan di paper:

- **Prefiks `npd:` dan `npdx:`.** Prefiks `npd:` untuk `http://sws.ifi.uio.no/data/npd-v2/` dan
  `npdx:` perlu disebut sekali, misalnya di catatan kaki.
- **Label sumber S₂.** Contoh ini memakai S₂ (MySQL) karena `company` ada di `registry`. Kalau
  ingin S₁, saya bisa mencari padanan di `reporting`.
- **Kalimat pembuka contoh.** Dump NPD yang dipakai tidak saya buka di lingkungan ini, jadi
  contoh tidak menyebut baris data tertentu, berbeda dengan Ana, Budi, dan Cici. Kalau ingin
  menyebut jumlah perusahaan dengan `'Y'`, hitung dulu di stack dengan
  `SELECT COUNT(*) FROM company WHERE cmpLicenceOperCurrent = 'Y'`.
- **Figure lineage.** Gambar `fig_lineage` perlu digambar ulang dengan elemen di atas.

> [GAMBAR] Figure lineage baru dengan empat lapis:
> - sumber `company` (`cmpNpdidCompany`, `cmpLicenceOperCurrent`, `cmpOrgNumberBrReg`);
> - foreign table `registry.company`;
> - view `v_current_operator` (`cmpNpdidCompany`, `orgNumber`);
> - assertion a₁ dan a₂.
>
> Panah putus-putus dari kedua kolom view menuju `cmpLicenceOperCurrent` (lewat WHERE). Elemen
> yang disorot: `cmpOrgNumberBrReg`, ĉ, `orgNumber`, dan a₂.

## 8. Cara membangun ulang dan memeriksa

```bash
cd setup/npd && python3 tools/validation.py          # idempoten: lapisan lama diganti
cd ../ascam/knowledge/service && python3 -m pytest tests/test_case_study_npd_v2.py
```

Yang sudah diperiksa tanpa Docker:
- **Tes Knowledge.** Ke-21 tes `test_case_study_npd_v2.py` lulus, begitu juga seluruh 214 tes
  Knowledge.
- **`ontop validate` (Ontop 4.1.1)** pada salinan skema NPD yang kosong di PostgreSQL:
  - menerima mapping dan ontologi lengkap dengan lapisan validasi;
  - menerima hasil setiap rencana yang dieksekusi (v01, v12–v15, v17–v20) setelah diterapkan
    dengan editor Executor.

### 8.1 Pemeriksaan Teiid tanpa server Teiid

Teiid tidak bisa dijalankan di lingkungan pengembangan saya. Kebijakan jaringan sesi menolak
Maven Central (`repo1.maven.org`: *connect_rejected*) dan registry Docker, padahal Teiid 16 hanya
tersedia dari sana. Karena itu penerimaan VDB oleh Teiid saya periksa terhadap dokumentasi Teiid
(berkas `teiid-documents.pdf` di project) dan secara struktural:

| Elemen | Dasar dokumentasi | Pemeriksaan struktural |
|---|---|---|
| View dengan kolom inline `v_pipeline_operator ("pipNpdidPipe" integer, "pipNameCurrentOperator" string)` | BNF `CREATE VIEW {table-name} [( <view-element> … )] [OPTIONS (…)] AS …`, dengan `<table-element> ::= {column-name} [<data-type> …]` (*Schema object DDL*, hlm. 356). Catatan *Important* di hlm. 357: kolom inline "can be only altered to modify their properties. You cannot ADD or DROP new columns", yang menjadi dasar kondisi notifikasi *inline* | Dua kolom inline untuk proyeksi dua kolom. Tipenya sama dengan foreign column (`integer`, `string(100)`). |
| View materialized `OPTIONS (MATERIALIZED 'TRUE')` | Opsi `MATERIALIZED 'TRUE'\|'FALSE'` di tabel *VIEW OPTIONS* (hlm. 357). Tanpa `MATERIALIZED_TABLE`, Teiid memakai materialisasi internal (*Materialized Views*, hlm. 117). Cache dimuat saat query pertama kalau `ALLOW_MATVIEW_MANAGEMENT` tidak diset (*Internal Materialization*, hlm. 129). | Body view valid dan kolomnya ada di foreign table. Primary key tidak wajib. Menurut hlm. 117, key hanya "important" untuk kinerja materialisasi. |
| Foreign column `company."cmpGroupName"` dengan `NAMEINSOURCE` ke kolom yang tidak ada | Foreign table dapat didefinisikan eksplisit dengan DDL alih-alih diimpor dari sumber, dan `NAMEINSOURCE` adalah nama kolom di sumber (*Schema object DDL*, hlm. 352–354). VDB NPD memakai DDL eksplisit tanpa import, jadi Teiid tidak membandingkan kolom dengan sumber saat memuat VDB. | Kolom ini tidak dibaca view atau mapping mana pun, sehingga tidak pernah dikirim ke MySQL. |
| `v_field_status` (UNION ALL) | Teiid menurunkan kolom view tanpa daftar kolom dari query-nya (hlm. 357, ADR-0032) | Kedua cabang punya 7 kolom dengan urutan dan tipe yang sama dengan foreign table. |
| Semua view baru | — | XML VDB well-formed. Setiap body bisa di-parse (sqlglot), dan setiap kolom yang disebut ada di foreign table-nya. Keenam view juga berhasil dibuat di PostgreSQL pada salinan skema yang dipakai `ontop validate`. |

Ada satu hal yang **tidak** bisa dijamin oleh pemeriksaan ini: dokumentasi tidak menyatakan
secara eksplisit bahwa Teiid tidak pernah memeriksa `NAMEINSOURCE` saat VDB dimuat. Pernyataan
itu kesimpulan dari cara kerja metadata DDL. Kalau di stack ternyata VDB gagal dimuat karena
`cmpGroupName`, hapus baris itu dari `LEGACY_COLUMN` di `validation.py`. Konsekuensinya, skenario
v05 tidak bisa dijalankan.

Yang tetap hanya bisa diperiksa di stack:
- penerimaan VDB oleh Teiid secara nyata;
- P-003 (v02, v16);
- Monitor dan rantai event;
- jawaban SPARQL atas data NPD yang sebenarnya.

Kalau domain `repo1.maven.org` diizinkan di pengaturan Capabilities claude.ai, Teiid Embedded
bisa saya jalankan di lingkungan ini untuk memeriksa penerimaan VDB secara langsung.
