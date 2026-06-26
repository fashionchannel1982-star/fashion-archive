"""
Idempotent per-show add/replace.

ADD  — show_key absent from DB → create row, run full ingest pipeline.
REPLACE — show_key present → ingest new video, atomically swap moments in one
          transaction, then (and only then) delete the old TL video.

The old show stays fully live (status=ready, moments intact) until the DB
transaction commits. Any failure before that commit leaves the old show
untouched.

Usage:
    python scripts/upsert_show.py \
        --brand "Dior" \
        --season "AW25-RTW" \
        --source fc_master \
        --video "https://..." \
        [--year 2025] \
        [--creative-director "Maria Grazia Chiuri"] \
        [--show-date 2025-03-04]
"""

import asyncio
import argparse
import logging
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, delete as sql_delete
from services.database import AsyncSessionLocal, Show, Moment, Provenance, make_show_key
from services.claude import enrich_look, generate_show_editorial
import services.twelvelabs as twelvelabs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("upsert_show")


# ─────────────────────────────────────────
# INGEST PIPELINE (shared by ADD + REPLACE)
# ─────────────────────────────────────────

async def run_ingest_pipeline(video: str, show_context: dict) -> tuple[str, str, list[dict], str]:
    """
    Upload video → wait → generate looks → enrich → editorial.
    Returns (video_id, task_id, enriched_looks, editorial).
    video can be a YouTube URL, WeTransfer URL, or local file path.
    """
    brand, season, year = show_context["brand"], show_context["season"], show_context["year"]
    metadata = {"brand": brand, "season": season, "year": year}

    logger.info(f"Uploading video for {brand} {season}…")
    if video.startswith("http"):
        if "wetransfer" in video:
            task_id = await twelvelabs.ingest_wetransfer(video, metadata)
        else:
            task_id = await twelvelabs.ingest_youtube_url(video, metadata)
    else:
        task_id = await twelvelabs.ingest_local_file(video, metadata)

    logger.info(f"Waiting for TL ingestion (task {task_id})…")
    status = await twelvelabs.wait_for_ingestion(task_id)
    video_id = status.get("video_id")
    if not video_id:
        raise RuntimeError(f"No video_id returned from TL: {status}")
    logger.info(f"TL video ready: {video_id}")

    logger.info("Generating look descriptions via Pegasus…")
    raw_looks = await twelvelabs.generate_look_descriptions(video_id, brand=brand, season=season)

    logger.info(f"Enriching {len(raw_looks)} looks with Claude…")
    enriched_looks = []
    for raw in raw_looks:
        enriched = await enrich_look(raw.get("description", ""), show_context)
        enriched_looks.append({
            "look_number": raw.get("look_number", 0),
            "description": raw.get("description", ""),
            "enriched_data": enriched,
            "timestamp_start": raw.get("timestamp_start"),
            "timestamp_end": raw.get("timestamp_end"),
        })

    logger.info("Generating show editorial…")
    editorial = await generate_show_editorial(show_context, enriched_looks)

    return video_id, task_id, enriched_looks, editorial


# ─────────────────────────────────────────
# ADD
# ─────────────────────────────────────────

async def add_show(args: argparse.Namespace) -> None:
    show_key = make_show_key(args.brand, args.season)
    logger.info(f"ADD: {show_key}")

    show_id = str(uuid.uuid4())
    show_context = {"brand": args.brand, "season": args.season, "year": args.year}

    async with AsyncSessionLocal() as session:
        show = Show(
            id=show_id,
            brand=args.brand,
            season=args.season,
            year=args.year,
            show_key=show_key,
            source=args.source,
            source_url=args.video if args.video.startswith("http") else None,
            creative_director=args.creative_director,
            show_date=datetime.fromisoformat(args.show_date) if args.show_date else None,
            status="queued",
        )
        session.add(show)
        await session.commit()
        logger.info(f"Show row created: {show_id}")

    video_id, task_id, enriched_looks, editorial = await run_ingest_pipeline(args.video, show_context)

    async with AsyncSessionLocal() as session:
        show = (await session.execute(select(Show).where(Show.id == show_id))).scalar_one()
        show.video_id = video_id
        show.task_id = task_id
        show.looks_count = len(enriched_looks)
        show.summary = editorial
        show.status = "ready"

        for look in enriched_looks:
            session.add(Moment(
                id=str(uuid.uuid4()),
                show_id=show_id,
                look_number=look["look_number"],
                description=look["description"],
                enriched_data=look["enriched_data"],
                timestamp_start=look["timestamp_start"],
                timestamp_end=look["timestamp_end"],
            ))

        prov = Provenance(
            show_id=show_id,
            source_name="FC Master Archive" if args.source == "fc_master" else "YouTube",
            source_type=args.source,
            source_url=args.video if args.video.startswith("http") else None,
            submitted_by="script",
        )
        session.add(prov)
        await session.commit()

    logger.info(f"ADD complete: {args.brand} {args.season} — {len(enriched_looks)} moments, status=ready")
    logger.info(f"  show_id={show_id}  video_id={video_id}  show_key={show_key}")
    logger.info("Run next: python scripts/generate_embeddings.py --show-id " + show_id)


# ─────────────────────────────────────────
# REPLACE
# ─────────────────────────────────────────

