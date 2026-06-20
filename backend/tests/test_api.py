"""
Fashion Archive — integration smoke tests.
Requires the live backend at localhost:8000 (auto-skipped if not running).
Start with: cd backend && uvicorn main:app --port 8000

Run all integration tests: pytest -m integration -v
Run everything:             pytest -v

NOTE: confidence_floor test asserts >= 7 (SIMILARITY_THRESHOLD*100), NOT >= 60.
See tests/README.md FINDINGS section for the threshold discrepancy.
"""

import sys
import os
import pytest
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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

def test_search_multi_word_concept_query(client):
    """Multi-word concept query → 200, well-formed result dicts with required fields."""
    r = client.post("/api/search", json={"query": "black structured jacket exaggerated shoulders", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert "results" in body
    assert "total" in body
    assert "processing_time_ms" in body
    assert body["total"] == len(body["results"])
    for result in body["results"]:
        for field in ("moment_id", "brand", "season", "year", "confidence",
                      "timestamp_start", "timestamp_end", "description", "season_type"):
            assert field in result, f"missing field {field!r} in result"
        assert isinstance(result["confidence"], int)


def test_search_confidence_floor(client):
    """
    All returned confidences must be >= SIMILARITY_THRESHOLD * 100 = 7.

    DISCREPANCY: CLAUDE.md spec says 'below 60 = suppress', but the actual
    code uses SIMILARITY_THRESHOLD = 0.07 (7 confidence). This test asserts
    what the code ACTUALLY enforces. See tests/README.md FINDINGS.
    """
    from services.twelvelabs import SIMILARITY_THRESHOLD
    floor = round(SIMILARITY_THRESHOLD * 100)
    r = client.post("/api/search", json={"query": "dress coat jacket", "limit": 50})
    assert r.status_code == 200
    for result in r.json()["results"]:
        assert result["confidence"] >= floor, (
            f"confidence {result['confidence']} is below floor {floor} "
            f"(SIMILARITY_THRESHOLD={SIMILARITY_THRESHOLD})"
        )


def test_search_single_brand_query_synthesis_null(client):
    """
    A query mentioning only one brand (bare brand name) should produce synthesis=None
    because the guard requires ≥2 distinct brands in the top results.
    This test is probabilistic — if top results happen to contain 2+ brands,
    synthesis may be non-null. We assert the field is present and well-typed.
    """
    r = client.post("/api/search", json={"query": "Chanel", "limit": 3})
    assert r.status_code == 200
    body = r.json()
    assert "synthesis" in body
    # synthesis is either None or a string — never an unexpected type
    assert body["synthesis"] is None or isinstance(body["synthesis"], str)


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


def test_search_synthesis_field_always_present(client):
    """synthesis key must always be present in the response, even when None."""
    r = client.post("/api/search", json={"query": "tailored coat", "limit": 5})
    assert r.status_code == 200
    assert "synthesis" in r.json()


def test_search_season_type_on_every_result(client):
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


def test_shows_list_required_fields(client):
    r = client.get("/api/shows")
    for show in r.json()["shows"]:
        for field in ("id", "brand", "season", "year", "show_key", "status"):
            assert field in show, f"missing field {field!r}"


def test_shows_detail_by_key(client):
    shows = client.get("/api/shows").json()["shows"]
    key = shows[0]["show_key"]
    r = client.get(f"/api/shows/{key}")
    assert r.status_code == 200
    assert r.json()["show_key"] == key


def test_shows_detail_not_found(client):
    r = client.get("/api/shows/does-not-exist-xyz")
    assert r.status_code == 404


def test_shows_internal_fields_not_in_default_view(client):
    """Internal ops fields must not leak in the default (client) response."""
    shows = client.get("/api/shows").json()["shows"]
    key = shows[0]["show_key"]
    body = client.get(f"/api/shows/{key}").json()
    for field in ("video_id", "task_id", "health", "sample_moments"):
        assert field not in body, f"internal field {field!r} leaked to client view"


# ── /api/timeline ──────────────────────────────────────────────────────────

def test_timeline_default_shape(client):
    r = client.get("/api/timeline")
    assert r.status_code == 200
    body = r.json()
    for field in ("house", "season_type", "points", "total"):
        assert field in body
    assert isinstance(body["points"], list)
    assert body["total"] == len(body["points"])


def test_timeline_chanel_returns_points(client):
    """Chanel has ingested seasons — must return at least one point."""
    r = client.get("/api/timeline?house=Chanel")
    assert r.status_code == 200
    body = r.json()
    assert body["house"] == "Chanel"
    assert len(body["points"]) > 0, "Chanel timeline returned no points — check DB has Chanel shows"


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


# ── verify_tl_sync (importable core) ───────────────────────────────────────

@pytest.mark.asyncio
async def test_tl_sync_clean():
    """
    Verify the TL index has 0 orphans and 0 ghosts.
    Uses check_sync() — the importable core of scripts/verify_tl_sync.py.

    Skips (not fails) when TWELVE_LABS_API_KEY is unset — same posture as
    the stack-down skip. Missing key is an environment problem, not a code
    regression; a hard fail here would block CI on machines without the key.
    """
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    api_key = os.getenv("TWELVE_LABS_API_KEY")
    if not api_key:
        pytest.skip("TWELVE_LABS_API_KEY not set — skipping TL sync check")

    from verify_tl_sync import check_sync
    orphans, ghosts = await check_sync()
    assert len(orphans) == 0, f"TL orphans found — polluting search: {orphans}"
    assert len(ghosts) == 0, f"DB ghosts found — shows with no TL video: {ghosts}"
