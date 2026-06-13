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
    show = await db.create_show(session, {
        "brand": request.brand,
        "season": request.season,
        "year": request.year,
        "youtube_url": str(request.url),
        "status": "queued",
        "raw_metadata": {"notes": request.notes},
    })

    await attach_provenance(
        session,
        show_id=show.id,
        source_name=request.brand,
        source_type=SourceType.youtube_public,
        source_url=str(request.url),
        submitted_by="api",
    )

    try:
        task_id = await twelvelabs.ingest_youtube_url(
            youtube_url=str(request.url),
            metadata={"brand": request.brand, "season": request.season, "year": request.year, "show_id": show.id},
        )
        await db.update_show_status(session, show.id, "processing")
        show.task_id = task_id
        await session.commit()
        background_tasks.add_task(_process_after_ingestion, show_id=show.id, task_id=task_id)
        return IngestStatusResponse(task_id=task_id, status="processing", message=f"Ingestion started for {request.brand} {request.season}")
    except Exception as e:
        await db.update_show_status(session, show.id, "failed")
        logger.error(f"Ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{task_id}", response_model=IngestStatusResponse)
async def get_ingest_status(task_id: str):
    try:
        status_data = await twelvelabs.get_task_status(task_id)
        tl_status = status_data.get("status", "unknown")
        status_map = {"pending": "queued", "indexing": "processing", "ready": "completed", "failed": "failed"}
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
async def list_shows(limit: int = 20, offset: int = 0, session: AsyncSession = Depends(get_session)):
    shows = await db.list_shows(session, limit=limit, offset=offset)
    return {"shows": [{"id": s.id, "brand": s.brand, "season": s.season, "year": s.year, "status": s.status, "looks_count": s.looks_count} for s in shows]}


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
    show = await db.create_show(session, {
        "brand": brand, "season": season, "year": year,
        "youtube_url": wetransfer_url, "status": "queued",
        "raw_metadata": {"notes": notes, "source_type": "wetransfer", "wetransfer_url": wetransfer_url},
    })
    await attach_provenance(
        session, show_id=show.id, source_name="Fashion Channel",
        source_type="archive_licensed", source_url=wetransfer_url,
        submitted_by="marzio@fashionchannel.tv",
        attribution_display="Courtesy of Fashion Channel Archive",
    )
    background_tasks.add_task(_download_and_ingest, show_id=show.id, wetransfer_url=wetransfer_url, brand=brand, season=season, year=year)
    return IngestStatusResponse(task_id=show.id, status="queued", message=f"Download queued for {brand} {season}. This may take 5–15 minutes.")


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
    import os
    if not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail=f"File not found: {file_path}")

    show = await db.create_show(session, {
        "brand": brand, "season": season, "year": year, "status": "queued",
        "raw_metadata": {"notes": notes, "source_type": "local_file", "file_path": file_path},
    })
    await attach_provenance(
        session, show_id=show.id, source_name="Fashion Channel",
        source_type="archive_licensed", source_url=file_path,
        submitted_by="manual_upload", attribution_display="Courtesy of Fashion Channel Archive",
    )
    background_tasks.add_task(_ingest_from_local_file, show_id=show.id, file_path=file_path, brand=brand, season=season, year=year)
    return IngestStatusResponse(task_id=show.id, status="queued", message=f"File ingestion queued for {brand} {season}.")


# ─────────────────────────────────────────
# BACKGROUND TASKS
# ─────────────────────────────────────────

async def _process_after_ingestion(show_id: str, task_id: str):
    import asyncio
    from services.claude import enrich_look, generate_show_editorial
    from services.database import AsyncSessionLocal, Moment
    from services.twelvelabs import save_thumbnail
    from sqlalchemy import select, update

    async with AsyncSessionLocal() as session:
        try:
            status = await twelvelabs.wait_for_ingestion(task_id)
            video_id = status.get("video_id")
            if not video_id:
                raise Exception("No video_id returned from Twelve Labs")

            show = await db.get_show(session, show_id)
            show.video_id = video_id
            show.status = "processing"
            await session.commit()

            show_context = {"brand": show.brand, "season": show.season, "year": show.year}

            raw_looks = await twelvelabs.generate_look_descriptions(video_id)

            enriched_looks = []
            frames = {}  # timestamp_start → frame bytes
            for raw_look in raw_looks:
                enriched = await enrich_look(raw_description=raw_look.get("description", ""), show_context=show_context)
                ts = raw_look.get("timestamp_start", 0)
                if raw_look.get("_frame"):
                    frames[ts] = raw_look["_frame"]
                enriched_looks.append({
                    "show_id": show_id,
                    "look_number": raw_look.get("look_number", 0),
                    "description": raw_look.get("description"),
                    "enriched_data": enriched,
                    "timestamp_start": ts,
                    "timestamp_end": raw_look.get("timestamp_end", 0),
                    "thumbnail_url": None,  # patched below after IDs are known
                })

            await db.bulk_create_looks(session, enriched_looks)

            # Patch thumbnail_url now that moment IDs exist
            if frames:
                rows = (await session.execute(
                    select(Moment).where(Moment.show_id == show_id)
                )).scalars().all()
                for moment in rows:
                    frame = frames.get(moment.timestamp_start)
                    if frame:
                        url = save_thumbnail(str(moment.id), frame)
                        await session.execute(
                            update(Moment).where(Moment.id == moment.id).values(thumbnail_url=url)
                        )
                await session.commit()

            editorial = await generate_show_editorial(show_context, enriched_looks)
            await db.update_show_status(session, show_id, status="ready", video_id=video_id, looks_count=len(enriched_looks), summary=editorial)
            logger.info(f"Processing complete: {show.brand} {show.season} — {len(enriched_looks)} looks")

        except Exception as e:
            logger.error(f"Background processing failed for show {show_id}: {e}")
            await db.update_show_status(session, show_id, "failed")


async def _download_and_ingest(show_id: str, wetransfer_url: str, brand: str, season: str, year: int):
    from services.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        try:
            await db.update_show_status(session, show_id, "processing")
            task_id = await twelvelabs.ingest_wetransfer(wetransfer_url=wetransfer_url, metadata={"brand": brand, "season": season, "year": year, "show_id": show_id})
            show = await db.get_show(session, show_id)
            if show:
                show.task_id = task_id
                await session.commit()
            await _process_after_ingestion(show_id=show_id, task_id=task_id)
        except Exception as e:
            logger.error(f"WeTransfer ingest failed for show {show_id}: {e}")
            await db.update_show_status(session, show_id, "failed")


async def _ingest_from_local_file(show_id: str, file_path: str, brand: str, season: str, year: int):
    from services.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        try:
            await db.update_show_status(session, show_id, "processing")
            task_id = await twelvelabs.ingest_local_file(file_path=file_path, metadata={"brand": brand, "season": season, "year": year, "show_id": show_id})
            show = await db.get_show(session, show_id)
            if show:
                show.task_id = task_id
                await session.commit()
            await _process_after_ingestion(show_id=show_id, task_id=task_id)
        except Exception as e:
            logger.error(f"Local file ingest failed for show {show_id}: {e}")
            await db.update_show_status(session, show_id, "failed")
