### 1. Integrasi Data Kemensos & Dukcapil
Query ini mengambil nama dari sistem Kemensos dan mencocokkannya dengan data pekerjaan serta penghasilan dari sistem Dukcapil. Ini adalah inti dari integrasi data Anda.

```sparql
PREFIX bansos: <http://bansos.go.id/ontology/>
PREFIX : <http://bansos.go.id/ontology#>

SELECT ?nik ?namaLengkap ?pekerjaan ?penghasilan
WHERE {
  ?penerima a bansos:PenerimaBansos ;
            bansos:nik ?nik ;
            bansos:namaLengkap ?namaLengkap ;
            bansos:memilikDataKependudukan ?penduduk . # Relasi antar DB
            
  ?penduduk bansos:pekerjaan ?pekerjaan ;
            bansos:penghasilan ?penghasilan .
}
```

---

### 2. Mencari Penerima di Wilayah Tertentu
Misalkan Anda ingin melihat siapa saja penerima bansos yang berdomisili di Provinsi **"Jawa Barat"**. Query ini akan menelusuri dari tabel Kemensos ke tabel Wilayah di MySQL.

```sparql
PREFIX bansos: <http://bansos.go.id/ontology/>

SELECT ?nama ?nik ?desa ?kecamatan
WHERE {
  ?penerima a bansos:PenerimaBansos ;
            bansos:namaLengkap ?nama ;
            bansos:nik ?nik ;
            bansos:memilikDataKependudukan ?penduduk .
            
  ?penduduk bansos:tergabungDalamKeluarga ?kk .
  ?kk bansos:berdomisiliDi ?wilayah .
  
  ?wilayah bansos:provinsi "Jawa Barat" ;
           bansos:desa ?desa ;
           bansos:kecamatan ?kecamatan .
}
```

---

### 3. Cek Kelayakan (Eligibility) & Alasan
Query ini menampilkan daftar orang yang diperiksa kelayakannya beserta alasan kenapa mereka dianggap `ELIGIBLE` atau `TIDAK ELIGIBLE`.

```sparql
PREFIX bansos: <http://bansos.go.id/ontology/>

SELECT ?nama ?program ?status ?alasan
WHERE {
  ?check a bansos:EligibilityCheck ;
         bansos:statusEligible ?status ;
         bansos:alasan ?alasan ;
         bansos:validasiPenerima ?penerima ;
         bansos:divalidasiDalam ?prog .
         
  ?penerima bansos:namaLengkap ?nama .
  ?prog bansos:namaProgram ?program .
  
  FILTER(?status = "TIDAK ELIGIBLE") # Opsional: hanya lihat yang ditolak
}
```

---

### 4. Audit Perbedaan Data (Data Integrity)
Salah satu kegunaan utama ontologi adalah audit. Query ini mencari penerima yang namanya di database Kemensos **berbeda** dengan nama di database Dukcapil. Ini sangat penting untuk pembersihan data (data cleansing).

```sparql
PREFIX bansos: <http://bansos.go.id/ontology/>

SELECT ?nik ?namaLengkap ?namaDukcapil
WHERE {
  ?penerima a bansos:PenerimaBansos ;
            bansos:nik ?nik ;
            bansos:namaLengkap ?namaLengkap ;
            bansos:memilikDataKependudukan ?penduduk .
            
  ?penduduk bansos:namaPenduduk ?namaDukcapil .
  
  # Mencari ketidaksesuaian nama (Case Insensitive)
  FILTER(LCASE(?namaLengkap) != LCASE(?namaDukcapil))
}
```

---

### 5. Rekap Total Penyaluran (Agregasi)
Menghitung total dana yang harus disalurkan per program berdasarkan transaksi yang ada.

```sparql
PREFIX bansos: <http://bansos.go.id/ontology/>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?namaProgram (SUM(?nominal) AS ?totalDana)
WHERE {
  ?transaksi a bansos:TransaksiBansos ;
             bansos:terdaftarPadaProgram ?prog ;
             bansos:statusTransaksi "TERSALURKAN" .
             
  ?prog bansos:namaProgram ?namaProgram ;
        bansos:nominal ?nominal .
}
GROUP BY ?namaProgram
```

---

### Tips Menjalankan Query:
1.  **Gunakan Ontop**: Jika Anda menggunakan **Ontop Protege**, pastikan tab "Ontop SPARQL" sudah aktif dan file `.obda` Anda sudah terhubung ke database virtual (Teiid/Government VDB).
2.  **Case Sensitive**: Perhatikan bahwa nilai string di dalam `FILTER` (seperti "Jawa Barat") bersifat *case-sensitive* kecuali Anda menggunakan fungsi `LCASE()`.
3.  **Typo Ontology**: Pada query di atas, saya menggunakan `bansos:memilikDataKependudukan` (kurang huruf 'i' di akhir) sesuai dengan nama property yang Anda tulis di file `.ttl` tadi. Jika nanti Anda memperbaikinya di ontologi, pastikan di SPARQL juga diubah ya!

Apakah ada skenario pengecekan data spesifik lainnya yang ingin Anda buatkan query-nya?