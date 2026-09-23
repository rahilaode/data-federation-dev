import logging
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from .transaction import KUNCI_SELESAI, KUNCI_SESI

log = logging.getLogger('ascam.knowledge')


def get_db(request: Request) -> Iterator[Session]:
    """Satu transaksi per permintaan. Commit dan rollback dilakukan TransactionalRoute sebelum
    respons dikirim; penutup ini hanya menutup sesi. Bila sebuah rute tidak memakai
    TransactionalRoute, perubahan tetap disimpan di sini sebagai cadangan, disertai peringatan."""
    db = request.app.state.session_factory()
    setattr(request.state, KUNCI_SESI, db)
    try:
        yield db
        if not getattr(request.state, KUNCI_SELESAI, False):
            log.warning('rute %s tidak memakai TransactionalRoute; commit setelah respons',
                        request.url.path)
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
