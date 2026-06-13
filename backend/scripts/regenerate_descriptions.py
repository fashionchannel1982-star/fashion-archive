"""
Regenerate invalid/placeholder moment descriptions using Claude Haiku vision.

Reads the local thumbnail (static/thumbnails/{moment_id}.jpg) and sends it to
Claude Haiku with a garment-only prompt — no brand/house in the prompt.
Attribution (brand, season) comes from show metadata at display time.

Why Haiku instead of Pegasus /analyze per-segment:
  - ~3s/call vs ~90-120s/call → 30-40x faster
  - CONCURRENT=20 safely → full corpus in ~2 min vs ~3.5h
  - Uses thumbnails already on disk — no TL API call needed
  - Claude produces better garment descriptions than Pegasus
  - No brand in prompt → no identification refusals (that was the old bug)

Flags:
  --all              Regenerate every moment, not just invalid ones
  --show-id UUID     Limit to a single show
  --show-ids A,B,C   Limit to comma-separated show UUIDs

Run: python scripts/regenerate_descriptions.py [--all] [--show-id UUID]
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, update
from services.database import AsyncSessionLocal, Moment, Show
from services.twelvelabs import describe_frame_with_claude, _is_valid_description

CONCURRENT = 8          # parallel calls in-flight
RPM_LIMIT  = 30         # stay under 50 RPM + 50K tokens/min (images ~1500 tok each)
THUMB_DIR = Path(__file__).parent.parent / "static" / "thumbnails"


class RateLimiter:
    """Sliding-window rate limiter: allows at most `rate` calls per 60 seconds.
    Releases the lock before sleeping so other callers can reserve their slots concurrently."""
    def __init__(self, rate: int):
        self._interval = 60.0 / rate
        self._lock = asyncio.Lock()
        self._next_slot = 0.0  # monotonic time when next call is allowed

    async def acquire(self):
        import time
        async with self._lock:
            now = time.monotonic()
            # Reserve the next available slot
            self._next_slot = max(self._next_slot, now) + self._interval
            wait = self._next_slot - self._interval - now  # time until our slot
        # Sleep OUTSIDE the lock — other callers can reserve slots in parallel
        if wait > 0:
            await asyncio.sleep(wait)


_rate_limiter = None  # RateLimiter, initialised in main()


async def describe_moment(moment: Moment, enriched: dict, sem: asyncio.Semaphore):
    async with sem:
        await _rate_limiter.acquire()
        thumb_path = THUMB_DIR / f"{moment.id}.jpg"
        if not thumb_path.exists():
            return None
        image_bytes = thumb_path.read_bytes()
        desc = await describe_frame_with_claude(image_bytes)
        return desc if _is_valid_description(desc) else None


async def regenerate_show(show: Show, all_moments: bool) -> dict:
    async with AsyncSessionLocal() as session:
        moments = (await session.execute(
            select(Moment).where(Moment.show_id == show.id)
        )).scalars().all()
        moment_data = [(m, dict(m.enriched_data or {})) for m in moments]

    targets = moment_data if all_moments else [
        (m, e) for m, e in moment_data if not _is_valid_description(m.description or "")
    ]

    if not targets:
        return {"total": len(moment_data), "updated": 0, "failed": 0}

    print(f"  {show.brand} {show.season}: {len(targets)}/{len(moment_data)} to describe")

    sem = asyncio.Semaphore(CONCURRENT)
    updated = 0
    failed = 0
    total = len(targets)

    async def process(m: Moment, enriched: dict):
        nonlocal updated, failed
        try:
            desc = await describe_moment(m, enriched, sem)
            new_enriched = dict(enriched)
            if desc:
                new_enriched["description"] = desc
            elif "description" in new_enriched:
                del new_enriched["description"]

            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(Moment).where(Moment.id == m.id).values(
                        description=desc,
                        enriched_data=new_enriched,
                    )
                )
                await session.commit()

            if desc:
                updated += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            print(f"  ERROR {m.id}: {type(e).__name__}: {e}")

        if (updated + failed) % 20 == 0 and (updated + failed) > 0:
            print(f"    {updated + failed}/{total} ({failed} null)")

    await asyncio.gather(*[process(m, e) for m, e in targets])
    print(f"  ✓ {updated} updated, {failed} null")
    return {"total": len(moment_data), "updated": updated, "failed": failed}


async def main():
    global _rate_limiter
    _rate_limiter = RateLimiter(RPM_LIMIT)
    all_moments = "--all" in sys.argv

    show_ids = None
    for i, arg in enumerate(sys.argv):
        if arg == "--show-id" and i + 1 < len(sys.argv):
            show_ids = [sys.argv[i + 1]]
        elif arg == "--show-ids" and i + 1 < len(sys.argv):
            show_ids = [s.strip() for s in sys.argv[i + 1].split(",")]

    mode = "all moments" if all_moments else "invalid/placeholder descriptions only"
    print(f"Mode: {mode} | Engine: Claude Haiku vision | CONCURRENT={CONCURRENT} | RPM limit={RPM_LIMIT}\n")

    async with AsyncSessionLocal() as session:
        q = select(Show).where(Show.status == "ready").where(Show.video_id.isnot(None))
        if show_ids:
            q = q.where(Show.id.in_(show_ids))
        shows = (await session.execute(q)).scalars().all()

    print(f"Found {len(shows)} shows\n")
    total_updated = 0
    for show in shows:
        stats = await regenerate_show(show, all_moments)
        total_updated += stats["updated"]

    print(f"\nDone. Total updated: {total_updated}")


if __name__ == "__main__":
    asyncio.run(main())
