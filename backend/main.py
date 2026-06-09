"""
Fashion Archive — Backend MVP v2
FastAPI + PostgreSQL + Twelve Labs + Claude

New in v2:
- Confidence scoring on search results (0–100, suppresses < 60)
- POST /api/export endpoint for per-moment JSON export
- Improved search response schema
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import asyncpg
import httpx
import os
import json
import anthropic
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Fashion Archive API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────

TWELVE_LABS_API_KEY = os.getenv("TWELVE_LABS_API_KEY")
TWELVE_LABS_INDEX_ID = os.getenv("TWELVE_LABS_INDEX_ID")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/fashion_archive")

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─────────────────────────────────────────
# MODELS
# ─────────────────────────────────────────

class IngestRequest(BaseModel):
    brand: str
    season: str
    year: int
    video_url: str

class SearchRequest(BaseModel):
    query: str
    limit: int = 20

class ExportRequest(BaseModel):
    moment_id: str

class SearchResult(BaseModel):
    moment_id: str
    show_id: str
    brand: str
    season: str
    year: int
    timestamp_start: float
    timestamp_end: float
    description: str
    thumbnail_url: Optional[str]
    confidence: int          # 0–100, integer
    score_raw: float         # raw cosine similarity

# ─────────────────────────────────────────
# DB CONNECTION
# ─────────────────────────────────────────

async def get_db():
    return await asyncpg.connect(DATABASE_URL)

# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def score_to_confidence(cosine_similarity: float) -> int:
    """
    Convert raw cosine similarity (0.0–1.0) to integer confidence (0–100).
    Results below 0.60 are suppressed at query time.
    """
    return round(cosine_similarity * 100)

def confidence_label(confidence: int) -> str:
    if confidence >= 90:
        return "Exact match"
    elif confidence >= 75:
        return "Strong match"
    elif confidence >= 60:
        return "Relevant"
    else:
        return "Weak"

async def twelve_labs_search(query: str, limit: int) -> list[dict]:
    """
    Semantic search via Twelve Labs Marengo.
    Returns list of {clip_id, score, start, end, video_id}
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.twelvelabs.io/v1.2/search",
            headers={
                "x-api-key": TWELVE_LABS_API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "index_id": TWELVE_LABS_INDEX_ID,
                "query_text": query,
                "search_options": ["visual", "conversation"],
                "threshold": "low",
                "page_limit": limit
            },
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])

# ─────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


@app.get("/api/shows")
async def get_shows():
    """Returns all ingested shows with moment counts."""
    db = await get_db()
    try:
        rows = await db.fetch("""
            SELECT s.id, s.brand, s.season, s.year,
                   COUNT(m.id) as moment_count,
                   CASE WHEN COUNT(m.id) > 0 THEN 'ready' ELSE 'processing' END as status
            FROM shows s
            LEFT JOIN moments m ON m.show_id = s.id
            GROUP BY s.id
            ORDER BY s.brand, s.year
        """)
        return {
            "shows": [
                {
                    "id": str(r["id"]),
                    "brand": r["brand"],
                    "season": r["season"],
                    "year": r["year"],
                    "moment_count": r["moment_count"],
                    "status": r["status"]
                }
                for r in rows
            ]
        }
    finally:
        await db.close()


@app.post("/api/ingest")
async def ingest_show(req: IngestRequest):
    """
    Queues a video for Twelve Labs ingestion.
    Run this once per show. Returns task_id to track progress.
    """
    db = await get_db()
    try:
        # Create show record
        show_id = await db.fetchval("""
            INSERT INTO shows (brand, season, year)
            VALUES ($1, $2, $3)
            RETURNING id
        """, req.brand, req.season, req.year)

        # Submit to Twelve Labs
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.twelvelabs.io/v1.2/tasks",
                headers={"x-api-key": TWELVE_LABS_API_KEY},
                json={
                    "index_id": TWELVE_LABS_INDEX_ID,
                    "video_url": req.video_url,
                    "language": "en"
                },
                timeout=30.0
            )
            response.raise_for_status()
            task_data = response.json()
            task_id = task_data["_id"]

        # Store task_id
        await db.execute("""
            UPDATE shows SET twelve_labs_video_id = $1 WHERE id = $2
        """, task_id, show_id)

        return {
            "show_id": str(show_id),
            "twelve_labs_task_id": task_id,
            "status": "queued"
        }
    finally:
        await db.close()


@app.post("/api/search")
async def search(req: SearchRequest):
    """
    Semantic search. Returns results with confidence scores.
    Suppresses results below confidence 60.
    """
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    import time
    start = time.time()

    db = await get_db()
    try:
        # Search via Twelve Labs
        tl_results = await twelve_labs_search(req.query, req.limit)

        if not tl_results:
            return {
                "query": req.query,
                "results": [],
                "total": 0,
                "processing_time_ms": round((time.time() - start) * 1000)
            }

        # Match TL results to our DB moments
        results = []
        for item in tl_results:
            video_id = item.get("video_id")
            score = item.get("score", 0.0)
            confidence = score_to_confidence(score)

            # Suppress weak matches
            if confidence < 60:
                continue

            # Look up show from TL video ID
            row = await db.fetchrow("""
                SELECT s.id as show_id, s.brand, s.season, s.year,
                       m.id as moment_id, m.timestamp_start, m.timestamp_end,
                       m.description, m.thumbnail_url
                FROM shows s
                JOIN moments m ON m.show_id = s.id
                WHERE s.twelve_labs_video_id = $1
                  AND m.timestamp_start <= $2
                  AND m.timestamp_end >= $2
                LIMIT 1
            """, video_id, item.get("start", 0))

            if row:
                results.append({
                    "moment_id": str(row["moment_id"]),
                    "show_id": str(row["show_id"]),
                    "brand": row["brand"],
                    "season": row["season"],
                    "year": row["year"],
                    "timestamp_start": row["timestamp_start"],
                    "timestamp_end": row["timestamp_end"],
                    "description": row["description"] or "No description available.",
                    "thumbnail_url": row["thumbnail_url"],
                    "confidence": confidence,
                    "score_raw": round(score, 4)
                })

        elapsed = round((time.time() - start) * 1000)
        return {
            "query": req.query,
            "results": results,
            "total": len(results),
            "processing_time_ms": elapsed
        }
    finally:
        await db.close()


@app.post("/api/export")
async def export_moment(req: ExportRequest):
    """
    Returns structured JSON export card for a single moment.
    Frontend triggers this as a file download.
    """
    db = await get_db()
    try:
        row = await db.fetchrow("""
            SELECT s.brand, s.season, s.year,
                   m.timestamp_start, m.timestamp_end,
                   m.description, m.thumbnail_url
            FROM moments m
            JOIN shows s ON s.id = m.show_id
            WHERE m.id = $1
        """, req.moment_id)

        if not row:
            raise HTTPException(status_code=404, detail="Moment not found")

        export = {
            "export_version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "source": "Fashion Archive — Internal MVP",
            "moment": {
                "brand": row["brand"],
                "season": row["season"],
                "year": row["year"],
                "timestamp_start": row["timestamp_start"],
                "timestamp_end": row["timestamp_end"],
                "description": row["description"],
                "thumbnail_url": row["thumbnail_url"]
            }
        }

        filename = f"fa-export-{row['brand'].lower()}-{int(row['timestamp_start'])}s.json"
        return JSONResponse(
            content=export,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    finally:
        await db.close()
