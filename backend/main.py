"""
Fashion Archive — Backend MVP v2
FastAPI + PostgreSQL + Twelve Labs + Claude

Endpoints:
  GET  /health
  GET  /api/shows
  POST /api/ingest/youtube
  POST /api/ingest/wetransfer
  POST /api/ingest/file
  POST /api/ingest/partner
  GET  /api/ingest/status/{task_id}
  POST /api/search
  POST /api/export
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import asyncio
import time
import os
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

from services.confidence import calibrate, confidence_floor
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Rate limiter (slowapi) ─────────────────────────────────────────────────────
# Applied to /api/search to protect the Marengo embed API from concurrent spikes.
# SEARCH_RATE_LIMIT env var overrides the default (e.g. "20/minute").
_search_rate_limit = os.getenv("SEARCH_RATE_LIMIT", "30/minute")
limiter = Limiter(key_func=get_remote_address, default_limits=[])

# ── Admin auth dependency ──────────────────────────────────────────────────────
# /api/admin/events requires a bearer token matching ADMIN_TOKEN env var.
# If ADMIN_TOKEN is unset the endpoint is blocked entirely (fail-closed).
_admin_token = os.getenv("ADMIN_TOKEN", "")
_bearer_scheme = HTTPBearer(auto_error=True)

def _require_admin(credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme)) -> None:
    if not _admin_token or credentials.credentials != _admin_token:
        raise HTTPException(status_code=401, detail="Unauthorized")

# Sentry — no-op when SENTRY_DSN is unset; set it in prod to enable error tracking.
_sentry_dsn = os.getenv("SENTRY_DSN", "")
if _sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
    logger.info("Sentry error tracking enabled")

# Disable /docs and /redoc in production (set DISABLE_DOCS=1 in prod env).
# In dev they're useful; in a shared/deployed environment they leak schema.
_disable_docs = os.getenv("DISABLE_DOCS", "0") == "1"
app = FastAPI(
    title="Fashion Archive API",
    version="2.0.0",
    docs_url=None if _disable_docs else "/docs",
    redoc_url=None if _disable_docs else "/redoc",
    openapi_url=None if _disable_docs else "/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_cors_origins = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────
# ROUTERS
# ─────────────────────────────────────────

from routers.ingest import router as ingest_router
from services.twelvelabs import _is_valid_description
app.include_router(ingest_router, prefix="/api/ingest", tags=["ingest"])

# ─────────────────────────────────────────
# STATIC FILES — thumbnails
# ─────────────────────────────────────────

_thumbnails_dir = os.path.join(os.path.dirname(__file__), "static", "thumbnails")
os.makedirs(_thumbnails_dir, exist_ok=True)
app.mount("/static/thumbnails", StaticFiles(directory=_thumbnails_dir), name="thumbnails")


# ─────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


# ─────────────────────────────────────────
# SUGGEST  (autocomplete)
# ─────────────────────────────────────────

# In-memory suggestion index built at startup (or on first call).
# Rebuilt automatically when the module reloads.
_suggest_index: Optional[list] = None

def _build_suggest_index() -> list:
    """
    Build a flat list of suggestion strings from the known brands and their
    seasons in the DB.  Called once; result is cached in _suggest_index.
    Format: ["Chanel", "Chanel AW25/26", "Chanel SS25", ...]
    DB is read synchronously here — this runs at startup, not per-request.
    """
    import asyncio as _asyncio
    from services.twelvelabs import KNOWN_BRANDS, _BRAND_ALIASES
    from services.database import AsyncSessionLocal
    from sqlalchemy import text as _text

    suggestions: list = []

    # All canonical brand names
    suggestions.extend(KNOWN_BRANDS)

    # Fetch brand+season combinations from DB
    async def _fetch():
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(_text(
                "SELECT brand, season, year, season_type FROM shows ORDER BY brand, year DESC"
            ))).fetchall()
        return rows

    try:
        loop = _asyncio.new_event_loop()
        rows = loop.run_until_complete(_fetch())
        loop.close()
    except Exception:
        rows = []

    for r in rows:
        # Vogue format: "Chanel Fall 2025"
        suggestions.append(f"{r.brand} {r.season}")
        # European compact: "Chanel AW25/26" or "Chanel SS25"
        y = r.year
        if r.season_type in ("AW-RTW", "Couture") or (r.season_type and "AW" in r.season_type):
            eu = f"AW{str(y)[2:]}/{str(y+1)[2:]}"
        elif r.season_type == "SS-RTW" or (r.season_type and "SS" in r.season_type):
            eu = f"SS{str(y)[2:]}"
        else:
            eu = None
        if eu:
            suggestions.append(f"{r.brand} {eu}")

    # Deduplicate preserving order
    seen: set = set()
    unique: list = []
    for s in suggestions:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            unique.append(s)

    return unique


@app.get("/api/suggest")
async def suggest(q: str = "", limit: int = 8):
    """
    Autocomplete suggestions for the search input.
    Returns up to `limit` suggestions ranked by relevance to the partial query `q`.

    Strategy (Google-style):
      1. Exact prefix matches first (case-insensitive)
      2. Any-position substring matches next
      3. Fuzzy matches last (typo correction via rapidfuzz)
    """
    global _suggest_index
    if _suggest_index is None:
        _suggest_index = _build_suggest_index()

    if not q or not q.strip():
        # Empty query → return all brand names (discovery mode)
        from services.twelvelabs import KNOWN_BRANDS
        return {"suggestions": sorted(KNOWN_BRANDS)[:limit]}

    q_lower = q.strip().lower()

    prefix: list = []
    substring: list = []
    for s in _suggest_index:
        sl = s.lower()
        if sl.startswith(q_lower):
            prefix.append(s)
        elif q_lower in sl:
            substring.append(s)

    results = prefix + substring

    # Fuzzy fallback if not enough results (typo correction)
    if len(results) < limit:
        try:
            from rapidfuzz import process as fuzz, fuzz as fuzz_ratio
            fuzzy_matches = fuzz.extract(
                q_lower,
                [s.lower() for s in _suggest_index],
                scorer=fuzz_ratio.WRatio,
                limit=limit * 2,
                score_cutoff=70,
            )
            existing_lower = {r.lower() for r in results}
            for match_str, score, idx in fuzzy_matches:
                original = _suggest_index[idx]
                if original.lower() not in existing_lower:
                    results.append(original)
                    existing_lower.add(original.lower())
        except Exception:
            pass

    return {"suggestions": results[:limit]}


# ─────────────────────────────────────────
# SHOWS
# ─────────────────────────────────────────

@app.get("/api/shows")
async def get_shows():
    """Returns all ingested shows with moment counts."""
    from services.database import AsyncSessionLocal, list_shows
    async with AsyncSessionLocal() as session:
        shows = await list_shows(session)
        return {
            "shows": [
                {
                    "id": s.id,
                    "show_key": s.show_key,
                    "brand": s.brand,
                    "season": s.season,
                    "season_type": s.season_type,
                    "year": s.year,
                    "creative_director": s.creative_director,
                    "show_date": s.show_date.isoformat() if s.show_date else None,
                    "source": s.source,
                    "status": s.status,
                    "moment_count": s.looks_count,
                    "summary": s.summary,
                }
                for s in shows
            ]
        }


@app.get("/api/shows/{ident}")
async def get_show_detail(ident: str, view: str = "client"):
    """
    Show metadata endpoint.
    Resolves by show.id first, then show_key.

    Default (?view=client): curated public subset — no operational fields.
      Fields: brand, season, season_type, year, creative_director, models,
              show_date, summary, show_key.

    ?view=internal: full ops metadata including video_id, task_id, health,
      sample_moments. For tooling/scripts only — never expose to the frontend.

    Security posture is fail-closed: the default NEVER includes video_id,
    task_id, health, or sample_moments.
    """
    from services.database import AsyncSessionLocal, Show, Moment
    from services.show_view import internal_metadata, client_safe_metadata
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        # Resolve by id, then show_key
        show = (await session.execute(
            select(Show).where(Show.id == ident)
        )).scalar_one_or_none()
        if show is None:
            show = (await session.execute(
                select(Show).where(Show.show_key == ident)
            )).scalar_one_or_none()
        if show is None:
            raise HTTPException(status_code=404, detail=f"Show not found: {ident!r}")

        if view == "internal":
            # Internal view: load moments and provenance
            moments = (await session.execute(
                select(Moment).where(Moment.show_id == show.id).order_by(Moment.look_number)
            )).scalars().all()

            from sqlalchemy.orm import selectinload
            show_with_prov = (await session.execute(
                select(Show).where(Show.id == show.id).options(selectinload(Show.provenance))
            )).scalar_one()
            return internal_metadata(show_with_prov, moments)

        # Default: client-safe projection — no operational fields
        return client_safe_metadata(show)


# ─────────────────────────────────────────
# SEARCH
# ─────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def query_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Query cannot be blank")
        return v


# ─────────────────────────────────────────
# HYBRID METADATA SEARCH
# ─────────────────────────────────────────

# Confidence score assigned to pure metadata matches (no cosine component).
# 97 → "Exact" bucket (≥90); honest because the filter is exact, not approximate.
_METADATA_CONFIDENCE = 97


async def _metadata_hybrid_search(
    meta: dict,
    limit: int,
) -> list:
    """
    Hybrid retrieval for queries containing structural metadata tokens
    (year, brand, season_code) plus an optional residual concept.

    Pure metadata query  → SQL filter → ORDER BY timestamp_start, confidence=97
    Mixed (filter+concept) → SQL filter → ORDER BY embedding cosine of residual

    Returns the same item shape as twelvelabs.semantic_search so the caller
    can reuse the existing result-building loop unchanged.
    """
    from services.database import AsyncSessionLocal, Moment, Show
    from sqlalchemy import select, text as sql_text
    from services import twelvelabs as tl_svc
    from services.confidence import calibrate as cal

    year = meta["year"]
    year_min = meta.get("year_min")
    year_max = meta.get("year_max")
    brand = meta["brand"]
    season_code = meta["season_code"]
    residual = meta["residual"].strip()

    # Any year constraint (exact, range, or era) → trust the filter; override confidence
    has_year_filter = bool(year or year_min or year_max)

    # Build WHERE clauses
    clauses: list = ["m.embedding IS NOT NULL"]
    if brand:
        safe_brand = brand.replace("'", "''")
        clauses.append(f"s.brand ILIKE '{safe_brand}'")
    if year:
        clauses.append(f"s.year = {int(year)}")
    if year_min and not year:
        clauses.append(f"s.year >= {int(year_min)}")
    if year_max and not year:
        clauses.append(f"s.year <= {int(year_max)}")
    if season_code == "FW":
        clauses.append(
            "(s.season_type ILIKE '%AW%' OR s.season ILIKE '%fall%' "
            "OR s.season ILIKE '%autumn%' OR s.season ILIKE '%winter%' "
            "OR s.season ILIKE '%AW%' OR s.season ILIKE '%FW%')"
        )
    elif season_code == "SS":
        clauses.append(
            "(s.season_type ILIKE '%SS%' OR s.season ILIKE '%spring%' "
            "OR s.season ILIKE '%summer%' OR s.season ILIKE '%SS%')"
        )
    elif season_code == "Couture":
        clauses.append("s.season ILIKE '%couture%'")

    where = " AND ".join(clauses)

    # Bare brand: brand-only, no year/season/concept → apply show diversity below
    is_bare_brand = bool(
        brand and not residual and not has_year_filter and not season_code
    )

    query_vec = None
    vec_str = ""
    if residual:
        # Mixed query: embed residual concept, KNN within filtered subset
        query_vec = await tl_svc.embed_text(residual)
        if query_vec:
            vec_str = "[" + ",".join(f"{v:.8f}" for v in query_vec) + "]"
        else:
            residual = ""  # embed failed — fall back to timestamp order

    if residual and query_vec:
        order_by = f"m.embedding <=> '{vec_str}'::vector"
        sql = f"""
            SELECT m.id, m.show_id, m.timestamp_start, m.timestamp_end,
                   m.description, m.thumbnail_url,
                   m.embedding <=> '{vec_str}'::vector AS distance
            FROM moments m
            JOIN shows s ON s.id = m.show_id
            WHERE {where}
            ORDER BY {order_by}
            LIMIT {limit * 3}
        """
        use_cosine = True
    elif is_bare_brand:
        # ROW_NUMBER per show gives at most 2 moments per show, then Python round-robin.
        # This avoids the UUID-ordering problem of ORDER BY show_id + LIMIT.
        # rn cap: enough per show so that even one-show brands (e.g. Gucci 2 shows)
        # return the full requested limit; ceil(limit / min_expected_shows ≈ 1) = limit.
        # Capped at limit so we never pull an entire show unnecessarily.
        _rn_cap = limit
        sql = f"""
            SELECT id, show_id, timestamp_start, timestamp_end, description,
                   thumbnail_url, NULL AS distance
            FROM (
                SELECT m.id, m.show_id, m.timestamp_start, m.timestamp_end,
                       m.description, m.thumbnail_url,
                       ROW_NUMBER() OVER (
                           PARTITION BY m.show_id ORDER BY m.timestamp_start
                       ) AS rn
                FROM moments m
                JOIN shows s ON s.id = m.show_id
                WHERE {where}
            ) _ranked
            WHERE rn <= {_rn_cap}
        """
        use_cosine = False
    else:
        sql = f"""
            SELECT m.id, m.show_id, m.timestamp_start, m.timestamp_end,
                   m.description, m.thumbnail_url,
                   NULL AS distance
            FROM moments m
            JOIN shows s ON s.id = m.show_id
            WHERE {where}
            ORDER BY m.timestamp_start
            LIMIT {limit}
        """
        use_cosine = False

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(sql_text(sql))).fetchall()

    if not rows:
        return []

    # Bare brand: round-robin across shows so the house is browseable, not monotone
    if is_bare_brand and rows:
        show_buckets: dict = {}
        for r in rows:
            key = str(r.show_id)
            if key not in show_buckets:
                show_buckets[key] = []
            show_buckets[key].append(r)
        diverse: list = []
        show_lists = list(show_buckets.values())
        i = 0
        while len(diverse) < limit:
            added_this_pass = False
            for sl in show_lists:
                if i < len(sl):
                    diverse.append(sl[i])
                    added_this_pass = True
                    if len(diverse) >= limit:
                        break
            if not added_this_pass:
                break
            i += 1
        rows = diverse

    # Load shows
    show_ids = list({r.show_id for r in rows})
    async with AsyncSessionLocal() as session:
        shows_map = {
            s.id: s
            for s in (
                await session.execute(select(Show).where(Show.id.in_(show_ids)))
            ).scalars().all()
        }

    results: list = []
    for r in rows:
        show = shows_map.get(r.show_id)
        if use_cosine:
            similarity = 1.0 - float(r.distance)
            score = round(similarity, 4)
            # Hybrid: use real KNN confidence so the concept ranks within the filtered set.
            # Floor is NOT applied for filtered hybrid results — the metadata filter already
            # constrains relevance; the concept only ranks, it should not discard everything.
            confidence = cal(similarity)
            match_type = "hybrid_filtered"
        else:
            score = 1.0
            confidence = _METADATA_CONFIDENCE
            match_type = "metadata"
        results.append({
            "video_id": show.video_id if show else None,
            "score": score,
            "start": r.timestamp_start,
            "end": r.timestamp_end,
            "thumbnail_url": r.thumbnail_url,
            "metadata": {},
            "_moment_id": str(r.id),
            "_show_id": str(r.show_id),
            "_confidence_override": confidence,
            "_match_type": match_type,
        })
        if len(results) >= limit:
            break

    return results


@app.post("/api/search")
@limiter.limit(_search_rate_limit)
async def search(request: Request, req: SearchRequest, bg: BackgroundTasks):
    """Semantic search across ingested moments via Twelve Labs."""

    start = time.time()

    from services import twelvelabs
    from services.database import AsyncSessionLocal, get_show
    from sqlalchemy import select, text
    from services.database import Moment, Show

    from services.structured_match import parse_query_attributes, attribute_boost, parse_metadata_filters
    from services.twelvelabs import KNOWN_BRANDS

    attrs = parse_query_attributes(req.query)
    has_attrs = any(attrs.values())

    # Detect structural metadata tokens (year / brand / season)
    meta = parse_metadata_filters(req.query, known_brands=list(KNOWN_BRANDS))
    has_structural = bool(
        meta["year"] or meta.get("year_min") or meta.get("year_max")
        or meta["season_code"] or meta["brand"]
    )
    cross_house = meta.get("cross_house", False)

    # Bare house: recognised brand with no year/season/concept qualifier.
    # Hard-filter path — never relaxes, never shows weak-match banner.
    is_bare_brand = bool(
        meta["brand"]
        and not meta["residual"].strip()
        and not meta["year"]
        and not meta.get("year_min")
        and not meta.get("year_max")
        and not meta["season_code"]
    )

    # Widen the candidate pool when structured attributes are present so re-ranking
    # has material to promote; otherwise use the requested limit directly.
    _MAX_CANDIDATE = 150
    candidate_limit = min(req.limit * 3, _MAX_CANDIDATE) if has_attrs else req.limit

    # Route:
    #   bare house  → hard metadata filter; no relaxation; no soft fallback
    #   other structural tokens (year/brand/season) → metadata hybrid + relaxation
    #   cross-house phrase ("across houses", "vs", etc.) → semantic on stripped concept
    #   else → normal semantic on full query
    if is_bare_brand:
        tl_results = await _metadata_hybrid_search(meta, limit=candidate_limit)
        # Never relax for a bare house — if the brand filter returns nothing,
        # that brand has no moments in the archive (return empty, not a fallback).
    elif has_structural:
        tl_results = await _metadata_hybrid_search(meta, limit=candidate_limit)
        # Progressive relaxation: if concept+filter combo is too tight, retry without
        # the concept (pure metadata), then without season, keeping brand/year as last resort.
        if not tl_results and meta["residual"].strip():
            relaxed = dict(meta, residual="")
            tl_results = await _metadata_hybrid_search(relaxed, limit=candidate_limit)
        if not tl_results and meta["season_code"]:
            relaxed = dict(meta, residual="", season_code=None)
            tl_results = await _metadata_hybrid_search(relaxed, limit=candidate_limit)
    elif cross_house:
        embed_query = meta["residual"].strip() or req.query
        tl_results = await twelvelabs.semantic_search(
            embed_query, limit=candidate_limit, cross_house=True
        )
    else:
        tl_results = await twelvelabs.semantic_search(req.query, limit=candidate_limit)

    if not tl_results:
        from services.database import log_event
        bg.add_task(log_event, "search", query_text=req.query, metadata={"result_count": 0})
        return {
            "query": req.query,
            "results": [],
            "total": 0,
            "processing_time_ms": round((time.time() - start) * 1000),
            "synthesis": None,
        }

    results = []
    # hybrid_filtered items that fail the floor are kept as fallback: if the entire
    # filtered set falls below the floor (e.g. "90s minimalism" where the era is
    # constrained but no moments score ≥60 for a style concept), these are returned
    # sorted by confidence rather than returning empty.
    soft_results: list = []

    async with AsyncSessionLocal() as session:
        for item in tl_results:
            score = item.get("score", 0.0)
            # Metadata matches carry a pre-computed confidence (exact filter, no cosine)
            confidence = item.get("_confidence_override") or calibrate(score)

            # pgvector path returns _moment_id directly; TL path uses video_id + timestamp
            moment_id = item.get("_moment_id")
            if moment_id:
                stmt = (
                    select(Moment, Show)
                    .join(Show, Show.id == Moment.show_id)
                    .where(Moment.id == moment_id)
                )
            else:
                video_id = item.get("video_id")
                stmt = (
                    select(Moment, Show)
                    .join(Show, Show.id == Moment.show_id)
                    .where(Show.video_id == video_id)
                    .where(Moment.timestamp_start <= item.get("start", 0) + 5)
                    .where(Moment.timestamp_end >= item.get("start", 0) - 5)
                    .limit(1)
                )
            row = (await session.execute(stmt)).first()

            if row:
                moment, show = row
                enriched = moment.enriched_data or {}
                description = enriched.get("description") or moment.description or ""

                # Skip results whose description is a hedge, refusal, or placeholder
                if not _is_valid_description(description):
                    continue

                # Compute attribute boost before the floor check: a strong
                # structured-attribute match (e.g. embellishment tags, gold colour)
                # can rescue a moment whose raw cosine sits just below the floor.
                boost = attribute_boost(enriched, attrs) if has_attrs else 0.0
                effective_confidence = (
                    calibrate(score + boost) if boost > 0 else confidence
                )

                # Apply calibrated confidence floor:
                # - "metadata": exact filter match, conf=97, never filtered
                # - "hybrid_filtered": concept ranks within a metadata-filtered set;
                #   floor applies so concept qualifiers narrow (e.g. "chanel 1993 red"
                #   drops non-red moments), but failures are kept in soft_results as a
                #   fallback if the full filtered set falls below floor.
                # - anything else (pure semantic): floor applies, no fallback
                match_type_raw = item.get("_match_type", "")
                is_metadata_only = match_type_raw == "metadata"
                is_hybrid_filtered = match_type_raw == "hybrid_filtered"

                if not is_metadata_only and effective_confidence < confidence_floor():
                    if is_hybrid_filtered:
                        # Keep for potential fallback (avoids total-empty on style concepts)
                        pass  # falls through to result build below, added to soft_results
                    else:
                        continue  # pure semantic — hard discard

                # Use the boosted confidence for display when it's higher
                if boost > 0 and effective_confidence > confidence:
                    confidence = effective_confidence

                enriched_out = {
                    "garments": enriched.get("garments", []),
                    "colours": enriched.get("colours", []),
                    "silhouette": enriched.get("silhouette", ""),
                    "key_pieces": enriched.get("key_pieces", []),
                    "search_tags": enriched.get("search_tags", []),
                }

                from services.database import make_show_key
                result_item = {
                    "moment_id": moment.id,
                    "show_id": show.id,
                    "show_key": make_show_key(show.brand, show.season),
                    "brand": show.brand,
                    "season": show.season,
                    "season_type": show.season_type,
                    "year": show.year,
                    "timestamp_start": moment.timestamp_start,
                    "timestamp_end": moment.timestamp_end,
                    "description": description,
                    "thumbnail_url": moment.thumbnail_url or item.get("thumbnail_url"),
                    "confidence": confidence,
                    "score_raw": round(score, 4),
                    "match_type": "hybrid" if is_hybrid_filtered else item.get("_match_type", "semantic"),
                    "is_bare_house": is_bare_brand,
                    "_boost": round(boost, 3),      # QA field
                    "_rank_score": score + boost,
                    "creative_director": show.creative_director,
                    "show_date": show.show_date.isoformat() if show.show_date else None,
                    "source": show.source,
                    "enriched": enriched_out,
                }

                if is_hybrid_filtered and effective_confidence < confidence_floor():
                    soft_results.append(result_item)
                else:
                    results.append(result_item)

    # Fallback: if filtered concept query yielded nothing above floor (e.g. "90s minimalism"),
    # surface the soft (sub-floor) filtered results sorted by confidence.
    # Never apply this fallback for bare-house queries — a house result is always strong.
    if not results and soft_results and not is_bare_brand:
        results = sorted(soft_results, key=lambda r: r["confidence"], reverse=True)

    # Re-rank and truncate when structured attributes are present
    if has_attrs and results:
        results.sort(key=lambda r: r["_rank_score"], reverse=True)
    results = results[: req.limit]

    elapsed = round((time.time() - start) * 1000)

    from services.database import log_event
    bg.add_task(log_event, "search",
        query_text=req.query,
        metadata={"result_count": len(results), "processing_time_ms": elapsed},
    )

    # Synthesize when ≥2 results; distinct-brand guard lives inside synthesize_results
    synthesis_text = None
    if len(results) >= 2:
        from services.claude import synthesize_results
        synthesis_text = await synthesize_results(req.query, results[:5])

    return {
        "query": req.query,
        "results": results,
        "total": len(results),
        "processing_time_ms": elapsed,
        "synthesis": synthesis_text,
    }


# ─────────────────────────────────────────
# SYNTHESIZE
# ─────────────────────────────────────────

class SynthesizeRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    moment_ids: list[str] = Field(..., min_length=1, max_length=50)


@app.post("/api/synthesize")
async def synthesize(req: SynthesizeRequest):
    """Grounded one-sentence synthesis over a set of search results."""
    from services.database import AsyncSessionLocal, Moment, Show
    from sqlalchemy import select
    from services.claude import synthesize_results

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Moment, Show)
            .join(Show, Show.id == Moment.show_id)
            .where(Moment.id.in_(req.moment_ids[:8]))
        )).all()

    moments = []
    for moment, show in rows:
        enriched = moment.enriched_data or {}
        moments.append({
            "moment_id": str(moment.id),
            "brand": show.brand,
            "season": show.season,
            "description": enriched.get("description") or moment.description or "",
            "enriched": {
                "garments": enriched.get("garments", []),
                "colours": enriched.get("colours", []),
                "silhouette": enriched.get("silhouette", ""),
            },
        })

    text = await synthesize_results(req.query, moments)
    cited_ids = [m["moment_id"] for m in moments]
    return {
        "synthesis": text or "",
        "grounded": text is not None,
        "cited_moment_ids": cited_ids,
    }


# ─────────────────────────────────────────
# EXPORT
# ─────────────────────────────────────────

class ExportRequest(BaseModel):
    moment_id: str
    confidence: Optional[int] = None


@app.post("/api/export")
async def export_moment(req: ExportRequest, bg: BackgroundTasks):
    """Returns a structured JSON export card for a single moment."""
    from services.database import AsyncSessionLocal, get_moment
    from sqlalchemy import select
    from services.database import Moment, Show

    async with AsyncSessionLocal() as session:
        stmt = (
            select(Moment, Show)
            .join(Show, Show.id == Moment.show_id)
            .where(Moment.id == req.moment_id)
        )
        row = (await session.execute(stmt)).first()

        if not row:
            raise HTTPException(status_code=404, detail="Moment not found")

        moment, show = row
        enriched = moment.enriched_data or {}

        export = {
            "export_version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source": "Fashion Archive — Internal MVP",
            "moment": {
                "brand": show.brand,
                "season": show.season,
                "year": show.year,
                "timestamp_start": moment.timestamp_start,
                "timestamp_end": moment.timestamp_end,
                "description": enriched.get("description") or moment.description,
                "thumbnail_url": moment.thumbnail_url,
                "confidence": req.confidence,
                "garments": enriched.get("garments", []),
                "colours": enriched.get("colours", []),
                "silhouette": enriched.get("silhouette", ""),
                "key_pieces": enriched.get("key_pieces", []),
                "search_tags": enriched.get("search_tags", []),
            },
        }

        filename = f"fa-export-{show.brand.lower()}-{int(moment.timestamp_start)}s.json"

        from services.database import log_event
        bg.add_task(log_event, "export",
            moment_id=req.moment_id,
            metadata={"brand": show.brand, "season": show.season},
        )

        return JSONResponse(
            content=export,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


# ─────────────────────────────────────────
# SHOW BRIEF
# ─────────────────────────────────────────

@app.get("/api/shows/{show_id}/brief")
async def get_show_brief(show_id: str, regenerate: bool = False):
    """
    Returns (and caches) a Claude-generated creative brief for a show.
    Brief covers: colour story, dominant silhouettes, key pieces, trend signals, season narrative.
    Pass ?regenerate=true to force a fresh generation.
    """
    from services.database import AsyncSessionLocal, Show, Moment
    from sqlalchemy import select, update

    async with AsyncSessionLocal() as session:
        show = (await session.execute(select(Show).where(Show.id == show_id))).scalar_one_or_none()
        if not show:
            raise HTTPException(status_code=404, detail="Show not found")

        if show.summary and not regenerate:
            return {"show_id": show_id, "brand": show.brand, "season": show.season, "brief": show.summary}

        moments = (await session.execute(
            select(Moment).where(Moment.show_id == show_id).where(Moment.description.isnot(None))
        )).scalars().all()

    if not moments:
        raise HTTPException(status_code=404, detail="No moments available for brief generation")

    descriptions = [m.description for m in moments if m.description and len(m.description) > 20]
    if not descriptions:
        raise HTTPException(status_code=404, detail="No valid descriptions found")

    sample = descriptions[:60]
    descriptions_block = "\n".join(f"- {d}" for d in sample)

    import anthropic
    client = anthropic.AsyncAnthropic()
    prompt = f"""You are a fashion intelligence system. Below are moment descriptions from {show.brand} {show.season}.

