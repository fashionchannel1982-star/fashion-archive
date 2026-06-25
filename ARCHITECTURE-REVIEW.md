# Fashion Archive — Architecture Review

> Honest pre-handoff assessment. Written June 2026 after a full audit pass.
> Target reader: Fengze, and any senior engineer doing a first read of the codebase.

---

## What's Solid

**The core search loop works and is reliable.**
Marengo embeddings + pgvector cosine KNN + logistic confidence calibration is a clean,
well-understood retrieval pipeline. At 3,280 vectors it's sub-20ms for full-table scans
without an HNSW index. The query routing (bare house, metadata hybrid, semantic,
cross-house) handles the real user cases cleanly. The never-empty guarantee holds: the
battery gate validates it on every preflight run with real queries, not mocks.

**Data provenance is well-designed.**
`show_key` (brand + season slug, source excluded) is a stable identity that survives
video replacement. The `provenance` table captures rights metadata. The `events` table
gives a full behavioural audit trail. These are production-quality design decisions.

**Confidence scoring is honest.**
The logistic calibration curve is documented and tunable via env vars. Buckets (Exact /
Strong / Relevant / suppressed) match user expectations. The floor is consistent between
server-side suppression and the frontend "No strong match" banner.

**Test coverage is unusually good for an MVP.**
131 offline unit tests covering all pure functions; a battery gate (10 queries, bare-house
count/confidence/flag assertions, funnel monotonicity); a 3-query eval smoke over validated
golden queries; a Playwright render gate that detects the stale-build failure mode.
These are not curated happy-path tests — they exercise the actual routing logic.

**The team is disciplined about progressive hardening.**
Each session added a concrete, verifiable improvement. The commit history is clean and the
messages are honest about what changed and why. This is the right trajectory.

---

## What Was Fixed This Pass

| Fix | Commit | Impact |
|---|---|---|
| Stale-build trap (`make demo`) | `16d7b0c` | **High** — this caused the blank-page failure repeatedly |
| Frontend `res.ok` check before `res.json()` | `16d7b0c` | Med — 422/500 now shows error banner, not blank grid |
| KNOWN_BRANDS aliases (hermes→Hermès, ysl→SL, etc.) | `5aa231b` | Med — "hermes" was silently returning 0 results |
| Year=2026 data errors for 3 Fall 2025 shows | `5aa231b` | Med — Balenciaga/Celine/Saint Laurent broke year filter |
| Preflight `results` variable shadow (dict in sum()) | `29cec02` | High — preflight was crashing after the 6 checks |
| Render gate structure (NameError on _frontend_up) | `29cec02` | High — render gate never ran |
| `/docs` env-gated with `DISABLE_DOCS=1` | `5aa231b` | Low — prod hygiene |
| `MoodBoardExportRequest.moment_ids` typed | `5aa231b` | Low |
| Claude model harmonised to `claude-sonnet-4-6` | `5aa231b` | Low |

---

## What Remains (Fengze's Attention)

### Decision required

**1. Dior "Fall 2025 Ready-to-Wear" show_date = 2025-01-20 (SUSPICIOUS)**
January 20 is Haute Couture week in Paris, not RTW week. Dior Fall 2025 RTW was shown
late February / early March 2025. This row has 147 moments — it's the largest show in the
corpus. If the date is wrong, year-range queries around 2025 will behave oddly.
→ **Confirm the show date and correct it if needed.**

**2. `next dev` / Turbopack is broken**
`@swc/helpers` module not found. Development workflow uses `next start` (full rebuild
on every TSX change). This is liveable for now but slows iteration.
→ Options: `npm install --save-dev @swc/helpers`, or add `--turbo=false` to next.config.js,
or stay on `next start`. **Pick one before adding more frontend features.**

**3. No authentication on `/api/admin/events`**
This endpoint returns every user query and export action. It has no auth guard.
→ Before any non-local deployment: either add HTTP basic auth (one env var, 10 lines)
or restrict it to `127.0.0.1` in the CORS config.

**4. Rate limiting on `/api/search`**
No rate limiting. Each search calls the Marengo embed API (external HTTP). If two tabs
search simultaneously or a script hammers the endpoint, TL quota burns.
→ `slowapi` (2-line setup) would add per-IP rate limiting. Low effort; worth doing before
any external demo.

### Remaining risks (no decision needed, but track)

**SQL f-strings in `_metadata_hybrid_search`** (severity: medium)
Brand names are matched against a bounded list and escaped with `replace("'","''")`.
Years and limits are int-validated. Not exploitable in the current trust model, but
f-string SQL is brittle as the query grows. Refactoring to parameterised queries
(`sqlalchemy.text()` with `:brand` style params) is the right path — scoped to
`_metadata_hybrid_search`, roughly 30 lines.

**Duplicate synthesis path** (severity: low)
`/api/search` calls `synthesize_results()` inline (~500ms penalty), AND the frontend
fires a separate `/api/synthesize` request afterwards. This means every search with ≥2
results does two Claude calls. The in-band call in `search()` should be removed; the
frontend already handles the async synthesis path correctly.

**Single-file frontend** (severity: low now, medium later)
`pages/index.tsx` is 1,537 lines. The ResultCard, SuggestionChips, BookmarkPanel, and
PlaybackModal components are all inline. This is fine until you add a second screen (show
detail, timeline view). Extract components before that point; don't wait.

**`unaccent` PostgreSQL extension not installed**
Brand alias matching normalises "hermes" → "Hermès" in Python before the SQL query.
This works but means new spellings need a code change rather than a DB config. Installing
`CREATE EXTENSION unaccent;` once would make accent-normalisation automatic.

**Chanel-only code_tags** (timeline feature)
The `code_tags` column and the Chanel house-code taxonomy are hardcoded to Chanel.
The `/api/timeline` endpoint only makes sense for Chanel AW-RTW. This is fine for MVP
but blocks the timeline feature for other houses.

---

## Overall Production-Readiness Verdict

**Not ready for external access, ready for internal demo and further development.**

The core search product is reliable and test-covered. The main blockers for any external
use are: no authentication, no rate limiting, and `/docs`+`/redoc` exposed. All three are
<2 hours of work. The stale-build trap (which caused several blank-page incidents this
session) is now structurally prevented by `make demo`.

The architecture is sound for the stated goal (semantic search over runway video, internal
single user). It will need: component extraction before adding more UI screens; parameterised
SQL before open-sourcing or deploying behind auth; and a decision on `next dev` before the
frontend grows further.

**What a senior reviewer would call out on first read:**
1. The f-string SQL in `_metadata_hybrid_search` — looks like an injection risk until you
   read the constraint that brand always comes from a bounded list.
2. `main.py` is 1,100 lines and does routing, search orchestration, export, timeline, admin,
   and playback. This should be split into routers eventually, but it's readable today.
3. The sync Claude client wrapped in `asyncio.to_thread` — works, but the right pattern
   for async FastAPI is `anthropic.AsyncAnthropic()`, which is already used in the brief
   endpoint. Synthesis should use it too.
4. No structured logging — `logger.warning(f"...")` with f-strings loses log parsing.
   Worth a 30-minute pass with `logger.warning("...", extra={...})` before adding Datadog.

None of these are fires. They're the kind of things you fix in the second sprint, not before
the first demo.
