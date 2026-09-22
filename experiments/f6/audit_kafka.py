"""
Audit pesan Kafka untuk kriteria C_safe (proposal §3.11.1, pers. 3.13–3.15).

Dijalankan DI DALAM kontainer yang tersambung ke jaringan ascam-networks (lihat evaluate.py):
  python audit_kafka.py offset          -> offset akhir setiap partisi, sebagai titik awal
  python audit_kafka.py audit awal.json -> klasifikasi seluruh pesan sejak titik awal

Klasifikasi setiap pesan:
  log              baris ddl_event_log (N_log, kontrol positif)
  produksi         baris tabel sumber lain, mis. penerima_manfaat (N_prod, harus 0)
  perubahan_skema  peristiwa skema Debezium (teks DDL, tanpa baris data)
  tombstone        pesan kosong penanda hapus
  lain             bentuk yang tidak dikenali (dilaporkan apa adanya)

Prinsip: berkas hasil TIDAK memuat nilai baris produksi. Untuk pesan produksi hanya dicatat
topik, offset, tabel, operasi, dan NAMA kolom; untuk pesan log hanya nama kolom log.
"""
import json
import sys

from kafka import KafkaConsumer, TopicPartition

BOOTSTRAP = 'kafka:9092'
PREFIKS = ('kemensos', 'dukcapil', 'schema-monitor-')


def topik_relevan(consumer) -> list[str]:
    """Topik milik konektor sumber; topik internal Kafka dan Kafka Connect diabaikan."""
    return sorted(t for t in consumer.topics()
                  if not t.startswith('_') and 'connect' not in t and t.startswith(PREFIKS))


def offset_akhir() -> dict:
    consumer = KafkaConsumer(bootstrap_servers=BOOTSTRAP, enable_auto_commit=False)
    hasil = {}
    for topik in topik_relevan(consumer):
        partisi = [TopicPartition(topik, p) for p in consumer.partitions_for_topic(topik) or []]
        hasil[topik] = {str(tp.partition): off for tp, off in consumer.end_offsets(partisi).items()}
    consumer.close()
    return hasil


def klasifikasi(nilai) -> tuple[str, dict]:
    if nilai is None:
        return 'tombstone', {}
    try:
        isi = json.loads(nilai)
    except ValueError:
        return 'lain', {'alasan': 'bukan JSON'}
    payload = isi.get('payload') if isinstance(isi, dict) and isinstance(isi.get('payload'), dict) else isi
    if not isinstance(payload, dict):
        return 'lain', {'alasan': f'tipe {type(payload).__name__}'}
    sumber = payload.get('source') or {}
    tabel = sumber.get('table')
    if 'ddl' in payload or 'tableChanges' in payload or 'historyRecord' in payload:
        return 'perubahan_skema', {'db': sumber.get('db'), 'tabel': tabel}
    baris = payload.get('after') or payload.get('before')
    if tabel == 'ddl_event_log':
        return 'log', {'kolom': sorted(baris) if isinstance(baris, dict) else []}
    if payload.get('op') in ('c', 'u', 'd', 'r') and isinstance(baris, dict):
        return 'produksi', {'db': sumber.get('db'), 'skema': sumber.get('schema'), 'tabel': tabel,
                            'op': payload.get('op'), 'kolom': sorted(baris)}
    return 'lain', {'kunci': sorted(payload)[:10]}


def audit(awal: dict) -> dict:
    consumer = KafkaConsumer(bootstrap_servers=BOOTSTRAP, enable_auto_commit=False,
                             consumer_timeout_ms=5000, value_deserializer=lambda v: v)
    hitung = {'log': 0, 'produksi': 0, 'perubahan_skema': 0, 'tombstone': 0, 'lain': 0}
    per_topik, contoh_produksi, kolom_log = {}, [], set()
    for topik in topik_relevan(consumer):
        partisi = [TopicPartition(topik, p) for p in consumer.partitions_for_topic(topik) or []]
        akhir = consumer.end_offsets(partisi)
        for tp in partisi:
            mulai = int((awal.get(topik) or {}).get(str(tp.partition), 0))
            if akhir[tp] <= mulai:
                continue
            consumer.assign([tp])
            consumer.seek(tp, mulai)
            while True:
                kumpulan = consumer.poll(timeout_ms=3000)
                pesan_list = [m for daftar in kumpulan.values() for m in daftar]
                if not pesan_list:
                    break
                selesai = False
                for pesan in pesan_list:
                    if pesan.offset >= akhir[tp]:
                        selesai = True
                        break
                    jenis, rincian = klasifikasi(pesan.value)
                    hitung[jenis] += 1
                    per_topik.setdefault(topik, {}).setdefault(jenis, 0)
                    per_topik[topik][jenis] += 1
                    if jenis == 'log':
                        kolom_log.update(rincian.get('kolom', []))
                    if jenis == 'produksi' and len(contoh_produksi) < 20:
                        contoh_produksi.append({'topik': topik, 'offset': pesan.offset, **rincian})
                if selesai or pesan_list[-1].offset >= akhir[tp] - 1:
                    break
    consumer.close()
    return {'N_log': hitung['log'], 'N_prod': hitung['produksi'], 'hitung': hitung,
            'per_topik': per_topik, 'kolom_pesan_log': sorted(kolom_log),
            'contoh_pesan_produksi': contoh_produksi,
            'C_safe': hitung['produksi'] == 0 and hitung['log'] > 0}


if __name__ == '__main__':
    if sys.argv[1] == 'offset':
        print(json.dumps(offset_akhir()))
    elif sys.argv[1] == 'audit':
        with open(sys.argv[2]) as berkas:
            print(json.dumps(audit(json.load(berkas))))
