"""
Per-request transaction boundary that commits BEFORE the response is sent.

The finaliser of a `yield` dependency (where the commit used to happen) runs in modern FastAPI
after the client has received the response. A client that uses the result of a write at once,
such as the Executor calling /sync and then /finish with the version just created, can overtake
that commit: a foreign key is violated and the second request is rejected (finding of evaluation
F6, 5 of 60 runs). This route class commits after the handler finishes but before the response
is returned, and rolls back when the handler or the commit fails, so integrity errors still
reach the client as 409 through the application's error handlers.
"""
from collections.abc import Callable

from fastapi import Request, Response
from fastapi.routing import APIRoute
from fastapi.concurrency import run_in_threadpool

SESSION_KEY = 'ascam_db'
DONE_KEY = 'ascam_tx_done'


class TransactionalRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            try:
                response = await handler(request)
            except Exception:
                await _finish(request, commit=False)
                raise
            await _finish(request, commit=True)       # commit errors go on to the error handlers
            return response

        return route_handler


async def _finish(request: Request, commit: bool) -> None:
    db = getattr(request.state, SESSION_KEY, None)
    if db is None or getattr(request.state, DONE_KEY, False):
        return
    setattr(request.state, DONE_KEY, True)
    if not commit:
        await run_in_threadpool(db.rollback)
        return
    try:
        await run_in_threadpool(db.commit)
    except Exception:
        await run_in_threadpool(db.rollback)
        raise
