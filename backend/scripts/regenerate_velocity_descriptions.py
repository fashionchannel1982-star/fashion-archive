"""
Regenerate descriptions + embeddings + thumbnails for Chanel AW-RTW velocity moments.

Uses Pegasus (describe_segment_with_pegasus) — NOT Claude vision.
Frame extracted at segment midpoint ((start+end)/2) for better texture/fabric coverage.
Brand/season never sent to the caption model.

Targets moments with invalid/placeholder descriptions by default.
Pass --all to regenerate every moment.

Run: python scripts/regenerate_velocity_descriptions.py [--all]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, update
from services.database import AsyncSessionLocal, Moment, Show
from services.twelvelabs import (
    get_hls_url, extract_frame, embed_image, save_thumbnail,
    describe_segment_with_pegasus, _is_valid_description,
)

CONCURRENT = 3


async def process_moment(moment: Moment, hls_url: str, sem: asyncio.Semaphore) -> bool:
    async with sem:
        midpoint = (moment.timestamp_start + moment.timestamp_end) / 2.0

        # Description via Pegasus — no brand in prompt
        desc = await describe_segment_with_pegasus(
            moment.show.video_id, moment.timestamp_start, moment.timestamp_end
        )

        # Frame at midpoint for embedding + thumbnail
        frame = await extract_frame(hls_url, midpoint)
        vec = await embed_image(frame) if frame else None
        thumb_url = save_thumbnail(str(moment.id), frame) if frame else None

        values: dict = {}
        if desc is not None:
            values["description"] = desc
            values["enriched_data"] = {"description": desc}
        if vec is not None:
            values["embedding"] = vec
        if thumb_url and not moment.thumbnail_url:
            values["thumbnail_url"] = thumb_url

        if values:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(Moment).where(Moment.id == moment.id).values(**values)
                )
                await session.commit()

        return desc is not None


async def regenerate_show(show: Show, all_moments: bool) -> dict:
    async with AsyncSessionLocal() as session:
        moments_raw = (await session.execute(
            select(Moment).where(Moment.show_id == show.id)
        )).scalars().all()

    targets = [
        m for m in moments_raw
        if all_moments or not _is_valid_description(m.description or "")
    ]

    if not targets:
        print(f"  {show.season}: all descriptions valid — skipping")
        return {"total": len(moments_raw), "updated": 0, "failed": 0}

    hls_url = await get_hls_url(show.video_id)
    if not hls_url:
        print(f"  {show.season}: no HLS URL — skipping")
        return {"total": len(moments_raw), "updated": 0, "failed": 0}

    # Attach show reference so process_moment can access video_id
    for m in targets:
        m.show = show

    print(f"  {show.season}: {len(targets)}/{len(moments_raw)} to regenerate")
    sem = asyncio.Semaphore(CONCURRENT)
    done = 0
    failed = 0

    async def run_one(m: Moment):
        nonlocal done, failed
        ok = await process_moment(m, hls_url, sem)
        if ok:
            done += 1
            if done % 10 == 0:
                print(f"      {done}/{len(targets)} done")
        else:
            failed += 1

    await asyncio.gather(*[run_one(m) for m in targets])
    print(f"  ✓ {done} updated, {failed} failed")
    return {"total": len(moments_raw), "updated": done, "failed": failed}


async def main():
    all_moments = "--all" in sys.argv
    print(f"Mode: {'all moments' if all_moments else 'placeholder/invalid descriptions only'}\n")

    async with AsyncSessionLocal() as session:
        shows = (await session.execute(
            select(Show)
            .where(Show.brand == "Chanel")
            .where(Show.season_type == "AW-RTW")
            .where(Show.status == "ready")
            .where(Show.video_id.isnot(None))
            .order_by(Show.year)
        )).scalars().all()

    print(f"Regenerating for {len(shows)} Chanel AW-RTW shows\n")
    total_updated = 0
    for show in shows:
        stats = await regenerate_show(show, all_moments)
        total_updated += stats["updated"]

    print(f"\nDone. Total updated: {total_updated}")


if __name__ == "__main__":
    asyncio.run(main())
