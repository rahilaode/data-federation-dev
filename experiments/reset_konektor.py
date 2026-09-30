"""
Memulihkan konektor Debezium yang kehilangan posisi baca, TANPA memutar ulang ddl_event_log.

Gejala yang ditangani (temuan 2026-09-30):
  - konektor MySQL FAILED dengan galat 1236 "Could not find first log file name in binary log
    index file": posisi binlog yang diingat sudah tidak ada di server;
  - konektor dengan snapshot.mode=initial yang kehilangan offset membaca ulang seluruh
    ddl_event_log, sehingga ASCAM mengadaptasi OBDF terhadap DDL lama.

Langkah: hentikan kedua konektor, hapus offset-nya (Kafka Connect REST, KIP-875), hapus
konektornya, hapus topik schema history MySQL, lalu hapus replication slot dan publikasi
PostgreSQL agar dibuat ulang dari posisi terkini. Pendaftaran ulang dilakukan jalankan.sh
dengan snapshot.mode=no_data, sehingga hanya DDL setelah pendaftaran yang diterbitkan.

Data sumber dan isi ddl_event_log TIDAK diubah. Jalankan dari root repository:
  python3 experiments/reset_konektor.py
  ./jalankan.sh --uji-rantai
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONNECT = 'http://localhost:8083'
KONEKTOR = ['postgres-connector', 'mysql-connector']
TOPIK_HISTORY = 'schema-monitor-dukcapil'
SLOT, PUBLIKASI = 'kemensos_slot', 'dbz_publication'


def rest(metode: str, path: str) -> tuple[int, str]:
    req = urllib.request.Request(CONNECT + path, method=metode)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def status(nama: str) -> str | None:
    kode, isi = rest('GET', f'/connectors/{nama}/status')
    return json.loads(isi)['connector']['state'] if kode == 200 else None


def docker(*args: str) -> str:
    hasil = subprocess.run(['docker', *args], capture_output=True, text=True)
    return (hasil.stdout + hasil.stderr).strip()


def main() -> int:
    kode, _ = rest('GET', '/connectors')
    if kode != 200:
        sys.exit(f'Kafka Connect tidak menjawab di {CONNECT}')

    for nama in KONEKTOR:
        if status(nama) is None:
            print(f'{nama}: tidak terdaftar, dilewati')
            continue
        print(f'{nama}: menghentikan …', rest('PUT', f'/connectors/{nama}/stop')[0])
        for _ in range(30):
            if status(nama) == 'STOPPED':
                break
            time.sleep(1)
        kode, isi = rest('DELETE', f'/connectors/{nama}/offsets')
        if kode not in (200, 204):
            sys.exit(f'{nama}: offset tidak dapat dihapus ({kode}): {isi[:300]}\n'
                     'Hentikan di sini; jangan daftarkan ulang dengan offset lama.')
        print(f'{nama}: offset dihapus')
        print(f'{nama}: dihapus', rest('DELETE', f'/connectors/{nama}')[0])

    print('topik schema history:', docker('exec', 'ascam-sm-kafka', '/kafka/bin/kafka-topics.sh',
                                          '--bootstrap-server', 'localhost:9092', '--delete',
                                          '--topic', TOPIK_HISTORY) or 'dihapus')

    psql = ['exec', 'datasources-pgsql', 'psql', '-U', 'postgres', '-d', 'kemensos', '-tAc']
    for _ in range(15):                                   # slot baru bisa dihapus bila tidak aktif
        aktif = docker(*psql, f"SELECT active FROM pg_replication_slots WHERE slot_name='{SLOT}'")
        if aktif in ('', 'f'):
            break
        time.sleep(2)
    if aktif:
        print('replication slot:', docker(*psql, f"SELECT pg_drop_replication_slot('{SLOT}')"))
    print('publikasi:', docker(*psql, f'DROP PUBLICATION IF EXISTS {PUBLIKASI}'))

    print('\nSelesai. Lanjutkan dengan: ./jalankan.sh --uji-rantai')
    print('Periksa setelahnya: kedua konektor RUNNING, dan publikasi hanya memuat ddl_event_log:')
    print("  docker exec datasources-pgsql psql -U postgres -d kemensos -c "
          "\"SELECT * FROM pg_publication_tables\"")
    return 0


if __name__ == '__main__':
    sys.exit(main())
