"""
Penyimpanan artefak yang aman.

atomic_write():
    Menulis ke berkas sementara di folder yang sama lalu os.replace().
    Pembaca (scanner WildFly, Ontop) tidak akan pernah melihat berkas
    yang setengah tertulis. Berkas sementara diawali titik dan berakhiran
    .tmp sehingga tidak dianggap sebagai deployment oleh scanner.

Snapshot:
    Menyalin isi artefak F sebelum eksekusi agar dapat dikembalikan
    (revert) bila verifikasi gagal. Snapshot disimpan di luar folder
    deployment agar tidak terbaca scanner.
"""

import logging
import os
import shutil
import tempfile
import time
from pathlib import Path

log = logging.getLogger('ascam.store')


def atomic_write(path: str | Path, content: str, encoding: str = 'utf-8') -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding=encoding) as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        # Pertahankan permission berkas lama (mkstemp membuat 0600)
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


class Snapshot:
    """Salinan artefak sebelum eksekusi satu adaptation plan."""

    def __init__(self, root: str | Path, event_id: str, paths: list[str | Path]):
        stamp = time.strftime('%Y%m%dT%H%M%S')
        self.dir = Path(root) / f'{stamp}_{event_id}'
        self.dir.mkdir(parents=True, exist_ok=True)
        self._files: dict[Path, Path] = {}
        for p in map(Path, paths):
            if p.exists():
                dst = self.dir / p.name
                shutil.copy2(p, dst)
                self._files[p] = dst
        log.info('[Snapshot] %d artefak disalin ke %s', len(self._files), self.dir)

    def restore(self) -> list[Path]:
        restored = []
        for original, copy in self._files.items():
            atomic_write(original, copy.read_text(encoding='utf-8'))
            restored.append(original)
        log.warning('[Snapshot] Revert %d artefak dari %s', len(restored), self.dir)
        return restored
