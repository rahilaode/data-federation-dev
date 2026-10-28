"""
Converts a result directory written by the Indonesian-language harness (branch eval/final,
experiments/f6/evaluate.py) into the English format read by analyze.py, extract.py, and plot.py.

The evaluation reported in the paper ran on eval/final, before the code base was translated.
The measurements are the same; only JSON keys, a few string values, query names, and file
names differ. This script renames them and copies everything else unchanged. The source
directory is never modified.

Text produced by the services themselves at run time (plan reasons, notification messages,
error messages, DDL output) is copied as it is, so it stays in Indonesian in converted files.

Usage (from the repository root):
  python3 experiments/evaluation/convert_results.py results/f6/20261028T230000
  python3 experiments/evaluation/convert_results.py <source> <target>
The default target is results/evaluation/<name of the source directory>.
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenarios import SCENARIOS  # noqa: E402

RECORD = {
    'mode': 'mode', 'skenario': 'scenario', 'kode_baseline': 'baseline_code', 'judul': 'title',
    'run': 'run', 'commit': 'commit', 'mulai': 'started', 'rencana_dinetralkan': 'superseded_plans',
    'pemulihan_terdeteksi': 'restore_detected', 'skenario_dipulihkan': 'restored_scenarios',
    'versi_dasar': 'base_version', 'hasil': 'outcome', 'jawaban_sebelum': 'answers_before',
    'jawaban_sesudah': 'answers_after', 'jeda_acak_s': 'random_delay_s', 'ddl': 'ddl',
    'isi_data': 'fill_data', 'penilaian': 'assessment', 'artefak_tidak_berubah': 'artifacts_unchanged',
    'selesai': 'finished', 'pola_diharapkan': 'expected_pattern',
    'keputusan_diharapkan': 'expected_decision', 't_start': 't_start', 'deteksi_ms': 'detection_ms',
    'event': 'event', 'keputusan': 'decision', 'pola': 'pattern', 'sesuai_harapan': 'as_expected',
    'alasan': 'reasons', 'alasan_sesuai': 'reasons_match', 'n_manual': 'n_manual',
    'keputusan_ms': 'decision_ms', 'disetujui_oleh': 'approved_by', 'adaptasi_ms': 'adaptation_ms',
    'eksekusi': 'execution', 'langkah': 'steps', 'status_langkah': 'step_status',
    't_end_langkah': 't_end_step', 'dt_adapt_ms': 'dt_adapt_ms',
    'dt_adapt_mesin_ms': 'dt_adapt_machine_ms', 'harapan': 'expectations',
    'notifikasi': 'notifications', 'terdampak': 'affected', 'eksekusi_baru': 'new_executions',
    'versi_sesudah': 'version_after', 'ketersediaan': 'availability',
}
MODE = {'perlakuan': 'treatment', 'baseline': 'baseline', 'keduanya': 'both'}
OUTCOME = {'persiapan tidak tuntas': 'setup incomplete', 'event tidak diterima': 'no event received',
           'eksekusi tidak selesai': 'execution not finished', 'dinotifikasi': 'notified',
           'dieksekusi_meski_dinotifikasi': 'executed_despite_notification', 'ditolak': 'rejected'}
ANSWER = {'status': 'status', 'ok': 'ok', 'galat': 'error', 'n': 'n', 'hasil': 'rows',
          'durasi_ms': 'duration_ms'}
QUERY_NAME = {
    'penerima': 'recipient', 'email': 'email', 'tautan_penduduk': 'resident_link',
    'jumlah_per_kelas': 'count_per_class', 'status_transaksi': 'transaction_status',
    'tipe_program': 'program_type', 'nama_program': 'program_name',
    'transaksi_program': 'transaction_program', 'tanggal_lahir': 'birth_date',
    'nama_pekerjaan': 'name_occupation', 'penerima_ke_penduduk': 'recipient_to_resident',
    'kode_pos': 'postal_code', 'wilayah': 'region', 'status_hidup': 'living_status',
    'keluarga_wilayah': 'family_region', 'tipe_nominal': 'type_amount',
    'nokk_penerima_aktif': 'active_recipient_kk', 'penerima_aktif': 'active_recipient',
    'petugas_riwayat': 'history_officer', 'kolom_riwayat': 'history_column', 'tahun_berakhir': 'end_year',
}
# 'nama_program' is the affected query of A006 and the neighbour query of A002/A009; the
# mapping is the same in every scenario, so one table is enough.
KIND = {'terdampak': 'affected', 'tetangga': 'neighbour', 'kontrol': 'control'}
ASSESSMENT = {'metrik': 'metric', 'preservation_ratio': 'preservation_ratio', 'per_kueri': 'per_query'}
PER_QUERY = {'jenis': 'kind', 'result_preserved': 'result_preserved',
             'execution_preserved': 'execution_preserved', 'baris_sebelum': 'rows_before',
             'baris_sesudah': 'rows_after', 'status_sesudah': 'status_after'}
EXPECTATION = {
    'kueri_terdampak_gagal': 'affected_query_fails', 'kueri_lama_valid': 'old_queries_valid',
    'kueri_baru_mengembalikan_hasil': 'new_query_returns_rows',
    'kueri_lama_tetap_berjalan': 'old_query_still_runs', 'answer_set_kosong': 'answer_set_empty',
    'answer_set_identik': 'answer_set_identical', 'tanpa_tindakan': 'no_actions',
    'notifikasi_menyebut_view': 'notification_names_view', 'tidak_dieksekusi': 'not_executed',
    'versi_tidak_berubah': 'version_unchanged',
}
AVAILABILITY = {'n_probe': 'n_probe', 'n_gagal': 'n_failed', 'ketersediaan_persen': 'availability_percent',
                'selang_gagal_terpanjang_ms': 'longest_outage_ms', 'jeda_probe_s': 'probe_interval_s'}
MESSAGE_KIND = {'log': 'log', 'produksi': 'production', 'perubahan_skema': 'schema_change',
                'tombstone': 'tombstone', 'lain': 'other'}
AUDIT = {'N_log': 'N_log', 'N_prod': 'N_prod', 'hitung': 'counts', 'per_topik': 'per_topic',
         'kolom_pesan_log': 'log_message_columns', 'contoh_pesan_produksi': 'production_message_samples',
         'C_safe': 'C_safe', '_galat': '_error'}
SAMPLE = {'topik': 'topic', 'offset': 'offset', 'db': 'db', 'skema': 'schema', 'tabel': 'table',
          'op': 'op', 'kolom': 'columns'}
METADATA = {'commit': 'commit', 'cabang': 'branch', 'perubahan_terlacak': 'tracked_changes',
            'argumen': 'arguments', 'seed_jeda': 'delay_seed', 'jeda_maks_s': 'max_delay_s',
            'mulai': 'started', 'mesin': 'machine'}
ARGUMENT = {'mode': 'mode', 'skenario': 'scenario', 'ulangan': 'runs', 'ulangan_baseline': 'baseline_runs',
            'batas': 'timeout', 'jeda_amati': 'observe_delay', 'lewati_periksa': 'skip_checks',
            'seed': 'seed', 'izinkan_kotor': 'allow_dirty'}
FILES = {'konfigurasi-debezium.json': 'debezium-config.json', 'kafka-awal.json': 'kafka-start.json'}

unknown: set[str] = set()


def rename(obj: dict, table: dict, where: str) -> dict:
    out = {}
    for key, value in obj.items():
        if key not in table:
            unknown.add(f'{where}.{key}')
        out[table.get(key, key)] = value
    return out


def answers(block: dict | None) -> dict | None:
    if not isinstance(block, dict):
        return block
    return {QUERY_NAME.get(name, name): rename(a, ANSWER, 'answer') if isinstance(a, dict) else a
            for name, a in block.items()}


def assessment(block: dict | None) -> dict | None:
    if not isinstance(block, dict):
        return block
    out = rename(block, ASSESSMENT, 'assessment')
    per_query = {}
    for name, value in (out.get('per_query') or {}).items():
        value = rename(value, PER_QUERY, 'per_query')
        value['kind'] = KIND.get(value.get('kind'), value.get('kind'))
        per_query[QUERY_NAME.get(name, name)] = value
    out['per_query'] = per_query
    return out


def record(rec: dict) -> dict:
    out = rename(rec, RECORD, 'record')
    out['mode'] = MODE.get(out.get('mode'), out.get('mode'))
    if out.get('outcome') in OUTCOME:
        out['outcome'] = OUTCOME[out['outcome']]
    if out.get('scenario') in SCENARIOS:
        out['title'] = SCENARIOS[out['scenario']].title
    for key in ('answers_before', 'answers_after'):
        if key in out:
            out[key] = answers(out[key])
    if 'assessment' in out:
        out['assessment'] = assessment(out['assessment'])
    if isinstance(out.get('expectations'), dict):
        out['expectations'] = rename(out['expectations'], EXPECTATION, 'expectations')
    if isinstance(out.get('availability'), dict):
        out['availability'] = rename(out['availability'], AVAILABILITY, 'availability')
    return out


def kafka_audit(audit: dict) -> dict:
    out = rename(audit, AUDIT, 'kafka_audit')
    if isinstance(out.get('counts'), dict):
        out['counts'] = rename(out['counts'], MESSAGE_KIND, 'counts')
    if isinstance(out.get('per_topic'), dict):
        out['per_topic'] = {t: rename(v, MESSAGE_KIND, 'per_topic') for t, v in out['per_topic'].items()}
    if isinstance(out.get('production_message_samples'), list):
        out['production_message_samples'] = [rename(s, SAMPLE, 'sample')
                                              for s in out['production_message_samples']]
    return out


def debezium_config(config: dict) -> dict:
    return {name: rename(v, {'table.include.list': 'table.include.list',
                             'hanya_ddl_event_log': 'only_ddl_event_log'}, 'debezium')
            for name, v in config.items()}


def metadata(meta: dict) -> dict:
    out = rename(meta, METADATA, 'metadata')
    if isinstance(out.get('arguments'), dict):
        out['arguments'] = rename(out['arguments'], ARGUMENT, 'arguments')
        out['arguments']['mode'] = MODE.get(out['arguments'].get('mode'), out['arguments'].get('mode'))
    return out


def main() -> int:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    source = Path(sys.argv[1])
    source = source if source.is_absolute() else ROOT / source
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / 'results/evaluation' / source.name
    target = target if target.is_absolute() else ROOT / target
    if target.exists():
        sys.exit(f'{target} already exists; choose another target')
    target.mkdir(parents=True)
    converted = 0
    for path in sorted(source.iterdir()):
        if path.is_dir():
            continue                                   # summaries are recomputed with extract.py
        name = FILES.get(path.name, path.name)
        if path.suffix != '.json':
            shutil.copy2(path, target / name)
            continue
        data = json.loads(path.read_text())
        if path.name.startswith(('baseline-', 'run-')):
            data = record(data)
        elif path.name == 'kafka-audit.json':
            data = kafka_audit(data)
        elif path.name == 'konfigurasi-debezium.json':
            data = debezium_config(data)
        elif path.name == 'metadata.json':
            data = metadata(data)
        (target / name).write_text(json.dumps(data, indent=2, ensure_ascii=False))
        converted += 1
    print(f'{converted} JSON files converted into {target.relative_to(ROOT) if target.is_relative_to(ROOT) else target}')
    if unknown:
        print('Keys kept as they are (not in the conversion tables): ' + ', '.join(sorted(unknown)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
