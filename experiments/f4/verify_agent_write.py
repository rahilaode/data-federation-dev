"""
F4a — Verifikasi kemampuan tulis Ontop Agent terhadap Ontop yang benar-benar berjalan.

Urutannya meniru langkah Executor (ADR-0004, ADR-0018):
  baca artefak -> tulis dengan penguncian sidik jari -> ontop validate -> muat ulang ->
  bandingkan jawaban SPARQL -> pulihkan dari cadangan -> muat ulang lagi.

Perubahan yang ditulis hanya berupa satu baris komentar pada ontologi, dan dikembalikan di
akhir. Dijalankan dari host: `python3 experiments/f4/verify_agent_write.py`
(hanya memakai pustaka standar).
"""
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT = 'http://127.0.0.1:18100'
SPARQL = 'http://localhost:8080/sparql'
TOKEN_FILE = ROOT / 'setup/ascam/knowledge/secrets/ontop_agent_api_tokens'
KUERI = 'SELECT ?p (COUNT(*) AS ?n) WHERE { ?s ?p ?o } GROUP BY ?p ORDER BY ?p'
KOMENTAR = '\n# Uji tulis Ontop Agent (F4a); baris ini dipulihkan di akhir uji.\n'


def token() -> str:
    baris = TOKEN_FILE.read_text().strip().splitlines()[0]
    return baris.split(':', 1)[1] if ':' in baris else baris


def panggil(metode: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f'{AGENT}{path}', data=data, method=metode,
                                     headers={'Authorization': f'Bearer {token()}',
                                              'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        isi = exc.read()
        try:
            return exc.code, json.loads(isi)
        except ValueError:
            return exc.code, {'detail': isi[:200].decode('utf-8', 'replace')}


def sidik_jari_graf() -> str:
    url = f'{SPARQL}?{urllib.parse.urlencode({"query": KUERI})}'
    request = urllib.request.Request(url, headers={'Accept': 'application/sparql-results+json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        hasil = json.loads(response.read())
    baris = sorted(json.dumps(b, sort_keys=True) for b in hasil['results']['bindings'])
    digest = hashlib.sha256(''.join(baris).encode()).hexdigest()[:16]
    return f'{digest} ({len(baris)} predikat)'


def judul(teks: str) -> None:
    print(f'\n===== {teks} =====')


def main() -> int:
    judul('1) artefak saat ini')
    status, daftar = panggil('GET', '/api/v1/artifacts')
    for a in daftar:
        print('  %-9s %-20s %7d bita  %s' % (a['kind'], a['name'], a['size'], a['sha256'][:16]))
    status, ontologi = panggil('GET', '/api/v1/artifacts/ontology')
    sha_awal = ontologi['sha256']
    print(f'  sidik jari graf sebelum : {sidik_jari_graf()}')

    judul('2) tulis ontologi dengan penguncian sidik jari')
    status, ditulis = panggil('PUT', '/api/v1/artifacts/ontology',
                              {'content': ontologi['content'] + KOMENTAR,
                               'expected_sha256': sha_awal})
    if status != 200:
        print('  GAGAL menulis:', ditulis)
        return 1
    backup_id = ditulis['backup_id']
    print(f"  ditulis  : sha256 {ditulis['sha256'][:16]}, cadangan {backup_id}")
    status, _ = panggil('PUT', '/api/v1/artifacts/ontology',
                        {'content': '# percobaan menimpa', 'expected_sha256': sha_awal})
    print(f'  penulisan dengan sidik jari lama -> HTTP {status} (diharapkan 409)')

    judul('3) ontop validate terhadap artefak baru')
    status, hasil = panggil('POST', '/api/v1/validate', {})
    keluaran = (hasil.get('output') or '').strip().splitlines()
    print(f"  ok={hasil.get('ok')} exit={hasil.get('exit_code')} {hasil.get('duration_ms')} ms")
    print(f"  keluaran : {keluaran[-1][:120] if keluaran else '-'}")

    judul('4) muat ulang Ontop')
    status, muat = panggil('POST', '/api/v1/reload')
    print(f"  ok={muat.get('ok')} | berhenti {muat.get('stop_ms')} ms | siap {muat.get('ready_ms')} ms"
          f" | total {muat.get('total_ms')} ms | {muat.get('error') or 'tanpa galat'}")
    print(f'  sidik jari graf sesudah : {sidik_jari_graf()}')

    judul('5) pulihkan dari cadangan')
    status, pulih = panggil('POST', '/api/v1/artifacts/restore', {'backup_id': backup_id})
    print(f"  dipulihkan: {pulih.get('kind')} sha256 {str(pulih.get('sha256'))[:16]}")
    status, ontologi_akhir = panggil('GET', '/api/v1/artifacts/ontology')
    sama = ontologi_akhir['sha256'] == sha_awal
    print(f"  sama dengan artefak awal : {'ya' if sama else 'TIDAK'}")
    panggil('POST', '/api/v1/reload')
    print(f'  sidik jari graf setelah pemulihan: {sidik_jari_graf()}')

    judul('6) daftar cadangan')
    status, cadangan = panggil('GET', '/api/v1/backups')
    for b in cadangan[:5]:
        print('  %-9s %-48s %7d bita' % (b['kind'], b['backup_id'], b['size']))
    return 0 if sama else 1


if __name__ == '__main__':
    sys.exit(main())
