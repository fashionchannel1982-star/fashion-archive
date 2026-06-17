"""
Fashion Archive — smoke tests.
Runs against the live local backend (localhost:8000).
Start the backend before running: uvicorn main:app --port 8000

Run: cd backend && pytest tests/ -v
"""

import pytest
import httpx

BASE = "http://localhost:8000"


@pytest.fixture(scope="session")
def client():
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


# ── /health ────────────────────────────────────────────────────────────────

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── /api/search ────────────────────────────────────────────────────────────

def test_search_happy_path(client):
    r = client.post("/api/search", json={"query": "black structured jacket", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert "results" in body
    assert "total" in body
    assert "processing_time_ms" in body
    assert isinstance(body["results"], list)
    assert body["total"] == len(body["results"])
    # Each result must have required fields
    for result in body["results"]:
        assert "moment_id" in result
        assert "brand" in result
        assert "confidence" in result
        assert isinstance(result["confidence"], int)


def test_search_empty_query_rejected(client):
    r = client.post("/api/search", json={"query": ""})
    assert r.status_code == 422


def test_search_blank_query_rejected(client):
    r = client.post("/api/search", json={"query": "   "})
    assert r.status_code == 422


def test_search_limit_capped(client):
    r = client.post("/api/search", json={"query": "dress", "limit": 999})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any("50" in str(d) for d in detail)


def test_search_limit_negative_rejected(client):
    r = client.post("/api/search", json={"query": "dress", "limit": -1})
    assert r.status_code == 422


def test_search_query_too_long_rejected(client):
    r = client.post("/api/search", json={"query": "x" * 501})
    assert r.status_code == 422


def test_search_max_limit_accepted(client):
    r = client.post("/api/search", json={"query": "coat", "limit": 50})
    assert r.status_code == 200


# ── /api/timeline ──────────────────────────────────────────────────────────

def test_timeline_shape(client):
    r = client.get("/api/timeline")
    assert r.status_code == 200
    body = r.json()
    assert "house" in body
    assert "season_type" in body
    assert "points" in body
    assert "total" in body
    assert isinstance(body["points"], list)
    assert body["total"] == len(body["points"])


def test_timeline_with_code(client):
    r = client.get("/api/timeline?code=tweed")
    assert r.status_code == 200
    body = r.json()
    assert "cross_year_echo" in body
    # Each point should have rep_moment key when code specified
    for point in body["points"]:
        assert "rep_moment" in point
        assert "codes" in point


def test_timeline_unknown_house_returns_empty(client):
    r = client.get("/api/timeline?house=NotAHouse")
    assert r.status_code == 200
    assert r.json()["points"] == []


# ── /api/admin/events ──────────────────────────────────────────────────────

def test_event_capture(client):
    """Search must fire an event that shows up in /api/admin/events."""
    # Get baseline count
    before = client.get("/api/admin/events").json()["total"]

    # Fire a search
    client.post("/api/search", json={"query": "test event capture", "limit": 1})

    # Allow async task to settle
    import time; time.sleep(0.5)

    after = client.get("/api/admin/events").json()["total"]
    assert after > before, "Event was not captured after search"


def test_admin_events_shape(client):
    r = client.get("/api/admin/events")
    assert r.status_code == 200
    body = r.json()
    assert "total" in body
    assert "recent" in body
    assert isinstance(body["recent"], list)
    if body["recent"]:
        evt = body["recent"][0]
        assert "event_type" in evt
        assert "created_at" in evt
