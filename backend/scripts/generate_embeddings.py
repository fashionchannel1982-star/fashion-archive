"""
Generate Marengo3.0 image embeddings for all moments.

For each moment missing an embedding or thumbnail:
  1. Get HLS URL for the parent show
  2. Extract a frame via ffmpeg at segment midpoint ((start+end)/2)
  3. POST frame to TL /embed → 512-dim vector → store in moments.embedding
  4. Save frame to static/thumbnails/{moment_id}.jpg → store URL in moments.thumbnail_url

Flags:
  --show-id UUID   Process only this show
  --force          Re-embed all moments, even those that already have embeddings
                   (use after Phase 4: existing embeddings are at start frame, not midpoint)

Run with: python scripts/generate_embeddings.py [--show-id UUID] [--force]
"""
import asyncio
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from services.database import AsyncSessionLocal, Moment, Show
from services.twelvelabs import get_hls_url, extract_frame, embed_image, save_thumbnail

CONCURRENT = 8  # embed-only is fast — can run more in parallel


async def process_moment(moment: Moment, hls_url: str, sem: asyncio.Semaphore, force_thumb: bool) -> bool:
    async with sem:
        midpoint = (moment.timestamp_start + moment.timestamp_end) / 2.0
        frame = await extract_frame(hls_url, midpoint)
        if not frame:
            return False
        vec = await embed_image(frame)
        if not vec:
            return False

        thumb_url = moment.thumbnail_url
        if not thumb_url or force_thumb:
            thumb_url = save_thumbnail(str(moment.id), frame)

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Moment)
                .where(Moment.id == moment.id)
                .values(embedding=vec, thumbnail_url=thumb_url)
            )
            await session.commit()
        return True


async def generate_for_show(show: Show, show_idx: int, total_shows: int, force: bool) -> int:
    print(f"[{show_idx}/{total_shows}] {show.brand} {show.season}")

    hls_url = await get_hls_url(show.video_id)
    if not hls_url:
        print(f"  No HLS URL — skipping")
        return 0

    async with AsyncSessionLocal() as session:
        q = select(Moment).where(Moment.show_id == show.id)
        if not force:
            q = q.where((Moment.embedding.is_(None)) | (Moment.thumbnail_url.is_(None)))
        rows = await session.execute(q)
        moments = rows.scalars().all()
        # snapshot thumbnail_url while session open
        moment_data = [(m, m.thumbnail_url) for m in moments]

    if not moment_data:
        print(f"  All embedded and thumbnailed — skipping (use --force to redo)")
        return 0

    print(f"  {len(moment_data)} moments to embed (CONCURRENT={CONCURRENT})")
    sem = asyncio.Semaphore(CONCURRENT)
    done = 0
    errors = 0
    total = len(moment_data)

    async def run_one(m: Moment, existing_thumb: str):
        nonlocal done, errors
        try:
            ok = await process_moment(m, hls_url, sem, force_thumb=force or not existing_thumb)
            if ok:
                done += 1
            else:
                errors += 1
        except Exception as e:
            errors += 1
            print(f"  ERROR {m.id}: {type(e).__name__}: {e}")
        if (done + errors) % 20 == 0:
            print(f"    {done + errors}/{total} ({errors} errors)")

    await asyncio.gather(*[run_one(m, t) for m, t in moment_data])
    print(f"  ✓ {done}/{total} embedded, {errors} errors")
    return done


async def main(show_id: str = None, force: bool = False):
    async with AsyncSessionLocal() as session:
        q = select(Show).where(Show.status == "ready").where(Show.video_id.isnot(None))
        if show_id:
            q = q.where(Show.id == show_id)
        shows = (await session.execute(q)).scalars().all()

    mode = "FORCE re-embed all" if force else "missing embeddings/thumbnails only"
    print(f"Found {len(shows)} shows — mode: {mode}\n")
    total_embedded = 0
    for i, show in enumerate(shows, 1):
        n = await generate_for_show(show, i, len(shows), force=force)
        total_embedded += n

    print(f"\nDone. Total moments embedded: {total_embedded}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-id", help="Process only this show UUID")
    parser.add_argument("--force", action="store_true",
                        help="Re-embed all moments (not just null ones) — fixes start-frame embeddings")
    args = parser.parse_args()
    asyncio.run(main(show_id=args.show_id, force=args.force))
