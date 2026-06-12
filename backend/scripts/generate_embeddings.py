"""
Generate Marengo3.0 image embeddings for all moments.

For each moment:
  1. Get HLS URL for the parent show
  2. Extract a frame via ffmpeg at timestamp_start
  3. POST frame to TL /embed → 512-dim vector
  4. Store in moments.embedding

Also captures thumbnail_url from the extracted frame (saved to /tmp/thumbnails/).
Run with: python scripts/generate_embeddings.py [--batch N] [--show-id UUID]
"""
import asyncio
import argparse
import os
import sys
import base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from services.database import AsyncSessionLocal, Moment, Show
from services.twelvelabs import get_hls_url, extract_frame, embed_image

CONCURRENT = 4  # parallel embedding calls


async def process_moment(session: AsyncSession, moment: Moment, hls_url: str, sem: asyncio.Semaphore) -> bool:
    async with sem:
        frame = await extract_frame(hls_url, moment.timestamp_start)
        if not frame:
            return False
        vec = await embed_image(frame)
        if not vec:
            return False

        # Store embedding (pgvector expects a list of floats)
        await session.execute(
            update(Moment)
            .where(Moment.id == moment.id)
            .values(embedding=vec)
        )
        return True


async def generate_for_show(show: Show, show_idx: int, total_shows: int):
    print(f"[{show_idx}/{total_shows}] {show.brand} {show.season} — {show.id}")

    hls_url = await get_hls_url(show.video_id)
    if not hls_url:
        print(f"  No HLS URL — skipping")
        return 0

    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(Moment)
            .where(Moment.show_id == show.id)
            .where(Moment.embedding.is_(None))
        )
        moments = rows.scalars().all()

    if not moments:
        print(f"  All embedded already — skipping")
        return 0

    print(f"  {len(moments)} moments to embed")
    sem = asyncio.Semaphore(CONCURRENT)
    done = 0

    async def run_one(m: Moment):
        nonlocal done
        async with AsyncSessionLocal() as session:
            ok = await process_moment(session, m, hls_url, sem)
            if ok:
                await session.commit()
                done += 1
                if done % 20 == 0:
                    print(f"    {done}/{len(moments)}")

    await asyncio.gather(*[run_one(m) for m in moments])
    print(f"  ✓ {done}/{len(moments)} embedded")
    return done


async def main(show_id=None):
    async with AsyncSessionLocal() as session:
        q = select(Show).where(Show.status == "ready").where(Show.video_id.isnot(None))
        if show_id:
            q = q.where(Show.id == show_id)
        shows = (await session.execute(q)).scalars().all()

    print(f"Found {len(shows)} shows to process\n")
    total_embedded = 0
    for i, show in enumerate(shows, 1):
        n = await generate_for_show(show, i, len(shows))
        total_embedded += n

    print(f"\nDone. Total moments embedded: {total_embedded}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-id", help="Process only this show UUID")
    args = parser.parse_args()
    asyncio.run(main(show_id=args.show_id))
