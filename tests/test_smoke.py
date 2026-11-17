"""Minimal smoke tests for the Pokemon Card Scanner API.

Purpose: catch the class of bug that `py_compile` cannot see — a name that only
becomes a NameError when a request actually hits the handler (the historical
`json.loads` in /api/collection/search and `time.time()` in the matcher
fallback both shipped that way).

Run with:  python -m pytest tests/ -q
Requires:  pytest (dev-only; not in requirements.txt)
"""
import os
import sys
import tempfile

import pytest

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC_DIR)

# Point the DB at a scratch directory before api.main builds any paths.
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pcs-smoke-"))


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from api.main import app

    # Deliberately not used as a context manager: that skips the lifespan (and
    # its multi-second CLIP warm-up) while still exercising the route handlers.
    return TestClient(app)


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_collection_requires_auth(client):
    assert client.get("/api/collection").status_code == 401


def test_stats_requires_auth(client):
    assert client.get("/api/stats").status_code == 401


def test_collection_search_requires_auth(client):
    response = client.get("/api/collection/search", params={"query": "pikachu"})
    assert response.status_code == 401


def test_card_search_returns_images_as_dict(client):
    """Regression: a missing `import json` made this quietly return images: [].

    The endpoint must not 500 and must return a list of results; when the local
    DB has a matching card its images must be a mapping, not a bare list.
    """
    response = client.get("/api/cards/search", params={"query": "pikachu", "limit": 5})
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["results"], list)
    for result in payload["results"]:
        assert not isinstance(result.get("images"), list), result


def test_password_reset_is_disabled(client):
    response = client.post(
        "/api/auth/reset-password",
        data={"email": "nobody@example.com", "new_password": "whatever123"},
    )
    assert response.status_code == 403


def test_oversized_upload_is_rejected(client):
    oversized = b"x" * (16 * 1024 * 1024)
    response = client.post(
        "/api/detect",
        files={"image": ("card.jpg", oversized, "image/jpeg")},
    )
    assert response.status_code == 413
