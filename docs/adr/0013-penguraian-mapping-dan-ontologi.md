# ADR-0013: Penguraian ℳ dan 𝒯 menjadi struktur di Knowledge

- **Status:** Diterima
- **Tanggal:** 2026-09-18
- **Fase MAPE-K:** Knowledge (sync), Analyze (dasar lineage)
- **Terkait:** ADR-0005, ADR-0006, ADR-0012, rancangan Knowledge §4.4–4.5

## Konteks

Artefak ℳ dan 𝒯 sudah tersimpan utuh beserta seluruh triple-nya (ADR-0012), tetapi analisis
dampak memerlukan strukturnya: TriplesMap, logical table dan kolomnya, term map, serta entitas
dan aksioma ontologi.

## Keputusan

1. **ℳ diurai sebagai graf RDF** (rdflib) mengikuti R2RML: logical table (`rr:tableName` atau
   `rr:sqlQuery`), subject map, predicate-object map, referencing object map beserta
   `rr:joinCondition`, bentuk pintasan (`rr:subject`, `rr:predicate`, `rr:object`, `rr:class`,
   `rr:graph`), template, `rr:termType`, `rr:datatype`, dan `rr:language`.
2. **Identitas yang stabil.** IRI relatif diselesaikan terhadap base tetap `urn:ascam:mapping`
   agar identitas TriplesMap tidak bergantung pada lokasi berkas. Karena label blank node tidak
   stabil antar-parse, setiap predicate-object map diberi **tanda tangan struktural** dari
   predikat dan objeknya.
3. **Kolom logical table** ditentukan dua cara: untuk `rr:tableName`, seluruh kolom tabel Σ_S;
   untuk `rr:sqlQuery`, hasil analisis `sqlglot` — proyeksi pass-through, ekspresi (beserta kolom
   sumbernya), dan `SELECT *` yang diperluas dari Σ_S. Kolom yang dirujuk pada `WHERE`, `JOIN`,
   `GROUP BY`, `HAVING`, `ORDER BY`, dan subkueri dicatat terpisah sebagai rujukan SQL.
4. **𝒯 diurai** menjadi ontologi (IRI, versi, `owl:imports`) dan entitas (kelas, datatype
   property, object property, annotation property; punning diperbolehkan) beserta domain
   (termasuk ekspresi kelas kompleks), range, relasi antarproperty, dan anotasi (label, komentar,
   deprecation, catatan perubahan).
5. **Kompatibilitas OWL 2 QL** ditandai hanya pada range **datatype property**; range object
   property berupa kelas sehingga penandanya tidak berlaku.
6. **Kegagalan menjadi masalah konsistensi, bukan kegagalan sync**: `sql_unparsed`,
   `logical_source_unresolved`, `logical_column_unresolved`, `owl2ql_profile_violation`,
   `artifact_structure_failed`.

## Bukti

Parser diuji terhadap artefak OBDF yang sebenarnya: `mapping.ttl` menghasilkan 8 TriplesMap
beserta template, datatype, dan referencing object map-nya; `ontology_file.ttl` menghasilkan
11 kelas, 39 datatype property, dan 9 object property, dengan `xsd:boolean` dan `xsd:date`
ditandai di luar OWL 2 QL. Lima uji tambahan (total 65 pada Knowledge Service) memverifikasi
penyimpanan struktur: kolom logical table dari `rr:tableName` dan dari `SELECT *`, kolom
ekspresi beserta kolom sumbernya, rujukan `WHERE`, subject map beserta kelas, datatype objek,
join condition dan parent TriplesMap, template multikolom, entitas ontologi beserta deprecation,
relasi `rdfs:subPropertyOf`, anotasi berbahasa, penanda OWL 2 QL, serta pelaporan kolom mapping
yang tidak ada di Σ_S dan SQL yang tidak dapat diurai.

## Konsekuensi

- Struktur ini menjadi masukan langsung bagi lineage kolom (`spec.column_usage`) pada langkah
  berikutnya.
- Analisis SQL bergantung pada sqlglot; dialek Teiid tidak sepenuhnya sama, sehingga kueri yang
  tidak terurai ditandai dan diperlakukan konservatif.
- Fungsi SQL dinormalkan sqlglot (mis. `UCASE` menjadi `UPPER`) pada teks ekspresi yang disimpan;
  kolom sumber tetap terlacak dengan benar.