Descriptions:
{descriptions_block}

Write a concise show brief in exactly this structure (use these headings, keep each section tight):

**Colour story** — 1–2 sentences on dominant palette and mood.
**Silhouettes** — 1–2 sentences on recurring shapes, proportions, volume.
**Key pieces** — 3–5 bullet points, each naming one standout garment type with its defining detail.
**Trend signals** — 2–3 bullets on what this show signals for the season.
**Season narrative** — One sentence capturing the creative thesis of the show.

Be precise and editorial. Do not mention the brand name or designer. Under 200 words total."""

    msg = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    brief = msg.content[0].text.strip()

    async with AsyncSessionLocal() as session:
        await session.execute(update(Show).where(Show.id == show_id).values(summary=brief))
        await session.commit()

    return {"show_id": show_id, "brand": show.brand, "season": show.season, "brief": brief}


# ─────────────────────────────────────────
# MOOD BOARD EXPORT
# ─────────────────────────────────────────

class MoodBoardExportRequest(BaseModel):
    moment_ids: list[str]
    title: str = "Mood Board"

@app.post("/api/moodboard/export")
async def export_moodboard(req: MoodBoardExportRequest):
    """Returns full metadata for a list of moment IDs for mood board export."""
    from services.database import AsyncSessionLocal, Moment, Show
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Moment, Show)
            .join(Show, Show.id == Moment.show_id)
            .where(Moment.id.in_(req.moment_ids))
        )).all()

    items = []
    for moment, show in rows:
        enriched = moment.enriched_data or {}
        items.append({
            "moment_id": str(moment.id),
            "brand": show.brand,
            "season": show.season,
            "year": show.year,
            "timestamp_start": moment.timestamp_start,
            "description": enriched.get("description") or moment.description,
            "thumbnail_url": moment.thumbnail_url,
            "confidence": None,
        })

    return {
        "export_version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "title": req.title,
        "source": "Fashion Archive — Internal MVP",
        "items": items,
    }


# ─────────────────────────────────────────
# ADMIN — INTERNAL ONLY (no auth yet)
# ─────────────────────────────────────────

@app.get("/api/admin/events")
async def admin_events(limit: int = 20, _: None = Depends(_require_admin)):
    """Internal endpoint: recent events + total count. Requires Authorization: Bearer <ADMIN_TOKEN>."""
    from services.database import AsyncSessionLocal, Event
    from sqlalchemy import select, func

    async with AsyncSessionLocal() as session:
        total = (await session.execute(select(func.count()).select_from(Event))).scalar()
        rows = (await session.execute(
            select(Event).order_by(Event.created_at.desc()).limit(limit)
        )).scalars().all()

    return {
        "total": total,
        "recent": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "query_text": e.query_text,
                "moment_id": e.moment_id,
                "event_meta": e.event_meta,
                "created_at": e.created_at.isoformat(),
            }
            for e in rows
        ],
    }


# ─────────────────────────────────────────
# TIMELINE — TREND VELOCITY
# ─────────────────────────────────────────

CHANEL_CODES = ["tweed", "two_tone", "camellia", "pearls", "chains", "quilting"]


@app.get("/api/timeline")
async def get_timeline(house: str = "Chanel", season_type: str = "AW-RTW", code: str = None):
    """
    Returns the 10-point trend velocity timeline for a house + season type.

    Query params:
      house        — brand name (default: Chanel)
      season_type  — AW-RTW, SS-RTW, Couture (default: AW-RTW)
      code         — optional: filter moments to a specific house code
                     (tweed, two_tone, camellia, pearls, chains, quilting)

    Response includes:
      - 10 timeline points ordered by year
      - per-show code tag aggregates
      - CD-transition markers
      - if code specified: representative moment from each show + cross-year echo pair
    """
    from services.database import AsyncSessionLocal, Show, Moment
    from services.twelvelabs import embed_text
    from sqlalchemy import select, text as sql_text

    async with AsyncSessionLocal() as session:
        shows_rows = await session.execute(
            select(Show)
            .where(Show.brand == house)
            .where(Show.season_type == season_type)
            .where(Show.status == "ready")
            .order_by(Show.year)
        )
        shows = shows_rows.scalars().all()

    if not shows:
        return {"house": house, "season_type": season_type, "points": [], "total": 0}

    show_ids = [s.id for s in shows]

    # Single query for all moments across all shows — avoids N+1 sessions
    async with AsyncSessionLocal() as session:
        all_moments_rows = await session.execute(
            select(Moment).where(Moment.show_id.in_(show_ids))
        )
        all_moments = all_moments_rows.scalars().all()

    from collections import defaultdict
    moments_by_show: dict = defaultdict(list)
    for m in all_moments:
        moments_by_show[m.show_id].append(m)

    points = []
    for show in shows:
        moments = moments_by_show[show.id]
        total = len(moments)
        code_agg = {}
        for c in CHANEL_CODES:
            hits = sum(1 for m in moments if m.code_tags and m.code_tags.get(c))
            code_agg[c] = {"count": hits, "pct": round(hits / total * 100) if total else 0}

        # Representative moment for requested code
        rep_moment = None
        if code and code in CHANEL_CODES:
            tagged = [m for m in moments if m.code_tags and m.code_tags.get(code)]
            if tagged:
                m = tagged[0]
                rep_moment = {
                    "moment_id": m.id,
                    "timestamp_start": m.timestamp_start,
                    "timestamp_end": m.timestamp_end,
                    "description": m.description,
                    "thumbnail_url": m.thumbnail_url,
                }

        points.append({
            "show_id": show.id,
            "season": show.season,
            "year": show.year,
            "show_date": show.show_date.isoformat() if show.show_date else None,
            "creative_director": show.creative_director,
            "is_cd_transition": bool(show.is_cd_transition),
            "source": show.source,
            "look_count": total,
            "codes": code_agg,
            "rep_moment": rep_moment,
        })

    # Cross-year echo: find two moments (from different shows) with highest embedding similarity
    echo = None
    if code and code in CHANEL_CODES:
        try:
            async with AsyncSessionLocal() as session:
                # Gather all moments with this code tag and an embedding, ordered by show year
                rows = await session.execute(sql_text(f"""
                    SELECT m.id, m.show_id, m.timestamp_start, m.description, m.thumbnail_url,
                           s.season, s.year
                    FROM moments m
                    JOIN shows s ON s.id = m.show_id
                    WHERE s.brand = :house
                      AND s.season_type = :stype
                      AND m.embedding IS NOT NULL
                      AND m.code_tags ->> :code = 'true'
                    ORDER BY s.year, m.timestamp_start
                """), {"house": house, "stype": season_type, "code": code})
                tagged_moments = rows.fetchall()

            # Find the pair from different shows with best cosine similarity
            if len(tagged_moments) >= 2:
                async with AsyncSessionLocal() as session:
                    # Use embedding similarity: pick earliest show's first moment,
                    # find closest from a different show
                    anchor = tagged_moments[0]
                    anchor_vec_row = await session.execute(sql_text(
                        "SELECT embedding FROM moments WHERE id = :id"
                    ), {"id": anchor.id})
                    anchor_vec = anchor_vec_row.scalar()

                    if anchor_vec:
                        other_ids = [str(m.id) for m in tagged_moments if m.show_id != anchor.show_id]
                        if other_ids:
                            echo_row = await session.execute(sql_text("""
                                SELECT m.id, m.show_id, m.timestamp_start, m.description,
                                       m.thumbnail_url, s.season, s.year,
                                       1 - (m.embedding <=> cast(:vec AS vector)) AS similarity
                                FROM moments m
                                JOIN shows s ON s.id = m.show_id
                                WHERE m.id IN (SELECT unnest(cast(:ids AS text[])))
                                ORDER BY m.embedding <=> cast(:vec AS vector)
                                LIMIT 1
                            """), {"vec": str(anchor_vec), "ids": other_ids})
                            best = echo_row.fetchone()
                            if best:
                                echo = {
                                    "anchor": {
                                        "moment_id": anchor.id,
                                        "season": anchor.season,
                                        "year": anchor.year,
                                        "timestamp_start": anchor.timestamp_start,
                                        "description": anchor.description,
                                        "thumbnail_url": anchor.thumbnail_url,
                                    },
                                    "echo": {
                                        "moment_id": best.id,
                                        "season": best.season,
                                        "year": best.year,
                                        "timestamp_start": best.timestamp_start,
                                        "description": best.description,
                                        "thumbnail_url": best.thumbnail_url,
                                        "similarity": round(float(best.similarity), 3),
                                    },
                                }
        except Exception as e:
            logger.warning(f"Cross-year echo failed: {e}")

    return {
        "house": house,
        "season_type": season_type,
        "total": len(points),
        "codes_available": CHANEL_CODES,
        "points": points,
        "cross_year_echo": echo,
    }


@app.get("/api/moments/{moment_id}/play")
async def get_play_url(moment_id: str, bg: BackgroundTasks):
    """Returns HLS stream URL + timestamp for video playback."""
    from services.database import AsyncSessionLocal, Moment, Show
    from sqlalchemy import select
    import httpx

    async with AsyncSessionLocal() as session:
        stmt = (
            select(Moment, Show)
            .join(Show, Show.id == Moment.show_id)
            .where(Moment.id == moment_id)
        )
        row = (await session.execute(stmt)).first()
        if not row:
            raise HTTPException(status_code=404, detail="Moment not found")
        moment, show = row

    if not show.video_id:
        raise HTTPException(status_code=404, detail="No video ID for this show")

    TL_KEY = os.getenv("TWELVE_LABS_API_KEY")
    TL_INDEX = os.getenv("TWELVE_LABS_INDEX_ID")
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"https://api.twelvelabs.io/v1.3/indexes/{TL_INDEX}/videos/{show.video_id}",
            headers={"x-api-key": TL_KEY},
        )
        r.raise_for_status()
        data = r.json()

    hls_url = data.get("hls", {}).get("video_url")
    if not hls_url:
        raise HTTPException(status_code=404, detail="No HLS stream available")

    from services.database import log_event
    bg.add_task(log_event, "play",
        moment_id=moment_id,
        metadata={"brand": show.brand, "season": show.season},
    )

    return {
        "hls_url": hls_url,
        "timestamp_start": moment.timestamp_start,
        "timestamp_end": moment.timestamp_end,
        "brand": show.brand,
        "season": show.season,
    }
