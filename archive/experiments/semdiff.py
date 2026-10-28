"""
Diff semantik dua berkas ontologi Turtle.
Membandingkan graf RDF (bukan teks), sehingga urutan dan format penulisan
tidak memengaruhi hasil. Juga melaporkan jumlah baris komentar, karena
komentar tidak termasuk graf RDF dan dapat hilang saat serialisasi ulang.
Pemakaian: python semdiff.py <sebelum.ttl> <sesudah.ttl>
"""
import sys
from rdflib import Graph
from rdflib.compare import to_isomorphic, graph_diff

a = Graph().parse(sys.argv[1], format='turtle')
b = Graph().parse(sys.argv[2], format='turtle')
_, only_a, only_b = graph_diff(to_isomorphic(a), to_isomorphic(b))
print(f'triple sebelum: {len(a)} | sesudah: {len(b)} | dihapus: {len(only_a)} | ditambah: {len(only_b)}')
for t in sorted(only_b): print('  +', ' '.join(x.n3() for x in t))
for t in sorted(only_a): print('  -', ' '.join(x.n3() for x in t))
for f in sys.argv[1:3]:
    lines = open(f, encoding='utf-8').read().splitlines()
    n_comment = sum(1 for l in lines if l.lstrip().startswith('#'))
    print(f'{f}: {len(lines)} baris, {n_comment} baris komentar')
