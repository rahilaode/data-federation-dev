"""
Utilitas penyuntingan berkas Turtle pada tingkat teks.

Dipakai agar ASCAM dapat menambah dan menghapus bagian **miliknya sendiri** tanpa menulis ulang
seluruh berkas, sehingga komentar dan tata letak tulisan manusia tetap utuh (invarian
kepemilikan P6). Setiap blok yang ditulis ASCAM diapit penanda yang memuat kuncinya, sehingga
penghapusan kembali bersifat deterministik.
"""
PENANDA_BLOK = '# ─── blok terkelola ASCAM (jangan disunting manual) ───'
AWAL = '# ascam:mulai'
AKHIR = '# ascam:selesai'


def prefix_map(text: str) -> dict[str, str]:
    """{namespace: prefix} dari deklarasi @prefix pada berkas."""
    mapping = {}
    for baris in text.splitlines():
        potongan = baris.strip()
        if potongan.lower().startswith('@prefix'):
            bagian = potongan.split()
            if len(bagian) >= 3:
                mapping[bagian[2].strip('<>')] = bagian[1].rstrip(':')
    return mapping


def shorten(iri: str, prefixes: dict[str, str]) -> str:
    for namespace, prefix in sorted(prefixes.items(), key=lambda kv: -len(kv[0])):
        if iri.startswith(namespace):
            sisa = iri[len(namespace):]
            if sisa and all(c.isalnum() or c in '_-' for c in sisa):
                return f'{prefix}:{sisa}'
    return f'<{iri}>'


def ensure_prefixes(text: str, dibutuhkan: dict[str, str]) -> str:
    """Menambahkan deklarasi @prefix yang belum ada, setelah deklarasi terakhir."""
    ada = prefix_map(text)
    tambahan = [f'@prefix {prefix}: <{namespace}> .'
                for prefix, namespace in dibutuhkan.items() if namespace not in ada]
    if not tambahan:
        return text
    baris = text.splitlines()
    terakhir = max((i for i, b in enumerate(baris) if b.strip().lower().startswith('@prefix')),
                   default=-1)
    baris[terakhir + 1:terakhir + 1] = tambahan
    return '\n'.join(baris) + ('\n' if text.endswith('\n') else '')


def append_block(text: str, kunci: str, isi: str) -> str:
    """Menambahkan blok terkelola beserta penandanya di akhir berkas."""
    dasar = text if text.endswith('\n') else text + '\n'
    if PENANDA_BLOK not in dasar:
        dasar += f'\n{PENANDA_BLOK}\n'
    return f'{dasar}\n{AWAL} {kunci}\n{isi.strip()}\n{AKHIR} {kunci}\n'


def remove_block(text: str, kunci: str) -> tuple[str, int]:
    """Menghapus blok terkelola dengan kunci tertentu; mengembalikan (teks, jumlah blok).

    Bila tidak ada blok terkelola yang tersisa, penanda blok ikut dihapus sehingga berkas
    kembali persis seperti sebelum ASCAM menyuntingnya.
    """
    baris = text.splitlines(keepends=True)
    hasil, jumlah, lewati = [], 0, False
    for item in baris:
        potongan = item.strip()
        if potongan == f'{AWAL} {kunci}':
            lewati, jumlah = True, jumlah + 1
            if hasil and hasil[-1].strip() == '':
                hasil.pop()
            continue
        if lewati:
            if potongan == f'{AKHIR} {kunci}':
                lewati = False
            continue
        hasil.append(item)
    keluaran = ''.join(hasil)
    if jumlah and AWAL not in keluaran:
        keluaran = keluaran.replace(PENANDA_BLOK + '\n', '').rstrip() + '\n'
    return keluaran, jumlah


def has_block(text: str, kunci: str) -> bool:
    return f'{AWAL} {kunci}' in text
