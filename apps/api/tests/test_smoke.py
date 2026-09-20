"""
Smoke tests: verify the app starts, /health works, and OpenAPI schema is populated.
These tests do not require a real database or S3.
"""


def test_app_starts(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_openapi_docs(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "paths" in data
    # Auth (incl. 2FA), users, admin, setup, notifications, events, site and
    # mail settings, the object proxy and health. The exact set is pinned by
    # test_openapi_surface.py; this only asserts the schema is populated.
    assert len(data["paths"]) > 20


def test_openapi_title(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["info"]["title"] == "FilmBill API"


def test_openapi_contact(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "contact" in data["info"]
    assert data["info"]["contact"]["name"] == "FilmBill"


def test_openapi_license(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "license" in data["info"]
    # AGPL-3.0, not FreeFrame's MIT. The platform modules derived from
    # FreeFrame keep their MIT notice in LICENSES/MIT-FreeFrame.txt; the
    # combined work is AGPL.
    assert data["info"]["license"]["name"] == "AGPL-3.0-or-later"


def test_docs_ui(client):
    """Swagger UI should be available."""
    resp = client.get("/docs")
    assert resp.status_code == 200


def test_redoc_ui(client):
    """ReDoc UI should be available."""
    resp = client.get("/redoc")
    assert resp.status_code == 200
