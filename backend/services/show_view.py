"""
Show metadata projections — single source of truth for field exposure.

Two functions decide what is shared and with whom:
  internal_metadata(show, moments, provenance) — everything; for tooling/ops
  client_safe_metadata(show)                   — curated public subset only

Add a new view tier? Write a new function here, not in the endpoint.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from services.database import Show, Moment, Provenance


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def _health(moments: list) -> dict:
    total = len(moments)
    if total == 0:
        return {
            "total_moments": 0,
            "with_embedding": 0,
            "with_enriched_data": 0,
            "with_code_tags": 0,
            "embedding_pct": 0,
            "enriched_pct": 0,
            "code_tag_pct": 0,
            "warnings": ["No moments — show may not have been embedded yet"],
        }

    with_emb = sum(1 for m in moments if m.embedding is not None)
    with_enr = sum(1 for m in moments if m.enriched_data)
    with_tag = sum(1 for m in moments if m.code_tags)

    warnings = []
    if with_emb < total:
        warnings.append(f"{total - with_emb} moments missing embeddings — run generate_embeddings.py")
    if with_enr < total:
        warnings.append(f"{total - with_enr} moments missing enriched_data")
    if with_enr > 0:
        # Check if structured fields are actually populated (6A blocker)
        colours_ok = sum(
            1 for m in moments
            if m.enriched_data and m.enriched_data.get("colours")
        )
        if colours_ok == 0:
            warnings.append("enriched_data.colours is empty for all moments — 6A re-rank is a no-op")

    return {
        "total_moments": total,
        "with_embedding": with_emb,
        "with_enriched_data": with_enr,
        "with_code_tags": with_tag,
        "embedding_pct": round(100 * with_emb / total),
        "enriched_pct": round(100 * with_enr / total),
        "code_tag_pct": round(100 * with_tag / total),
        "warnings": warnings,
    }


def _provenance_dict(prov) -> Optional[dict]:
    if prov is None:
        return None
    return {
        "source_name": prov.source_name,
        "source_type": prov.source_type,
        "source_url": prov.source_url,
        "submitted_by": prov.submitted_by,
        "access_tier": prov.access_tier,
        "usage_rights": prov.usage_rights,
        "embargo_until": _iso(prov.embargo_until),
        "attribution_display": prov.attribution_display,
        "restrictions_notes": prov.restrictions_notes,
        "created_at": _iso(prov.created_at),
    }


def internal_metadata(show, moments: list, provenance=None) -> dict:
    """
    Full internal metadata — ops/tooling only, never exposed to clients.
    Includes video_id, task_id, status, provenance, and health diagnostics.
    """
    prov = provenance or show.provenance
    health = _health(moments)
    provenance_complete = (
        bool(show.creative_director) and
        bool(show.show_date) and
        bool(show.source) and
        prov is not None
    )

    sample = [
        {
            "moment_id": m.id,
            "look_number": m.look_number,
            "timestamp_start": m.timestamp_start,
            "timestamp_end": m.timestamp_end,
            "description": (m.enriched_data or {}).get("description") or m.description,
            "thumbnail_url": m.thumbnail_url,
            "has_embedding": m.embedding is not None,
            "has_code_tags": bool(m.code_tags),
        }
        for m in moments[:5]
    ]

    return {
        # Identity
        "id": show.id,
        "show_key": show.show_key,
        # Editorial
        "brand": show.brand,
        "season": show.season,
        "season_type": show.season_type,
        "year": show.year,
        "creative_director": show.creative_director,
        "show_date": _iso(show.show_date),
        "models": (show.raw_metadata or {}).get("models"),
        "summary": show.summary,
        "is_cd_transition": show.is_cd_transition,
        # Technical
        "status": show.status,
        "video_id": show.video_id,
        "task_id": show.task_id,
        "source": show.source,
        "source_url": show.source_url,
        "youtube_url": show.youtube_url,
        "created_at": _iso(show.created_at),
        # Health
        "health": health,
        "provenance_complete": provenance_complete,
        "provenance": _provenance_dict(prov),
        # Sample
        "sample_moments": sample,
    }


def client_safe_metadata(show) -> dict:
    """
    Curated client-facing subset — no operational fields, no source secrets.
    models is a forward-compatible nullable slot (not yet stored in DB).
    This is the ONLY function that should populate a client-facing response.
    """
    return {
        "show_key": show.show_key,
        "brand": show.brand,
        "season": show.season,
        "season_type": show.season_type,
        "year": show.year,
        "creative_director": show.creative_director,
        "models": (show.raw_metadata or {}).get("models"),
        "show_date": _iso(show.show_date),
        "summary": show.summary,
    }
