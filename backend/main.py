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
app.include_router(ingest_router, prefix="/api/ingest", tags=["ingest"])


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
            video_id = item.get("video_id")
            score = item.get("score", 0.0)
            confidence = round(score * 100)

            if confidence < 60:
                continue

            # Find matching moment in DB
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
                description = enriched.get("description") or moment.description or "No description available."
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
                })

    elapsed = round((time.time() - start) * 1000)
    return {
        "query": req.query,
        "results": results,
        "total": len(results),
        "processing_time_ms": elapsed,
    }


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
