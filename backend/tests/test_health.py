from fastapi.testclient import TestClient

from backend.app.main import app


def test_health() -> None:
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_frontend_route_does_not_serve_files_outside_the_built_assets() -> None:
    response = TestClient(app).get("/%2e%2e/pyproject.toml")

    assert 'name = "ttb-label-verification"' not in response.text
