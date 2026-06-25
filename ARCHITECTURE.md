# Fashion Archive — Architecture

> Last updated: June 2026.  Reflects the real system, not the original CLAUDE.md MVP spec.

---

## 1. System Overview

Fashion Archive is a conversational semantic search platform over runway video footage.
A user types a natural-language query and gets back ranked video moments from ingested
shows, each with a thumbnail, brand/season provenance, AI-generated description, and a
calibrated confidence score.

**Current corpus:** 42 shows, 18 houses, 1993–2026, 3,280 moments, 100% embedding coverage.

---

## 2. Components

```
┌──────────────────────────────────────────────────────────┐
│  Browser (Next.js 14 / React)                            │
│  Single page — search, results grid, bookmarks,          │
│  mood board, show briefs, timeline, video playback       │
└────────────────┬─────────────────────────────────────────┘
                 │  HTTP / JSON  (port 3000 → 8000)
┌────────────────▼─────────────────────────────────────────┐
│  FastAPI  (Python 3.9, uvicorn)           port 8000      │
│  main.py + routers/ingest.py                             │
│  services/: database, twelvelabs, structured_match,      │
│             confidence, claude, show_view, access_control│
└──────┬──────────────┬──────────────┬─────────────────────┘
       │              │              │
  AsyncPG        Twelve Labs     Anthropic
  (asyncpg)      REST API        Claude API
       │
┌──────▼──────────────────────────────────────────────────┐
│  PostgreSQL 15 + pgvector extension                     │
│  Tables: shows, moments (VECTOR 512), events,           │
│          provenance                                     │
└─────────────────────────────────────────────────────────┘
                         │
                  S3 / localhost/static
                  (thumbnails, JPEG)
```

### Backend
- **FastAPI** application in `backend/main.py` (~1,080 lines).
- **Async SQLAlchemy** with `asyncpg` driver; connection pool shared across requests.
- **Lazy imports** inside route handlers insulate startup from per-module import errors.
- **Routers**: `routers/ingest.py` handles all four ingest paths (YouTube, WeTransfer,
  local file, partner). Search, export, timeline, admin are inline in `main.py`.

### Database
- `shows` — one row per ingested show; fields: brand, season, year, show_date,
  season_type, creative_director, source, video_id (Twelve Labs), show_key (stable slug).
- `moments` — one row per extracted look/clip; fields: timestamp_start/end,
  description, thumbnail_url, `embedding VECTOR(512)`, enriched_data (JSON),
  code_tags (JSON, Chanel-specific), look_number.
- `events` — fire-and-forget audit log for every search, click, export, play.
- `provenance` — rights metadata per show; usage_rights, embargo, access_tier.

**Note:** CLAUDE.md specifies VECTOR(1024); the actual DB and ingest pipeline use 512.
This is not a bug — it reflects the Marengo embedding dimension used at ingest time.

### Twelve Labs (Marengo 3.0)
- Ingest: video is uploaded and indexed; returns a `video_id`.
- Embeddings: `embed_text(query)` embeds a text query into 512-d space.
- pgvector cosine search is the primary retrieval path; Twelve Labs clip search
  is the fallback when embeddings are absent.

### Claude API
- Model: `claude-sonnet-4-6` (used consistently throughout; `claude-sonnet-4-20250514`
  was an earlier alias, now harmonised).
- Roles: look enrichment (structured JSON from raw Pegasus text), result synthesis
  (one grounded sentence across ≥2 distinct brands), show briefs.
- All calls are synchronous (wrapped in `asyncio.to_thread` for synthesis) because
  the ingest path is an offline batch script.

### Frontend
- **Next.js 14 Pages Router** (`pages/index.tsx` — 1,537 lines; all UI in one file).
- **Production-only** (`next start`). `next dev` / Turbopack is broken due to a missing
  `@swc/helpers` dependency and is not used.
- **API_URL** defaults to `http://localhost:8000` (env var `NEXT_PUBLIC_API_URL`).
- No server-side rendering for search — all data fetched client-side after hydration.

---

