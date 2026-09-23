"""
F6 — Uji terarah monitor MySQL berbasis polling (anomali A003 run 12).

Monitor MySQL membandingkan cuplikan kolom (ddl_column_snapshot) dengan information_schema
setiap 10 detik. Hipotesis: bila sebuah kolom diganti nama lalu dikembalikan di antara dua
pemindaian, tidak ada selisih yang terlihat dan kedua perubahan hilang.

Uji ini memakai tabel terpisah `ascam_uji_monitor` yang didaftarkan sementara di
captured_tables_ref, sehingga tabel studi kasus tidak tersentuh dan ASCAM mengabaikan
event-nya (tabel tidak difederasikan). Semua perubahan dibersihkan di akhir.

Untuk setiap jeda W, kolom diganti nama (a -> b), ditunggu W detik, dikembalikan (b -> a),
lalu ditunggu cukup lama agar pemindaian berikutnya pasti terjadi. Dihitung berapa dari dua
perubahan yang tercatat di ddl_event_log, beserta latensi deteksinya.

Jalankan dari root repository:  python3 experiments/f6/uji_monitor_mysql.py [--ulangan 3]
"""
import argparse
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

TABEL = 'ascam_uji_monitor'
JEDA = [2, 5, 12, 25]
TENANG = 25                         # > 2 x interval polling


def mysql(sql: str) -> list[list[str]]:
    hasil = subprocess.run(
        ['docker', 'exec', '-i', 'datasources-mysql', 'sh', '-c',
         'mysql -N -B -uroot -p"$MYSQL_ROOT_PASSWORD" dukcapil 2>/dev/null'],
        input=sql, capture_output=True, text=True)
    if hasil.returncode != 0:
        raise RuntimeError(hasil.stderr or hasil.stdout)
    return [b.split('\t') for b in hasil.stdout.strip().splitlines() if b.strip()]


def id_log_terakhir() -> int:
    baris = mysql('SELECT COALESCE(MAX(id), 0) FROM ddl_event_log;')
    return int(baris[0][0])


def log_sejak(id_awal: int) -> list[dict]:
    baris = mysql(f"SELECT id, alter_type, column_name, captured_at FROM ddl_event_log "
                  f"WHERE id > {id_awal} AND table_name = '{TABEL}' ORDER BY id;")
    return [{'id': int(b[0]), 'op': b[1], 'kolom': b[2],
             'waktu': datetime.fromisoformat(b[3]).replace(tzinfo=timezone.utc)} for b in baris]


def siapkan() -> None:
    mysql(f"""
        DROP TABLE IF EXISTS {TABEL};
        CREATE TABLE {TABEL} (id INT PRIMARY KEY, kolom_a VARCHAR(20));
        DELETE FROM ddl_column_snapshot WHERE table_name = '{TABEL}';
        INSERT INTO ddl_column_snapshot (db_name, table_name, column_name, column_type, ordinal_pos)
            SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, ORDINAL_POSITION
            FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = 'dukcapil' AND TABLE_NAME = '{TABEL}';
        INSERT INTO captured_tables_ref (db_name, table_name, description)
            VALUES ('dukcapil', '{TABEL}', 'uji sementara monitor (F6)');
    """)
    time.sleep(TENANG)              # biarkan satu pemindaian berjalan tanpa perubahan


def bersihkan() -> None:
    mysql(f"""
        DELETE FROM captured_tables_ref WHERE table_name = '{TABEL}';
        DELETE FROM ddl_column_snapshot WHERE table_name = '{TABEL}';
        DROP TABLE IF EXISTS {TABEL};
    """)


def ganti(dari: str, ke: str) -> datetime:
    t = datetime.now(timezone.utc)
    mysql(f'ALTER TABLE {TABEL} RENAME COLUMN {dari} TO {ke};')
    return t


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--ulangan', type=int, default=3)
    args = parser.parse_args()
    interval = mysql("SELECT INTERVAL_VALUE, INTERVAL_FIELD FROM information_schema.EVENTS "
                     "WHERE EVENT_NAME = 'evt_detect_ddl_changes';")
    print(f'Interval pemindaian monitor MySQL: {" ".join(interval[0]) if interval else "?"}')
    print(f'Perkiraan durasi: {args.ulangan * sum(w + 2 * TENANG for w in JEDA) // 60 + 1} menit\n')

    hasil = {w: {'siklus': 0, 'tercatat': 0, 'keduanya_hilang': 0, 'latensi': []} for w in JEDA}
    siapkan()
    try:
        for ulang in range(1, args.ulangan + 1):
            for w in JEDA:
                awal = id_log_terakhir()
                t1 = ganti('kolom_a', 'kolom_b')
                time.sleep(w)
                t2 = ganti('kolom_b', 'kolom_a')
                time.sleep(TENANG)
                log = [e for e in log_sejak(awal) if e['op'] == 'RENAME COLUMN']
                maju = [e for e in log if e['kolom'].startswith('kolom_a')]
                balik = [e for e in log if e['kolom'].startswith('kolom_b')]
                r = hasil[w]
                r['siklus'] += 1
                r['tercatat'] += len(maju[:1]) + len(balik[:1])
                r['keduanya_hilang'] += int(not maju and not balik)
                if maju:
                    r['latensi'].append((maju[0]['waktu'] - t1).total_seconds())
                if balik:
                    r['latensi'].append((balik[0]['waktu'] - t2).total_seconds())
                print(f'  ulangan {ulang}, jeda {w:>2} s: maju={"ya" if maju else "HILANG"}, '
                      f'balik={"ya" if balik else "HILANG"}')
                time.sleep(TENANG)
    finally:
        bersihkan()

    print('\n| Jeda (s) | Siklus | Perubahan tercatat | Siklus dengan keduanya hilang | Latensi deteksi (s) |')
    print('|---:|---:|---:|---:|---:|')
    for w, r in hasil.items():
        lat = r['latensi']
        teks = (f'{statistics.median(lat):.1f} ({min(lat):.1f}–{max(lat):.1f})' if lat else '-')
        print(f"| {w} | {r['siklus']} | {r['tercatat']}/{2 * r['siklus']} | "
              f"{r['keduanya_hilang']} | {teks} |")
    return 0


if __name__ == '__main__':
    sys.exit(main())
