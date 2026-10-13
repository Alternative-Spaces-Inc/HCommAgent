import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_healthz_ok():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_healthz_reports_db_down_as_503(monkeypatch):
    from app import main as main_module

    def boom():
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(main_module, "db_ping", boom)
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded", "db": "down"}
