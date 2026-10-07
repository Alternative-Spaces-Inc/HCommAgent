"""HCA-P17: /privacy is a public HTML page (the Web Store listing links it)."""

from fastapi.testclient import TestClient

from app.main import app


def test_privacy_is_public_html():
    c = TestClient(app)
    r = c.get("/privacy")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Human Collections Agent: privacy policy" in r.text
    assert "No browsing history" in r.text


def test_privacy_needs_no_auth_and_is_not_in_the_api_schema():
    c = TestClient(app)
    assert c.get("/privacy", headers={"Authorization": "Bearer nonsense"}).status_code == 200
    assert "/privacy" not in c.get("/openapi.json").json()["paths"]
