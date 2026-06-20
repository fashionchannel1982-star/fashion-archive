"""
Fashion Archive — integration smoke tests.
Requires the live backend at localhost:8000 (auto-skipped if not running).
Start with: cd backend && uvicorn main:app --port 8000

Run all integration tests: pytest -m integration -v
Run with unit tests:        pytest -v
"""

import pytest
import httpx

BASE = "http://localhost:8000"

pytestmark = pytest.mark.integration


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


def test_search_confidence_integers(client):
    r = client.post("/api/search", json={"query": "evening gown", "limit": 10})
    assert r.status_code == 200
    for result in r.json()["results"]:
        assert isinstance(result["confidence"], int)
        assert 0 <= result["confidence"] <= 100


def test_search_synthesis_field_present(client):
    """synthesis key must always be present, even when None."""
    r = client.post("/api/search", json={"query": "tailored coat", "limit": 5})
    assert r.status_code == 200
    assert "synthesis" in r.json()


def test_search_season_type_present(client):
    """season_type must be present on every result."""
    r = client.post("/api/search", json={"query": "dress", "limit": 5})
    assert r.status_code == 200
    for result in r.json()["results"]:
        assert "season_type" in result


# ── /api/shows ─────────────────────────────────────────────────────────────

def test_shows_list(client):
    r = client.get("/api/shows")
    assert r.status_code == 200
    body = r.json()
    assert "shows" in body
    assert isinstance(body["shows"], list)
    assert len(body["shows"]) > 0


def test_shows_list_fields(client):
    r = client.get("/api/shows")
    for show in r.json()["shows"]:
        for field in ("id", "brand", "season", "year", "show_key", "status"):
            assert field in show, f"missing field {field!r}"


def test_shows_detail_by_key(client):
    shows = client.get("/api/shows").json()["shows"]
    key = shows[0]["show_key"]
    r = client.get(f"/api/shows/{key}")
    assert r.status_code == 200
    body = r.json()
    assert body["show_key"] == key


def test_shows_detail_not_found(client):
    r = client.get("/api/shows/does-not-exist-xyz")
    assert r.status_code == 404


def test_shows_internal_view_blocked_without_param(client):
    """Internal view fields must not leak in the default response."""
    shows = client.get("/api/shows").json()["shows"]
    key = shows[0]["show_key"]
    body = client.get(f"/api/shows/{key}").json()
    for sensitive in ("video_id", "task_id", "health", "sample_moments"):
        assert sensitive not in body, f"internal field {sensitive!r} leaked to client view"


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
    for point in body["points"]:
        assert "rep_moment" in point
        assert "codes" in point


def test_timeline_unknown_house_returns_empty(client):
    r = client.get("/api/timeline?house=NotAHouse")
    assert r.status_code == 200
    assert r.json()["points"] == []


# ── /api/admin/events ──────────────────────────────────────────────────────

def test_event_capture(client):
    before = client.get("/api/admin/events").json()["total"]
    client.post("/api/search", json={"query": "test event capture", "limit": 1})
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
