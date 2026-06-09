"""
Fashion Archive — Ingest Router
Endpoints for submitting and monitoring video ingestion.
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
import logging

from models.schemas import IngestYouTubeRequest, IngestStatusResponse
from services import twelvelabs, database as db
from services.database import get_session
from services.access_control import attach_provenance
from models.provenance import SourceType, AccessTier

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/youtube", response_model=IngestStatusResponse)
async def ingest_youtube(
    request: IngestYouTubeRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """
    Submit a YouTube URL for ingestion.

    Twelve Labs fetches the video directly — no downloading required.
    Returns immediately with a task_id for status polling.

    Example body:
    {
        "url": "https://www.youtube.com/watch?v=...",
        "brand": "Dior",
        "season": "SS2024",
        "year": 2024
    }
    """
    # Create show record in DB
    show = await db.create_show(session, {
        "brand": request.brand,
        "season": request.season,
        "year": request.year,
        "youtube_url": str(request.url),
        "status": "queued",
        "raw_metadata": {"notes": request.notes},
    })

    # Attach provenance — defaults to youtube_public
    await attach_provenance(
        session,
        show_id=show.id,
        source_name=request.brand,
        source_type=SourceType.youtube_public,
        source_url=str(request.url),
        submitted_by="api",
        attribution_display=None,  # Public YouTube — no attribution required
    )

    try:
        # Submit to Twelve Labs
        task_id = await twelvelabs.ingest_youtube_url(
            youtube_url=str(request.url),
            metadata={
                "brand": request.brand,
                "season": request.season,
                "year": request.year,
                "show_id": show.id,
            },
        )

        # Update show with task_id
        await db.update_show_status(session, show.id, "processing")
        show.task_id = task_id
        await session.commit()

        # Queue background processing once ingestion completes
        background_tasks.add_task(
            _process_after_ingestion,
            show_id=show.id,
            task_id=task_id,
        )

        return IngestStatusResponse(
            task_id=task_id,
            status="processing",
            message=f"Ingestion started for {request.brand} {request.season}",
        )

    except Exception as e:
        await db.update_show_status(session, show.id, "failed")
        logger.error(f"Ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{task_id}", response_model=IngestStatusResponse)
async def get_ingest_status(task_id: str):
    """
    Poll ingestion status. Call this every 10-15 seconds until status is 'ready'.

    Status values: pending | indexing | ready | failed
    """
    try:
        status_data = await twelvelabs.get_task_status(task_id)
        tl_status = status_data.get("status", "unknown")

        # Map Twelve Labs status to our status
        status_map = {
            "pending": "queued",
            "indexing": "processing",
            "ready": "completed",
            "failed": "failed",
        }

        return IngestStatusResponse(
            task_id=task_id,
            video_id=status_data.get("video_id"),
            status=status_map.get(tl_status, tl_status),
            progress=status_data.get("process", {}).get("upload_percentage"),
            message=f"Twelve Labs status: {tl_status}",
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/shows")
async def list_shows(
    limit: int = 20,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    """List all ingested shows."""
    shows = await db.list_shows(session, limit=limit, offset=offset)
    return {
        "shows": [
            {
                "id": s.id,
                "brand": s.brand,
                "season": s.season,
                "year": s.year,
                "status": s.status,
                "looks_count": s.looks_count,
                "youtube_url": s.youtube_url,
            }
            for s in shows
        ]
    }


# ─────────────────────────────────────────
# BACKGROUND PROCESSING
# ─────────────────────────────────────────

async def _process_after_ingestion(show_id: str, task_id: str):
    """
    Background task: once Twelve Labs ingestion completes,
    extract looks and enrich with Claude.
    """
    import asyncio
    from services.claude import enrich_look, generate_show_editorial
    from services.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        try:
            # Wait for Twelve Labs ingestion
            logger.info(f"Waiting for ingestion: {task_id}")
            status = await twelvelabs.wait_for_ingestion(task_id)
            video_id = status.get("video_id")

            if not video_id:
                raise Exception("No video_id returned from Twelve Labs")

            # Update show with video_id
            show = await db.get_show(session, show_id)
            show.video_id = video_id
            show.status = "processing"
            await session.commit()

            show_context = {
                "brand": show.brand,
                "season": show.season,
                "year": show.year,
            }

            # Generate look descriptions via Pegasus
            logger.info(f"Generating look descriptions for {video_id}")
            raw_looks = await twelvelabs.generate_look_descriptions(video_id)

            # Enrich each look with Claude
            enriched_looks = []
            for raw_look in raw_looks:
                enriched = await enrich_look(
                    raw_description=raw_look.get("description", ""),
                    show_context=show_context,
                )
                enriched_looks.append({
                    "show_id": show_id,
                    "look_number": raw_look.get("look_number", 0),
                    "description": raw_look.get("description", ""),
                    "enriched_data": enriched,
                    "timestamp_start": raw_look.get("timestamp_start"),
                    "timestamp_end": raw_look.get("timestamp_end"),
                })

            # Bulk save looks
            await db.bulk_create_looks(session, enriched_looks)

            # Generate show editorial summary
            editorial = await generate_show_editorial(show_context, enriched_looks)

            # Mark show as ready
            await db.update_show_status(
                session,
                show_id,
                status="ready",
                video_id=video_id,
                looks_count=len(enriched_looks),
                summary=editorial,
            )

            logger.info(f"Processing complete: {show.brand} {show.season} — {len(enriched_looks)} looks")

        except Exception as e:
            logger.error(f"Background processing failed for show {show_id}: {e}")
            await db.update_show_status(session, show_id, "failed")


@router.post("/partner", response_model=IngestStatusResponse)
async def ingest_partner_video(
    request,  # IngestPartnerRequest
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """
    Ingest a video from a fashion house partner.

    This is the endpoint for private content — videos shared directly
    by Dior, Chanel, Gucci, etc. that are not publicly available.

    Key differences from /youtube:
    - Provenance is explicitly set with restricted access by default
    - access_tier defaults to 'partner' (not visible to public/schools)
    - Embargo dates supported for pre-season content
    - audit log is created immediately

    Example body:
    {
        "url": "https://cdn.dior.com/private/archive/...",
        "brand": "Dior",
        "season": "SS2025",
        "year": 2025,
        "source_name": "Dior Archives",
        "source_type": "partner_private",
        "submitted_by": "archive@dior.com",
        "access_tier": "partner",
        "attribution_display": "Courtesy of Dior Archives",
        "restrictions_notes": "Internal research use only. Not for public display."
    }
    """
    from models.schemas import IngestPartnerRequest
    from models.provenance import SourceType, AccessTier
    from datetime import datetime

    show = await db.create_show(session, {
        "brand": request.brand,
        "season": request.season,
        "year": request.year,
        "youtube_url": str(request.url),
        "status": "queued",
        "raw_metadata": {
            "notes": request.notes,
            "partner_source": request.source_name,
        },
    })

    # Parse embargo date
    embargo_dt = None
    if request.embargo_until:
        try:
            embargo_dt = datetime.fromisoformat(request.embargo_until)
        except ValueError:
            pass

    # Attach explicit provenance — partner content is restricted by default
    await attach_provenance(
        session,
        show_id=show.id,
        source_name=request.source_name,
        source_type=request.source_type or SourceType.partner_private,
        source_url=str(request.url),
        submitted_by=request.submitted_by,
        access_tier=request.access_tier or AccessTier.partner,
        usage_rights=request.usage_rights,
        embargo_until=embargo_dt,
        attribution_display=request.attribution_display,
        restrictions_notes=request.restrictions_notes,
    )

    try:
        task_id = await twelvelabs.ingest_youtube_url(
            youtube_url=str(request.url),
            metadata={
                "brand": request.brand,
                "season": request.season,
                "year": request.year,
                "show_id": show.id,
                "source": request.source_name,
                "confidential": True,
            },
        )

        await db.update_show_status(session, show.id, "processing")
        show.task_id = task_id
        await session.commit()

        background_tasks.add_task(
            _process_after_ingestion,
            show_id=show.id,
            task_id=task_id,
        )

        return IngestStatusResponse(
            task_id=task_id,
            status="processing",
            message=f"Partner content ingestion started: {request.brand} {request.season} [{request.source_name}]",
        )

    except Exception as e:
        await db.update_show_status(session, show.id, "failed")
        logger.error(f"Partner ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/wetransfer", response_model=IngestStatusResponse)
async def ingest_wetransfer(
    wetransfer_url: str,
    brand: str,
    season: str,
    year: int,
    notes: str = None,
    background_tasks: BackgroundTasks = None,
    session: AsyncSession = Depends(get_session),
):
    """
    Ingest a video from a WeTransfer link.

    WeTransfer links cannot be processed directly — this endpoint:
    1. Creates the show record immediately
    2. Downloads the file in the background (may take several minutes)
    3. Uploads to Twelve Labs
    4. Processes looks and enriches with Claude

    Poll /status/{task_id} to track progress.

    Example:
        POST /api/ingest/wetransfer
        ?wetransfer_url=https://we.tl/t-xxxx
        &brand=Fashion+Channel
        &season=AW1999
        &year=1999
    """
    show = await db.create_show(session, {
        "brand": brand,
        "season": season,
        "year": year,
        "youtube_url": wetransfer_url,  # Storing original source URL
        "status": "queued",
        "raw_metadata": {
            "notes": notes,
            "source_type": "wetransfer",
            "wetransfer_url": wetransfer_url,
        },
    })

    # Attach provenance as FC licensed archive content
    await attach_provenance(
        session,
        show_id=show.id,
        source_name="Fashion Channel",
        source_type="archive_licensed",
        source_url=wetransfer_url,
        submitted_by="marzio@fashionchannel.tv",
        attribution_display="Courtesy of Fashion Channel Archive",
    )

    # Queue background download + ingest
    background_tasks.add_task(
        _download_and_ingest,
        show_id=show.id,
        wetransfer_url=wetransfer_url,
        brand=brand,
        season=season,
        year=year,
    )

    return IngestStatusResponse(
        task_id=show.id,  # Use show ID as task reference until TL task_id is available
        status="queued",
        message=f"Download queued for {brand} {season}. This may take 5–15 minutes depending on file size.",
    )


@router.post("/file", response_model=IngestStatusResponse)
async def ingest_local_file(
    file_path: str,
    brand: str,
    season: str,
    year: int,
    notes: str = None,
    background_tasks: BackgroundTasks = None,
    session: AsyncSession = Depends(get_session),
):
    """
    Ingest a video file already downloaded to the local machine.

    Use this when you've manually downloaded from WeTransfer and
    want to ingest from the local path.

    Example:
        POST /api/ingest/file
        ?file_path=/Users/yourname/Downloads/dior_ss2024.mp4
        &brand=Dior
        &season=SS2024
        &year=2024
    """
    import os
    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=400,
            detail=f"File not found at path: {file_path}. Make sure the path is correct."
        )

    show = await db.create_show(session, {
        "brand": brand,
        "season": season,
        "year": year,
        "status": "queued",
        "raw_metadata": {
            "notes": notes,
            "source_type": "local_file",
            "file_path": file_path,
        },
    })

    await attach_provenance(
        session,
        show_id=show.id,
        source_name="Fashion Channel",
        source_type="archive_licensed",
        source_url=file_path,
        submitted_by="manual_upload",
        attribution_display="Courtesy of Fashion Channel Archive",
    )

    background_tasks.add_task(
        _ingest_from_local_file,
        show_id=show.id,
        file_path=file_path,
        brand=brand,
        season=season,
        year=year,
    )

    return IngestStatusResponse(
        task_id=show.id,
        status="queued",
        message=f"File ingestion queued for {brand} {season}. Uploading to Twelve Labs...",
    )


# ─────────────────────────────────────────
# BACKGROUND TASKS — WeTransfer and file
# ─────────────────────────────────────────

async def _download_and_ingest(
    show_id: str,
    wetransfer_url: str,
    brand: str,
    season: str,
    year: int,
):
    """Background: download from WeTransfer then ingest."""
    from services.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        try:
            await db.update_show_status(session, show_id, "processing")

            task_id = await twelvelabs.ingest_wetransfer(
                wetransfer_url=wetransfer_url,
                metadata={
                    "brand": brand,
                    "season": season,
                    "year": year,
                    "show_id": show_id,
                },
            )

            # Update show with real Twelve Labs task_id
            show = await db.get_show(session, show_id)
            if show:
                show.task_id = task_id
                await session.commit()

            # Now wait for indexing and process
            await _process_after_ingestion(show_id=show_id, task_id=task_id)

        except Exception as e:
            logger.error(f"WeTransfer ingest failed for show {show_id}: {e}")
            await db.update_show_status(session, show_id, "failed")


async def _ingest_from_local_file(
    show_id: str,
    file_path: str,
    brand: str,
    season: str,
    year: int,
):
    """Background: upload local file to Twelve Labs then process."""
    from services.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        try:
            await db.update_show_status(session, show_id, "processing")

            task_id = await twelvelabs.ingest_local_file(
                file_path=file_path,
                metadata={
                    "brand": brand,
                    "season": season,
                    "year": year,
                    "show_id": show_id,
                },
            )

            show = await db.get_show(session, show_id)
            if show:
                show.task_id = task_id
                await session.commit()

            await _process_after_ingestion(show_id=show_id, task_id=task_id)

        except Exception as e:
            logger.error(f"Local file ingest failed for show {show_id}: {e}")
            await db.update_show_status(session, show_id, "failed")