## 3. Query Data-Flow

```
User types query
      │
      ▼
parse_metadata_filters()               [structured_match.py]
  ├─ strip meta-phrases ("across houses", "vs") → cross_house flag
  ├─ strip era tokens ("Lagerfeld era") → brand_lock + year range
  ├─ parse compound tokens (FW25, SS00) → season_code + year
  ├─ detect brand (KNOWN_BRANDS + _BRAND_ALIASES → DB-canonical spelling)
  ├─ detect decade ("90s" → year_min/year_max ±2)
  └─ residual = query minus structural tokens

parse_query_attributes()               [structured_match.py]
  └─ extract colours, garments, silhouettes, accessories for post-retrieval boost

Route decision (main.py search()):
  ├─ is_bare_brand?  → _metadata_hybrid_search() [no relaxation, no soft fallback]
  ├─ has_structural? → _metadata_hybrid_search() [+ progressive relaxation]
  ├─ cross_house?    → semantic_search(residual, cross_house=True)
  └─ else            → semantic_search(full query)

_metadata_hybrid_search():
  ├─ bare_brand path: ROW_NUMBER OVER (PARTITION BY show_id) + round-robin → diverse spread
  ├─ concept+filter path: embed residual → pgvector KNN within filtered subset
  └─ pure metadata path (no residual): ORDER BY timestamp_start, conf=97

semantic_search():
  ├─ embed_text(visual_query) via Marengo API
  └─ pgvector cosine KNN with per-show (3) and per-brand (4) diversity caps

Post-retrieval (main.py):
  ├─ confidence calibration: logistic curve (k=40, x0=0.065) → integer 0–100
  ├─ attribute_boost(): +0.08 per matched colour/garment, +0.05 silhouette, +0.06 accessory
  ├─ floor: SEARCH_CONFIDENCE_FLOOR (default 60) — discard pure-semantic below floor
  ├─ soft_results fallback: hybrid_filtered sub-floor saved; surfaced if nothing passes floor
  ├─ is_bare_brand: skips soft fallback; all results carry is_bare_house=True
  └─ synthesis: synthesize_results() if ≥2 results and ≥2 distinct brands

Response → browser
  ├─ results[]: moment_id, brand, season, timestamp, description, thumbnail_url,
  │             confidence (int), match_type, is_bare_house, enriched{}
  └─ synthesis: one grounded sentence (or null)
```

---

## 4. Data Model (actual, not CLAUDE.md spec)

```sql
shows (
  id                UUID PK,
  brand             TEXT NOT NULL,          -- 'Chanel', 'Dior', etc.
  season            TEXT NOT NULL,          -- 'Fall 2025 Ready-to-Wear'
  year              INTEGER NOT NULL,       -- season year (e.g. 2025 for Fall 2025)
  season_type       TEXT,                  -- 'AW-RTW', 'SS-RTW', 'Couture'
  show_date         TIMESTAMPTZ,           -- actual show date (may differ from year)
  creative_director TEXT,
  source            TEXT,                  -- 'fc_master' | 'youtube_mvp'
  show_key          TEXT UNIQUE,           -- slugified brand__season (stable identity)
  video_id          TEXT,                  -- Twelve Labs video_id
  status            TEXT DEFAULT 'queued', -- 'ready' | 'processing' | 'error'
  summary           TEXT,                  -- Claude show brief (cached)
  ...
)

moments (
  id                UUID PK,
  show_id           UUID FK → shows,
  look_number       INTEGER,
  timestamp_start   FLOAT,
  timestamp_end     FLOAT,
  description       TEXT,                  -- Claude-enriched one-sentence description
  thumbnail_url     TEXT,                  -- /static/thumbnails/{id}.jpg
  enriched_data     JSON,                  -- {description, garments, colours, silhouette,
                                           --  key_pieces, search_tags}
  embedding         VECTOR(512),           -- Marengo 3.0 embedding
  code_tags         JSON,                  -- Chanel house codes {tweed, pearls, ...}
  ...
)

events (
  event_type TEXT, query_text TEXT, moment_id TEXT, event_meta JSON, created_at TIMESTAMPTZ
)

provenance (
  show_id UUID FK, source_name TEXT, source_type TEXT, access_tier TEXT, usage_rights TEXT,
  embargo_until TIMESTAMPTZ, ...
)
```

