# Fashion Archive — Test Suite

## Loop gate command (runs every agent iteration)

```
cd backend && pytest -m unit
```

Must pass with **zero running services** — no DB, no Twelve Labs, no Anthropic. Runs in <5s.

---

## Tiers

| Marker | Command | Requires | When to run |
|--------|---------|----------|-------------|
| `unit` | `pytest -m unit` | nothing | Every build iteration (agent gate) |
| `integration` | `pytest -m integration` | live backend at localhost:8000 | Before merge / after deploy |
| (both) | `pytest` | backend up or tests auto-skip | Full run |

Integration tests **auto-skip** when the backend is not reachable — `pytest -m unit` never fails due to missing services.

---

## Setup

```bash
pip install -r requirements-dev.txt
```

## Running

```bash
# Unit only (offline, always safe):
cd backend && pytest -m unit -v

# Integration (start backend first):
cd backend && uvicorn main:app --port 8000 &
cd backend && pytest -m integration -v

# Everything:
cd backend && pytest -v
```

---

## What's covered

### Unit (`-m unit`)

| Module | What's tested |
|--------|--------------|
| `services/database.py` | `make_show_key`: normalisation, accents, apostrophes, whitespace, case collapse, **source does not affect key** |
| `services/structured_match.py` | `parse_query_attributes`: "red dress" → colour+garment; empty query → empty lists |
| `services/structured_match.py` | `attribute_boost`: 0 when enriched is empty/null; positive on match; only adds, never subtracts; capped at 0.20 |
| `services/claude.py` | `synthesize_results` guard: <2 distinct brands → None **without model call** (monkeypatched); NONE escape hatch; empty output → None |
| `services/show_view.py` | `client_safe_metadata`: `video_id`, `task_id`, `status`, `health`, `source` absent; public fields present; `models` slot always present |
| Confidence contract | Integer type, 0-100 range, band thresholds |

### Integration (`-m integration`)

| Endpoint / behaviour | What's checked |
|---------------------|---------------|
| `GET /health` | 200, `status: ok` |
| `POST /api/search` multi-word concept | 200, required fields (`brand`, `confidence`, `season_type`, provenance), `total == len(results)` |
| `POST /api/search` confidence floor | All confidences ≥ actual threshold (see FINDINGS) |
| `POST /api/search` single-brand query | `synthesis` key always present; type is None or str |
| `POST /api/search` validation | Empty/blank/too-long/out-of-range inputs → 422 |
| `GET /api/timeline?house=Chanel` | 200, `points` non-empty |
| `GET /api/timeline?code=tweed` | `cross_year_echo`, `rep_moment`, `codes` present |
| `GET /api/timeline?house=NotAHouse` | 200, empty points (not 404) |
| `GET /api/shows` | List non-empty, required fields present |
| `GET /api/shows/{key}` | 200, correct key; 404 for unknown |
| Internal field exposure | `video_id`, `task_id`, `health`, `sample_moments` absent from default show view |
| TL sync | `check_sync()` returns 0 orphans, 0 ghosts |

---

## FINDINGS

### Confidence suppression threshold discrepancy

**CLAUDE.md spec**: "below 60 = suppress from results"  
**Actual code** (`services/twelvelabs.py`): `SIMILARITY_THRESHOLD = 0.07` → suppresses below confidence **7**  
**Prompt spec**: "confidence never below the 30 suppression floor"

Three different values. The integration test (`test_confidence_floor`) asserts against the actual code value (7). Results with confidence 7–59 are currently returned by the API. **Decision needed: which threshold is correct?**

---

## Adding a test

- **Pure logic with no services** → add to `tests/test_unit.py` with `pytestmark = pytest.mark.unit`
- **Needs the live stack** → add to `tests/test_api.py` (already marked integration)
- Never import DB/TL/Anthropic from a unit test — use `unittest.mock.patch` or `MagicMock`
