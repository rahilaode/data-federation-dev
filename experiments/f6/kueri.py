"""
Himpunan kueri Q_k per skenario dan penilaiannya (proposal §3.10–3.11.3).

Setiap skenario memiliki tiga jenis kueri:
  terdampak  menyentuh kolom yang berubah;
  tetangga   tabel yang sama, kolom lain;
  kontrol    tabel lain, termasuk kueri lintas sumber.

Setiap eksekusi kueri dicatat lengkap: status HTTP, pesan galat Ontop, jumlah baris, dan
answer set terurut, sehingga baseline (§3.10.1) dan perlakuan (§3.10.2) dinilai dengan cara
yang sama.

  ResultPreserved(q)     = 1 bila kedua eksekusi berhasil dan answer set identik (pers. 3.19)
  ExecutionPreserved(q)  = 1 bila respons Ontop sesudah perubahan bukan galat (pers. 3.20)
  PreservationRatio(A_k) = rata-rata P_k(q) atas Q_k × 100 % (pers. 3.21)
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

SPARQL = 'http://localhost:8080/sparql'
B = 'PREFIX bansos: <http://bansos.go.id/ontology/>\n'

KONTROL = {
    'jumlah_per_kelas': ('kontrol', B + 'SELECT ?kelas (COUNT(?s) AS ?n) WHERE { ?s a ?kelas } '
                                    'GROUP BY ?kelas ORDER BY ?kelas'),
    'status_transaksi': ('kontrol', B + 'SELECT ?t ?status WHERE { ?t a bansos:TransaksiBansos ; '
                                    'bansos:statusTransaksi ?status } ORDER BY ?t'),
}

Q = {
    'a001': {
        'penerima': ('tetangga', B + 'SELECT ?p ?nama ?status WHERE { ?p a bansos:PenerimaBansos ; '
                                 'bansos:namaLengkap ?nama ; bansos:statusEkonomi ?status } ORDER BY ?p'),
        'email': ('terdampak', B + 'SELECT ?p ?email WHERE { ?p bansos:email ?email } ORDER BY ?p'),
        'tautan_penduduk': ('kontrol', B + 'SELECT ?p ?d WHERE { ?p bansos:memilikDataKependudukan ?d } '
                                       'ORDER BY ?p'),
        **KONTROL,
    },
    'a002': {
        'tipe_program': ('terdampak', B + 'SELECT ?p ?tipe WHERE { ?p a bansos:ProgramBansos ; '
                                      'bansos:tipeProgram ?tipe } ORDER BY ?p'),
        'nama_program': ('tetangga', B + 'SELECT ?p ?nama ?nominal WHERE { ?p a bansos:ProgramBansos ; '
                                     'bansos:namaProgram ?nama ; bansos:nominal ?nominal } ORDER BY ?p'),
        'transaksi_program': ('kontrol', B + 'SELECT ?t ?prog WHERE { ?t bansos:terdaftarPadaProgram ?prog } '
                                         'ORDER BY ?t'),
        **KONTROL,
    },
    'a003': {
        'tanggal_lahir': ('terdampak', B + 'SELECT ?s ?tgl WHERE { ?s a bansos:Penduduk ; '
                                       'bansos:tanggalLahir ?tgl } ORDER BY ?s'),
        'nama_pekerjaan': ('tetangga', B + 'SELECT ?s ?nama ?kerja WHERE { ?s a bansos:Penduduk ; '
                                       'bansos:namaPenduduk ?nama ; bansos:pekerjaan ?kerja } ORDER BY ?s'),
        'penerima_ke_penduduk': ('kontrol', B + 'SELECT ?p ?nama WHERE { ?p bansos:memilikDataKependudukan ?d . '
                                            '?d bansos:namaPenduduk ?nama } ORDER BY ?p'),
        **KONTROL,
    },
}


def jalankan(kueri: str, batas: float = 90.0) -> dict:
    """Satu eksekusi kueri, dicatat apa adanya (termasuk galat)."""
    url = SPARQL + '?' + urllib.parse.urlencode({'query': kueri})
    permintaan = urllib.request.Request(url, headers={'Accept': 'application/sparql-results+json'})
    mulai = time.perf_counter()
    try:
        with urllib.request.urlopen(permintaan, timeout=batas) as respons:
            isi = json.loads(respons.read())
        baris = sorted(json.dumps(b, sort_keys=True) for b in isi['results']['bindings'])
        return {'status': respons.status, 'ok': True, 'galat': None, 'n': len(baris),
                'hasil': baris, 'durasi_ms': int((time.perf_counter() - mulai) * 1000)}
    except urllib.error.HTTPError as exc:
        pesan = exc.read()[:600].decode('utf-8', 'replace')
        return {'status': exc.code, 'ok': False, 'galat': pesan, 'n': None, 'hasil': None,
                'durasi_ms': int((time.perf_counter() - mulai) * 1000)}
    except Exception as exc:                                        # noqa: BLE001
        return {'status': None, 'ok': False, 'galat': f'{type(exc).__name__}: {str(exc)[:300]}',
                'n': None, 'hasil': None, 'durasi_ms': int((time.perf_counter() - mulai) * 1000)}


def jalankan_himpunan(kode: str) -> dict:
    return {nama: jalankan(kueri) for nama, (_, kueri) in Q[kode].items()}


def jalankan_stabil(kode: str, percobaan: int = 6, jeda: float = 2.0) -> dict:
    """Untuk cuplikan DASAR: diulang bila endpoint belum menjawab (bukan untuk sesudah perubahan,
    karena galat sesudah perubahan justru yang diukur)."""
    hasil = {}
    for _ in range(percobaan):
        hasil = jalankan_himpunan(kode)
        if all(h['status'] is not None for h in hasil.values()):
            return hasil
        time.sleep(jeda)
    return hasil


def nilai(kode: str, sebelum: dict, sesudah: dict) -> dict:
    """ResultPreserved dan ExecutionPreserved per kueri, serta PreservationRatio skenario."""
    per_kueri = {}
    for nama, (jenis, _) in Q[kode].items():
        a, b = sebelum.get(nama) or {}, sesudah.get(nama) or {}
        per_kueri[nama] = {
            'jenis': jenis,
            'result_preserved': int(bool(a.get('ok') and b.get('ok') and a['hasil'] == b['hasil'])),
            'execution_preserved': int(bool(b.get('ok'))),
            'baris_sebelum': a.get('n'), 'baris_sesudah': b.get('n'),
            'status_sesudah': b.get('status'),
        }
    kunci = 'result_preserved' if kode == 'a003' else 'execution_preserved'
    rasio = 100.0 * sum(v[kunci] for v in per_kueri.values()) / len(per_kueri)
    return {'metrik': kunci, 'preservation_ratio': rasio, 'per_kueri': per_kueri}


def sesuai_harapan(kode: str, sesudah: dict, penilaian: dict) -> dict:
    """Harapan teoretis skenario perlakuan (proposal §3.10.2, hlm. 104)."""
    if kode == 'a001':
        email = sesudah.get('email') or {}
        return {'kueri_lama_valid': all(v['execution_preserved'] for n, v in penilaian['per_kueri'].items()
                                        if n != 'email'),
                'kueri_baru_mengembalikan_hasil': bool(email.get('ok') and (email.get('n') or 0) > 0)}
    if kode == 'a002':
        tipe = sesudah.get('tipe_program') or {}
        return {'kueri_lama_tetap_berjalan': bool(tipe.get('ok')),
                'answer_set_kosong': bool(tipe.get('ok') and tipe.get('n') == 0)}
    return {'answer_set_identik': penilaian['preservation_ratio'] == 100.0}
