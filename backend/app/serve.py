"""One web service for hosts like Render: the dashboard at /, the API at /api.

    uvicorn app.serve:app --host 0.0.0.0 --port $PORT

The API app is mounted at /api (so /api/devices, /api/ws/live, ...), exactly the
paths the dashboard uses when it is built with VITE_API_URL=/api. The built
dashboard (WEB_DIR) is served at / with a fallback to index.html, so links like
/incidents/inc_0001 work after a reload.

API_MODULE chooses which API to serve (default: the stand-in API with triage).
When the full agent API exists, set API_MODULE to it; nothing else changes.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from .config import env

api_module = importlib.import_module(env("API_MODULE", "app.dev_api"))
api: FastAPI = api_module.app
WEB_DIR = Path(env("WEB_DIR", str(Path(__file__).resolve().parents[2] / "dashboard" / "dist")))


class SinglePageApp(StaticFiles):
    """Static files, but unknown paths return index.html (client-side routes)."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or "." in path.rsplit("/", 1)[-1]:
                raise                      # a missing file (e.g. .js) is a real 404
            return await super().get_response("index.html", scope)


# a mounted app's lifespan does not run by itself: start ingestion, triage, simulator link here
app = FastAPI(title="HVAC multi-agent system", lifespan=api.router.lifespan_context, docs_url=None, redoc_url=None)
app.mount("/api", api)


@app.get("/health")
def health():
    """Render's health check (the API's own is /api/health)."""
    return {"ok": True}


if WEB_DIR.is_dir():
    app.mount("/", SinglePageApp(directory=WEB_DIR, html=True), name="dashboard")
elif os.getenv("REQUIRE_WEB") == "1":
    raise RuntimeError(f"dashboard build not found at {WEB_DIR}")
