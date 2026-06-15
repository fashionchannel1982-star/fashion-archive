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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
import time
import os
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Fashion Archive API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
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
                    "brand": s.brand,
                    "season": s.season,
                    "year": s.year,
                    "status": s.status,
                    "moment_count": s.looks_count,
                    "summary": s.summary,
                }
                for s in shows
            ]
        }


# ─────────────────────────────────────────
# SEARCH
# ─────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    limit: int = 20


@app.post("/api/search")
async def search(req: SearchRequest):
    """Semantic search across ingested moments via Twelve Labs."""
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    start = time.time()

    from services import twelvelabs
    from services.database import AsyncSessionLocal, get_show
    from sqlalchemy import select, text
    from services.database import Moment, Show

    # Search via Twelve Labs
    tl_results = await twelvelabs.semantic_search(req.query, limit=req.limit)

    if not tl_results:
        return {
            "query": req.query,
            "results": [],
            "total": 0,
            "processing_time_ms": round((time.time() - start) * 1000),
        }

    results = []
    async with AsyncSessionLocal() as session:
        for item in tl_results:
            score = item.get("score", 0.0)
            confidence = round(score * 100)

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

                results.append({
                    "moment_id": moment.id,
                    "show_id": show.id,
                    "brand": show.brand,
                    "season": show.season,
                    "year": show.year,
                    "timestamp_start": moment.timestamp_start,
                    "timestamp_end": moment.timestamp_end,
                    "description": description,
                    "thumbnail_url": moment.thumbnail_url or item.get("thumbnail_url"),
                    "confidence": confidence,
                    "score_raw": round(score, 4),
                    "creative_director": show.creative_director,
                    "show_date": show.show_date.isoformat() if show.show_date else None,
                    "source": show.source,
                    "enriched": {
                        "garments": enriched.get("garments", []),
                        "colours": enriched.get("colours", []),
                        "silhouette": enriched.get("silhouette", ""),
                    },
                })

    elapsed = round((time.time() - start) * 1000)
    return {
        "query": req.query,
        "results": results,
        "total": len(results),
        "processing_time_ms": elapsed,
    }


# ─────────────────────────────────────────
# SYNTHESIZE
# ─────────────────────────────────────────

class SynthesizeRequest(BaseModel):
    query: str
    moment_ids: list[str]


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

    result = await synthesize_results(req.query, moments)
    return result


# ─────────────────────────────────────────
# EXPORT
# ─────────────────────────────────────────

class ExportRequest(BaseModel):
    moment_id: str


@app.post("/api/export")
async def export_moment(req: ExportRequest):
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
                "garments": enriched.get("garments", []),
                "colours": enriched.get("colours", []),
                "silhouette": enriched.get("silhouette", ""),
                "key_pieces": enriched.get("key_pieces", []),
                "search_tags": enriched.get("search_tags", []),
            },
        }

        filename = f"fa-export-{show.brand.lower()}-{int(moment.timestamp_start)}s.json"
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
        model="claude-sonnet-4-20250514",
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
    moment_ids: list
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

    points = []
    for show in shows:
        async with AsyncSessionLocal() as session:
            moments_rows = await session.execute(
                select(Moment).where(Moment.show_id == show.id)
            )
            moments = moments_rows.scalars().all()

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
                        vec_str = anchor_vec  # already stored as pgvector string
                        other_ids = [str(m.id) for m in tagged_moments if m.show_id != anchor.show_id]
                        if other_ids:
                            id_list = "', '".join(other_ids)
                            echo_row = await session.execute(sql_text(f"""
                                SELECT m.id, m.show_id, m.timestamp_start, m.description,
                                       m.thumbnail_url, s.season, s.year,
                                       1 - (m.embedding <=> '{vec_str}'::vector) AS similarity
                                FROM moments m
                                JOIN shows s ON s.id = m.show_id
                                WHERE m.id IN ('{id_list}')
                                ORDER BY m.embedding <=> '{vec_str}'::vector
                                LIMIT 1
                            """))
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
async def get_play_url(moment_id: str):
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

    return {
        "hls_url": hls_url,
        "timestamp_start": moment.timestamp_start,
        "timestamp_end": moment.timestamp_end,
        "brand": show.brand,
        "season": show.season,
    }
