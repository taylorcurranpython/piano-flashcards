"""Piano flashcards server: the page in public/ plus a small JSON API.

Set ACCESS_TOKEN to require a token (the page asks for it once per device); leave it unset on a private network.
Run:  uvicorn app.server:app --host 0.0.0.0 --port 8000
"""

import hmac
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import drill
from .db import init_db, session

TOKEN = os.environ.get("ACCESS_TOKEN", "")
PUBLIC = Path(__file__).resolve().parent.parent / "public"


def authorized(request) -> bool:
    if not TOKEN:
        return True
    supplied = request.headers.get("x-access-token", "")
    return bool(supplied) and hmac.compare_digest(supplied, TOKEN)


def api(fn):
    async def endpoint(request):
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        body = await request.json() if request.method == "POST" else {}
        try:
            with session() as s:
                out = fn(s, body or {})
        except (KeyError, TypeError, ValueError) as e:
            return JSONResponse({"error": f"bad request: {e}"}, status_code=400)
        return JSONResponse(out, status_code=400 if "error" in out else 200)
    return endpoint


async def health(_):
    return JSONResponse({"ok": True})


def build_app() -> Starlette:
    init_db()
    routes = [
        Route("/health", health),
        Route("/api/state", api(lambda s, b: drill.state(s)), methods=["GET"]),
        Route("/api/practice", api(lambda s, b: drill.start_practice(s, b.get("scope", ""))), methods=["POST"]),
        Route("/api/answer", api(lambda s, b: drill.record_answer(
            s, int(b["practice_id"]), str(b["card_id"]), b.get("answer"),
            None if b.get("ms") is None else float(b["ms"]), b.get("engine"))), methods=["POST"]),
        Route("/api/finish", api(lambda s, b: drill.finish_practice(s, int(b["practice_id"]))), methods=["POST"]),
    ]
    if not os.environ.get("VERCEL"):  # on Vercel the CDN serves public/
        routes.append(Mount("/", StaticFiles(directory=PUBLIC, html=True)))
    return Starlette(routes=routes)


app = build_app()
