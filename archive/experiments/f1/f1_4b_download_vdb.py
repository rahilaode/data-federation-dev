"""
F1.4b — Uji kelayakan: mengunduh isi deployment VDB lewat management API dan memverifikasi
keutuhannya.

Dasar: WildFly Core 11.1.1 `DomainUtil.java` — parameter `useStreamAsResponse` (URL) atau
header `org.wildfly.useStreamAsResponse` mengembalikan attached stream sebagai isi respons.

Pertanyaan:
  1. Apakah `read-content` + `useStreamAsResponse` mengembalikan isi berkas VDB?
  2. Apakah isi itu identik byte-per-byte dengan berkas di folder deployment (dipasang
     read-only di /deployments hanya untuk pembanding)?
  3. Algoritme apa yang menghasilkan hash konten yang dilaporkan WildFly
     (dugaan: SHA-1, 20 byte)? Apakah cocok dengan isi unduhan?
"""
import base64
import hashlib
import json
import os
from datetime import datetime

import httpx

TEIID = os.getenv('TEIID_HOST', 'data-federation-teiid')
MGMT = f'http://{TEIID}:9990/management'
AUTH = httpx.DigestAuth(os.getenv('MGMT_USER', 'admin'), os.getenv('MGMT_PASSWORD', 'Password12345_'))
DEPLOYMENT = os.getenv('VDB_DEPLOYMENT', 'government-vdb.xml')
LOCAL = f'/deployments/{DEPLOYMENT}'
REPORT = {}


def main():
    with httpx.Client(auth=AUTH, timeout=30) as c:
        addr = [{'deployment': DEPLOYMENT}]
        meta = c.post(MGMT, json={'operation': 'read-resource', 'address': addr}).json()['result']
        reported = base64.b64decode(meta['content'][0]['hash']['BYTES_VALUE'])
        print(f'hash dilaporkan WildFly : {reported.hex()} ({len(reported)} byte)')

        results = {}
        for label, kwargs in (('parameter URL', {'params': {'useStreamAsResponse': ''}}),
                              ('header', {'headers': {'org.wildfly.useStreamAsResponse': '0'}})):
            r = c.post(MGMT, json={'operation': 'read-content', 'address': addr}, **kwargs)
            body = r.content
            results[label] = body
            print(f'\n[{label}] HTTP {r.status_code}, content-type {r.headers.get("content-type")}, '
                  f'{len(body)} byte')
            print('  awal isi:', body[:80].decode('utf-8', 'replace').replace('\n', ' '))
            REPORT[label] = {'status': r.status_code, 'content_type': r.headers.get('content-type'),
                             'bytes': len(body)}

    body = results['parameter URL']
    digests = {name: hashlib.new(name, body).digest() for name in ('sha1', 'sha256', 'md5')}
    matched = [name for name, d in digests.items() if d == reported]
    print(f'\nalgoritme hash yang cocok dengan laporan WildFly: {matched or "tidak ada"}')
    print(f'unduhan lewat URL dan header identik: {results["parameter URL"] == results["header"]}')
    REPORT['hash'] = {'reported_hex': reported.hex(), 'matched_algorithms': matched,
                      'sha256_of_download': hashlib.sha256(body).hexdigest()}

    if os.path.exists(LOCAL):
        local = open(LOCAL, 'rb').read()
        same = local == body
        print(f'identik dengan berkas di disk ({len(local)} byte): {same}')
        REPORT['identical_to_disk'] = same
    else:
        print('berkas lokal tidak dipasang; perbandingan dilewati')


if __name__ == '__main__':
    try:
        main()
    finally:
        os.makedirs('/out', exist_ok=True)
        out = f"/out/f1_4b_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
        with open(out, 'w') as fh:
            json.dump(REPORT, fh, indent=2)
        print(f'laporan: results/f1/{os.path.basename(out)}')