---

## 5. Run / Deploy Model

**Development (this session):**
```
make demo          # kill ports 3000/8000, clear .next, build, start both, health-check
make dev           # same + backend --reload
make stop          # kill both
make preflight     # run all gates (unit tests, tsc, build, battery, eval smoke, render)
make unit          # offline unit tests only (~2s)
```

**IMPORTANT — stale-chunk invariant:**
`next start` loads its internal route manifest at startup. A new `npm run build` after
that replaces chunks on disk but the running process returns 404 for all JS bundles →
React never hydrates → blank page, no error. `make demo` enforces build→start atomically
(deletes .next first). **Never run `next start` without a preceding `npm run build`.**

**Environment:**
```
backend/.env          # TWELVE_LABS_API_KEY, TWELVE_LABS_INDEX_ID, ANTHROPIC_API_KEY,
                      # DATABASE_URL, CORS_ORIGINS, DISABLE_DOCS, SENTRY_DSN (optional)
frontend/.env.local   # NEXT_PUBLIC_API_URL (default: http://localhost:8000)
```

**Migrations:** Alembic under `backend/alembic/`. Run `alembic upgrade head` after schema changes.

---

## 6. Architecture Assessment

### Strengths
- **Embedding path is solid.** pgvector cosine KNN over 3,280 vectors is sub-20ms; no
  separate vector DB required at this scale; exact KNN (no HNSW approximation).
- **Progressive narrowing holds.** `chanel` ≥ `chanel 1993` ≥ `chanel 1993 red` at
  limit=50 is verified by the funnel gate; the never-empty guarantee is enforced by
  the soft_results fallback.
- **Provenance and data integrity.** show_key is a stable identity slug (brand+season,
  source excluded) that survives video replacement. Events table provides a full audit trail.
- **Calibrated confidence.** Logistic curve maps raw cosine to a meaningful 0–100 scale
  with display buckets; floor prevents low-confidence results from appearing.
- **Test coverage.** 131 unit tests covering all pure functions; battery gate; eval harness
  on validated queries; render gate for API→UI consistency.

### Architectural Risks

| Risk | Severity | Notes |
|---|---|---|
| Single-file frontend (1,537 lines) | **Med** | All UI, state, types in `pages/index.tsx`. Manageable now; will need splitting before adding screens. |
| SQL built by f-string in `_metadata_hybrid_search` | **Med** | Brand comes from a bounded known list + `replace("'","''")` escape; year/limit are int-validated. Not exploitable in practice but antipattern. Parameterised SQL is the fix. |
| No authentication on any endpoint | **Med** | Internal MVP only. `/api/admin/events` is exposed. Must gate before any external access. |
| No rate limiting on `/api/search` | **Med** | Each search embeds with Twelve Labs (external HTTP call). Concurrent search spike could exhaust the Marengo API rate limit. |
| `next dev` / Turbopack broken | **Low** | `@swc/helpers` module not found. Development uses `next start` (production mode); hot-reload on TSX changes requires a full rebuild. |
| `/docs` and `/redoc` exposed in dev | **Low** | `DISABLE_DOCS=1` env var suppresses them. Must be set in any non-local environment. |
| Dior "Fall 2025 RTW" show_date=2025-01-20 suspect | **Low** | January 20 is Couture week, not RTW week. Either a data entry error or an unusual schedule. Needs Fengze confirmation before correcting. |
| Embedding dimension mismatch in CLAUDE.md | **Info** | CLAUDE.md says VECTOR(1024); actual corpus and DB use VECTOR(512). Not a functional bug but a stale spec. |
| Claude synthesis fires on every ≥2-result search | **Low** | In-band API call adds ~500ms to the search response for the synthesis path. Frontend already fires it async via /api/synthesize, making the in-band call in search() redundant. |
