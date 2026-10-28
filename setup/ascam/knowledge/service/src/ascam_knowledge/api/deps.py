import logging
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from .transaction import DONE_KEY, SESSION_KEY

log = logging.getLogger('ascam.knowledge')


def get_db(request: Request) -> Iterator[Session]:
    """One transaction per request. TransactionalRoute commits or rolls back before the response
    is sent; this finaliser only closes the session. If a route does not use
    TransactionalRoute, the changes are still committed here as a fallback, with a warning."""
    db = request.app.state.session_factory()
    setattr(request.state, SESSION_KEY, db)
    try:
        yield db
        if not getattr(request.state, DONE_KEY, False):
            log.warning('route %s does not use TransactionalRoute; committing after the response',
                        request.url.path)
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
