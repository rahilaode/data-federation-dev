"""
Verifikasi pasca-eksekusi (penutup loop MAPE-K).

Setiap VerificationCheck adalah kueri SPARQL COUNT yang dikirim ke Ontop.
Karena Ontop menerjemahkan SPARQL menjadi SQL ke Teiid, kueri yang
menyentuh property terdampak sekaligus menguji konsistensi ketiga artefak:
  - T' dan M' dapat dimuat Ontop,
  - SQL hasil unfolding valid terhadap Σ'_S di Teiid,
  - Σ'_S konsisten dengan skema fisik Σ_i di sumber.

Ekspektasi:
  'ok'       -> kueri berhasil dieksekusi (HTTP 200), nilai apa pun diterima.
                Dipakai untuk ADD COLUMN, karena kolom baru umumnya masih NULL.
  'zero'     -> berhasil dan hasilnya 0 (property yang di-deprecate).
  'positive' -> berhasil dan hasilnya > 0 (data lama tetap terjawab).
"""

import logging
from dataclasses import dataclass, field

import requests

log = logging.getLogger('ascam.verify')


@dataclass
class VerificationCheck:
    name: str
    query: str
    expect: str = 'ok'            # 'ok' | 'zero' | 'positive'


@dataclass
class CheckResult:
    name: str
    passed: bool
    http_status: int | None = None
    value: int | None = None
    detail: str = ''


@dataclass
class VerificationReport:
    passed: bool
    results: list[CheckResult] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {'passed': self.passed,
                'results': [r.__dict__ for r in self.results]}


def run_checks(sparql_url: str, checks: list[VerificationCheck],
               timeout: int = 30) -> VerificationReport:
    results = [_run_one(sparql_url, c, timeout) for c in checks]
    report = VerificationReport(all(r.passed for r in results), results)
    log.info('[Verify] %d/%d lolos', sum(r.passed for r in results), len(results))
    return report


def _run_one(url: str, check: VerificationCheck, timeout: int) -> CheckResult:
    try:
        r = requests.get(url, params={'query': check.query},
                         headers={'Accept': 'application/sparql-results+json'},
                         timeout=timeout)
    except requests.exceptions.RequestException as exc:
        return CheckResult(check.name, False, detail=str(exc))

    if r.status_code != 200:
        return CheckResult(check.name, False, r.status_code, detail=r.text[:300])

    try:
        bindings = r.json()['results']['bindings']
        value = int(bindings[0]['n']['value']) if bindings else 0
    except (ValueError, KeyError, IndexError) as exc:
        return CheckResult(check.name, False, 200, detail=f'respons tidak terbaca: {exc}')

    passed = {'ok': True, 'zero': value == 0, 'positive': value > 0}[check.expect]
    detail = '' if passed else f'ekspektasi {check.expect}, diperoleh {value}'
    log.info('[Verify] %-28s n=%-6d %s', check.name, value, 'LOLOS' if passed else 'GAGAL')
    return CheckResult(check.name, passed, 200, value, detail)


def count_class(prefix_iri: str, cls: str) -> str:
    return (f'PREFIX bansos: <{prefix_iri}>\n'
            f'SELECT (COUNT(?s) AS ?n) WHERE {{ ?s a bansos:{cls} }}')


def count_property(prefix_iri: str, prop: str) -> str:
    local = prop.split(':', 1)[-1]
    return (f'PREFIX bansos: <{prefix_iri}>\n'
            f'SELECT (COUNT(?o) AS ?n) WHERE {{ ?s bansos:{local} ?o }}')
