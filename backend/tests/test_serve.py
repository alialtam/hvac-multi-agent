"""Single-service mode (Render): dashboard at /, API at /api, client-side routes fall back to index.html."""
import importlib

from fastapi.testclient import TestClient


def test_dashboard_and_api_on_one_address(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>dashboard</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("WEB_DIR", str(tmp_path))
    monkeypatch.setenv("DEMO_TOKEN", "k")
    import app.serve as serve
    serve = importlib.reload(serve)
    c = TestClient(serve.app)
    assert c.get("/health").json() == {"ok": True}
    assert c.get("/api/settings/llm").status_code == 200                       # API under /api
    assert "dashboard" in c.get("/").text
    assert "dashboard" in c.get("/incidents/inc_0001").text                     # reload on a deep link
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/assets/missing.js").status_code == 404                      # missing file is a real 404
    assert c.post("/api/settings/llm", json={"provider": "rules"}).status_code == 401   # demo key still applies
