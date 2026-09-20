from fastapi.testclient import TestClient

from app.api.main import create_app


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/v1/queries",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )


def test_default_local_vite_origins_are_allowed():
    response = _preflight(TestClient(create_app()), "http://localhost:5173")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_custom_cors_origins_replace_the_defaults():
    client = TestClient(create_app(cors_origins=["https://research.example.com"]))

    allowed = _preflight(client, "https://research.example.com")
    rejected = _preflight(client, "http://localhost:5173")

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://research.example.com"
    assert rejected.status_code == 400
