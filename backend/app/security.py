"""Demo access key for the public deployment.

When DEMO_TOKEN is set (on the Azure server), every request that CHANGES something
(POST/PUT/PATCH/DELETE: inject a fault, approve an incident, switch the AI model)
must send the header `X-Demo-Token: <key>`. Reading is open, so anyone with the
link can watch the live system. When DEMO_TOKEN is empty (laptops), nothing changes.

    app.middleware("http")(demo_token_guard)
    GET /auth/check  -> {"required": true, "ok": false}   (the dashboard uses it)
"""
from __future__ import annotations

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse

from .config import env

HEADER = "X-Demo-Token"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def demo_token() -> str:
    return env("DEMO_TOKEN")


def token_ok(request: Request) -> bool:
    expected = demo_token()
    if not expected:
        return True
    given = request.headers.get(HEADER, "")
    return hmac.compare_digest(given.encode(), expected.encode())


async def demo_token_guard(request: Request, call_next):
    if request.method in WRITE_METHODS and not token_ok(request):
        return JSONResponse(status_code=401, content={
            "detail": "This action needs the demo access key. Click 'Unlock controls' at the top of the dashboard."})
    return await call_next(request)


def auth_status(request: Request) -> dict:
    return {"required": bool(demo_token()), "ok": token_ok(request)}
