# Backend API (Abdulelah)

The real FastAPI app that replaces `backend/app/dev_api.py` (Ali's stand-in with live data but no agents).

- Endpoints, shapes and examples: `contracts/api.md` and `contracts/api_examples/`.
  The dashboard is built against those files, so match them exactly.
- Start from `dev_api.py`: the devices, telemetry and WebSocket parts can be reused as they are.
- `POST /incidents/{id}/approve` and `/reject` resume the paused LangGraph run (Ahmed's graph).
- CORS must allow the dashboard origin.
- Bad input returns a clear 4xx error with a message, never a 500 (robustness is graded).
- Tests: FastAPI `TestClient` against each endpoint with the example data.

Run it:

```bash
cd backend
uvicorn app.main:app --port 8000 --host 0.0.0.0
```
