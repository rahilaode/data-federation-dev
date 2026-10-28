"""
Effector untuk Σ'_S: redeploy VDB Teiid melalui deployment scanner WildFly.

Dasar:
  - Teiid Documentation, "Deploying VDBs" (Direct File Deployment):
    VDB disalin ke standalone/deployments dan marker .dodeploy dibuat;
    VDB dengan nama sama akan digantikan.
  - README deployments WildFly Core (MARKER FILES):
      .dodeploy    -> dibuat pengguna, meminta (re)deploy
      .isdeploying -> dibuat scanner selama proses deploy
      .deployed    -> dibuat scanner bila deploy berhasil
                      (JANGAN dihapus: menghapusnya memicu undeploy)
      .failed      -> dibuat scanner bila deploy gagal (berisi penyebab)
  - Teiid Documentation, "VDB Versioning": redeploy dengan nama/versi
    sama memutus koneksi lama dan mengarahkan koneksi baru ke VDB baru.
    Karena itu Ontop wajib di-reload SETELAH langkah ini.

Scanner diset ke manual deploy mode untuk XML (setup.cli), sehingga
redeploy hanya terjadi ketika fungsi ini membuat marker .dodeploy.

Mesin status marker (diverifikasi dari source WildFly Core 11.1.1.Final,
FileSystemDeploymentService: handleSuccessResult() dan writeFailedMarker()):
  sukses -> hapus .dodeploy & .failed, tulis .deployed dengan
            mtime = mtime berkas VDB (setLastModified(doDeployTimestamp)),
            lalu hapus .isdeploying
  gagal  -> hapus .dodeploy, .deployed, .undeployed, lalu tulis .failed
Proses selesai bila .dodeploy dan .isdeploying sudah tidak ada.
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger('ascam.teiid')


@dataclass
class DeployResult:
    ok: bool
    elapsed_s: float
    detail: str = ''


def _marker(vdb: Path, suffix: str) -> Path:
    return vdb.with_name(vdb.name + suffix)


def redeploy_vdb(vdb_path: str | Path, timeout: int = 120,
                 poll: float = 0.5) -> DeployResult:
    vdb      = Path(vdb_path)
    dodeploy = _marker(vdb, '.dodeploy')
    deployed = _marker(vdb, '.deployed')
    failed   = _marker(vdb, '.failed')

    if not vdb.exists():
        return DeployResult(False, 0.0, f'VDB tidak ditemukan: {vdb}')

    # .failed lama dibersihkan agar tidak terbaca sebagai hasil event ini
    if failed.exists():
        failed.unlink()

    t0 = time.time()
    dodeploy.touch()
    log.info('[Teiid] Marker %s dibuat, menunggu scanner...', dodeploy.name)

    deadline = t0 + timeout
    while time.time() < deadline:
        # Scanner selesai memproses bila .dodeploy dan .isdeploying sudah hilang
        in_progress = dodeploy.exists() or _marker(vdb, '.isdeploying').exists()
        if not in_progress:
            if failed.exists():
                reason = failed.read_text(encoding='utf-8', errors='ignore')[:500]
                log.error('[Teiid] Deploy GAGAL: %s', reason)
                return DeployResult(False, time.time() - t0, reason)
            if deployed.exists():
                elapsed = time.time() - t0
                # Pemeriksaan kewarasan: WildFly menyamakan mtime .deployed
                # dengan mtime VDB yang di-deploy (presisi milidetik).
                drift = abs(deployed.stat().st_mtime - vdb.stat().st_mtime)
                if drift > 1.0:
                    log.warning('[Teiid] mtime .deployed berbeda %.3f s dari VDB; '
                                'kemungkinan marker bukan hasil deploy ini', drift)
                    return DeployResult(False, elapsed, 'marker .deployed tidak cocok dengan VDB')
                log.info('[Teiid] VDB ter-deploy dalam %.2f s', elapsed)
                return DeployResult(True, elapsed)
            return DeployResult(False, time.time() - t0,
                                'scanner selesai tanpa .deployed maupun .failed')
        time.sleep(poll)

    return DeployResult(False, time.time() - t0,
                        f'Timeout {timeout}s menunggu deployment scanner')
