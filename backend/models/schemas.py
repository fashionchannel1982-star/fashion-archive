"""
Fashion Archive — Pydantic Schemas
Request/response models for all API endpoints.
"""

from pydantic import BaseModel, HttpUrl
from typing import Optional
from models.provenance import SourceType, AccessTier


# ─────────────────────────────────────────
# INGEST
# ─────────────────────────────────────────

class IngestYouTubeRequest(BaseModel):
    url: HttpUrl
    brand: str
    season: str
    year: int
    notes: Optional[str] = None


class IngestPartnerRequest(BaseModel):
    url: HttpUrl
    brand: str
    season: str
    year: int
    source_name: str
    source_type: Optional[SourceType] = SourceType.partner_private
    submitted_by: str
    access_tier: Optional[AccessTier] = AccessTier.partner
    usage_rights: Optional[str] = None
    embargo_until: Optional[str] = None
    attribution_display: Optional[str] = None
    restrictions_notes: Optional[str] = None
    notes: Optional[str] = None


class IngestStatusResponse(BaseModel):
    task_id: str
    status: str
    message: str
    video_id: Optional[str] = None
    progress: Optional[float] = None


# ─────────────────────────────────────────
# SEARCH
# ─────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str
    limit: int = 20
    brand: Optional[str] = None
    season: Optional[str] = None


class SearchResult(BaseModel):
    moment_id: str
    show_id: str
    brand: str
    season: str
    year: int
    timestamp_start: float
    timestamp_end: float
    description: str
    thumbnail_url: Optional[str] = None
    confidence: int
    score_raw: float
    creative_director: Optional[str] = None
    show_date: Optional[str] = None
    source: Optional[str] = None
    enriched: Optional[dict] = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResult]
    total: int
    processing_time_ms: int


# ─────────────────────────────────────────
# SHOWS
# ─────────────────────────────────────────

class ShowResponse(BaseModel):
    id: str
    brand: str
    season: str
    year: int
    status: str
    moment_count: int
    summary: Optional[str] = None


# ─────────────────────────────────────────
# EXPORT
# ─────────────────────────────────────────

class ExportRequest(BaseModel):
    moment_id: str
