# Contoh: Teiid multi-source model

Contoh mandiri untuk melihat cara kerja *multi-source model* Teiid. Contoh ini terpisah dari
OBDF utama di `setup/`: kontainer, jaringan, volume, dan port-nya berbeda, sehingga dapat
dijalankan bersamaan tanpa saling mengganggu.

## Isi

| Berkas | Isi |
| --- | --- |
| `docker-compose.yaml` | `example-pgsql` (PostgreSQL 15) dan `example-teiid` (Teiid 16.0.0) |
| `source/01-dinsos.sql` | tiga basis data berskema identik: `dinsos_sulsel`, `dinsos_jabar`, `dinsos_dki` |
| `teiid/Dockerfile` | image Teiid, sama dengan `setup/data-federation/Dockerfile` |
| `teiid/setup.cli` | driver JDBC dan satu datasource untuk setiap sumber |
| `teiid/deployments/nasional-vdb.xml` | VDB `nasional` dengan multi-source model `dinsos` |
| `mulai.sh` | memberi izin tulis folder `teiid/deployments`, lalu `docker-compose up -d --build` |
| `uji.sh` | kueri uji dan skenario *drop* pada satu sumber |

## Menjalankan

```bash
cd example
./mulai.sh                       # izin folder deployments + docker-compose up -d --build
./uji.sh                         # kueri dasar
./uji.sh drop                    # skenario: hanya Jawa Barat menghapus kolom email
docker-compose down -v           # hapus kontainer dan data contoh
```

| Port host | Layanan |
| --- | --- |
| 25432 | PostgreSQL sumber (`postgres`/`postgres`) |
| 31100 | JDBC Teiid |
| 35532 | ODBC Teiid (protokol PostgreSQL), VDB `nasional`, `user1`/`Password12345_` |
| 19190 | konsol manajemen WildFly (`admin`/`Password12345_`) |

## Yang diharapkan

1. `SYS.Columns` memuat `penerima_id, nik, nama_lengkap, email, provinsi`.
2. `SELECT … FROM dinsos.penerima_manfaat` mengembalikan 5 baris dari tiga sumber, dengan
   `provinsi` berisi `sulsel`, `jabar`, atau `dki`; `penerima_id` berulang antar sumber.
3. `WHERE provinsi = 'jabar'` hanya mengembalikan baris Jawa Barat.
4. Skenario `drop`: setelah `dinsos_jabar` menghapus `email`, kueri yang membaca `email` dari
   semua sumber gagal, sedangkan kueri tanpa `email` tetap berjalan. Satu foreign table tidak
   dapat sekaligus memiliki dan tidak memiliki kolom tersebut; karena itu ASCAM hanya
   memberi notifikasi untuk perubahan pada multi-source model (Table 1 paper).

Rujukan konfigurasi: `teiid-documents.pdf`, bagian *MultiSource Models*, hlm. 366–367.