async def replace_show(existing: Show, args: argparse.Namespace) -> None:
    show_id = existing.id
    old_video_id = existing.video_id
    show_context = {"brand": existing.brand, "season": existing.season, "year": args.year or existing.year}
    logger.info(f"REPLACE: show_id={show_id}  old_video_id={old_video_id}")

    # Mark in-progress so verify_index shows it's being worked on
    async with AsyncSessionLocal() as session:
        show = (await session.execute(select(Show).where(Show.id == show_id))).scalar_one()
        show.status = "replacing"
        await session.commit()

    try:
        # Step a: full ingest of new video — old show untouched throughout
        video_id, task_id, enriched_looks, editorial = await run_ingest_pipeline(args.video, show_context)

        # Step b: atomic swap in a single transaction
        async with AsyncSessionLocal() as session:
            show = (await session.execute(
                select(Show).where(Show.id == show_id)
            )).scalar_one()

            # Delete old moments (cascade removes embeddings via ORM relationship)
            await session.execute(sql_delete(Moment).where(Moment.show_id == show_id))

            # Insert new moments
            for look in enriched_looks:
                session.add(Moment(
                    id=str(uuid.uuid4()),
                    show_id=show_id,
                    look_number=look["look_number"],
                    description=look["description"],
                    enriched_data=look["enriched_data"],
                    timestamp_start=look["timestamp_start"],
                    timestamp_end=look["timestamp_end"],
                ))

            # Update all show-level fields that change with the video
            new_source = args.source or existing.source
            show.video_id = video_id
            show.task_id = task_id
            show.looks_count = len(enriched_looks)
            show.summary = editorial
            show.source = new_source
            show.source_url = args.video if args.video.startswith("http") else existing.source_url
            if args.creative_director:
                show.creative_director = args.creative_director
            if args.show_date:
                show.show_date = datetime.fromisoformat(args.show_date)
            if args.year:
                show.year = args.year
            show.status = "ready"

            # Update provenance row
            prov = (await session.execute(
                select(Provenance).where(Provenance.show_id == show_id)
            )).scalar_one_or_none()
            if prov:
                prov.source_name = "FC Master Archive" if new_source == "fc_master" else "YouTube"
                prov.source_type = new_source
                prov.source_url = args.video if args.video.startswith("http") else prov.source_url
            else:
                session.add(Provenance(
                    show_id=show_id,
                    source_name="FC Master Archive" if new_source == "fc_master" else "YouTube",
                    source_type=new_source,
                    source_url=args.video if args.video.startswith("http") else None,
                    submitted_by="script",
                ))

            await session.commit()

        logger.info(f"DB swap committed: {len(enriched_looks)} new moments, status=ready")

    except Exception as e:
        logger.error(f"REPLACE failed before/during commit: {e}", exc_info=True)
        # Restore old show to ready so search keeps working
        async with AsyncSessionLocal() as session:
            show = (await session.execute(select(Show).where(Show.id == show_id))).scalar_one()
            show.status = "ready"
            await session.commit()
        logger.error("Rolled back status to ready — old show intact, no data lost")
        raise

    # Step c: generate embeddings for new moments
    logger.info("Run embeddings for new moments:")
    logger.info(f"  python scripts/generate_embeddings.py --show-id {show_id}")

    # Step d: re-tag Chanel codes if applicable
    if existing.brand.lower() == "chanel" and existing.season_type == "AW-RTW":
        logger.info("Chanel house — re-run: python scripts/tag_chanel_codes.py")

    # Step e: delete old TL video LAST — only after commit succeeded
    if old_video_id and old_video_id != video_id:
        logger.info(f"Deleting old TL video: {old_video_id}")
        deleted = await twelvelabs.delete_video(old_video_id)
        if deleted:
            logger.info(f"Old TL video deleted: {old_video_id}")
        else:
            logger.warning(f"Old TL video was already absent or delete failed: {old_video_id} — run verify_tl_sync.py")

    logger.info(f"REPLACE complete: {existing.brand} {existing.season}")
    logger.info(f"  show_id={show_id}  new_video_id={video_id}  show_key={existing.show_key}")
    logger.info("Run verify: python scripts/verify_tl_sync.py && python scripts/verify_index.py")


# ─────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="Idempotent show add/replace")
    parser.add_argument("--brand", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument("--video", required=True, help="YouTube URL, WeTransfer URL, or local file path")
    parser.add_argument("--source", default=None, choices=["youtube_mvp", "fc_master"],
                        help="Source identifier (defaults to keeping existing on replace)")
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--creative-director", default=None)
    parser.add_argument("--show-date", default=None, help="YYYY-MM-DD")
    args = parser.parse_args()

    show_key = make_show_key(args.brand, args.season)
    logger.info(f"show_key: {show_key}")

    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(Show).where(Show.show_key == show_key)
        )).scalar_one_or_none()

    if existing is None:
        if not args.year:
            parser.error("--year is required for ADD (show doesn't exist yet)")
        if not args.source:
            parser.error("--source is required for ADD")
        await add_show(args)
    else:
        logger.info(f"Existing show found: id={existing.id}  source={existing.source}  status={existing.status}")
        if existing.status == "replacing":
            logger.error("Show is already mid-replace — check logs and resolve manually before re-running")
            sys.exit(1)
        await replace_show(existing, args)


if __name__ == "__main__":
    asyncio.run(main())
