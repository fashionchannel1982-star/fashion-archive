"""
Regenerate moment descriptions using Claude vision.
Fetches a frame from the HLS stream at each moment's timestamp,
sends it to Claude, and updates the DB.

Targets all placeholder descriptions ("— look at Xs") first,
then optionally all moments.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from services.database import AsyncSessionLocal, Moment, Show
from services.twelvelabs import get_hls_url, extract_frame, describe_frame_with_claude

BATCH_CONCURRENT = 4  # parallel Claude calls at once


async def regenerate_show(session: AsyncSession, show: Show, placeholder_only: bool = True):
    stmt = select(Moment).where(Moment.show_id == show.id)
    if placeholder_only:
        stmt = stmt.where(Moment.description.like("% — look at %"))
    moments = (await session.execute(stmt)).scalars().all()

    if not moments:
        return 0

    hls_url = await get_hls_url(show.video_id)
    if not hls_url:
        print(f"  No HLS URL for {show.brand} {show.season} — skipping")
        return 0

    print(f"  {show.brand} {show.season}: {len(moments)} moments to update")

    sem = asyncio.Semaphore(BATCH_CONCURRENT)
    updated = 0

    async def process(moment: Moment):
        nonlocal updated
        async with sem:
            frame = await extract_frame(hls_url, moment.timestamp_start)
            if not frame:
                return
            try:
                desc = await describe_frame_with_claude(frame, show.brand, show.season)
                moment.description = desc
                enriched = dict(moment.enriched_data or {})
                enriched["description"] = desc
                moment.enriched_data = enriched
                updated += 1
                if updated % 10 == 0:
                    print(f"    {updated}/{len(moments)} done")
            except Exception as e:
                print(f"    ERROR at {moment.timestamp_start}s: {e}")

    await asyncio.gather(*[process(m) for m in moments])
    await session.commit()
    return updated


async def main():
    placeholder_only = "--all" not in sys.argv
    print(f"Mode: {'placeholder descriptions only' if placeholder_only else 'all descriptions'}\n")

    async with AsyncSessionLocal() as session:
        shows = (await session.execute(
            select(Show).where(Show.status == "ready").where(Show.video_id.isnot(None))
        )).scalars().all()

        print(f"Found {len(shows)} ready shows\n")
        total = 0
        for show in shows:
            n = await regenerate_show(session, show, placeholder_only=placeholder_only)
            total += n
            if n:
                print(f"  ✓ {n} updated\n")

    print(f"Done. Total updated: {total}")


if __name__ == "__main__":
    asyncio.run(main())
