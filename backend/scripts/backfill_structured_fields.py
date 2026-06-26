"""
Backfill structured fields (colours, garments, silhouette, key_pieces, search_tags)
for moments where enriched_data.colours is empty or missing.

Uses the existing description as input to enrich_look() — no image needed.
Writes structured fields additively; never overwrites the existing description.
Resumable: skips any moment where colours is already populated.

Run:
    python scripts/backfill_structured_fields.py [--limit N] [--show-id UUID]
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, update, text
from services.database import AsyncSessionLocal, Moment, Show
from services.claude import enrich_look

CONCURRENT = 15  # parallel Claude calls via asyncio.to_thread
RPM_LIMIT  = 90  # Sonnet tier limit is much higher; 90 is conservative


class RateLimiter:
    def __init__(self, rate: int):
        self._interval = 60.0 / rate
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            self._next_slot = max(self._next_slot, now) + self._interval
            wait = self._next_slot - self._interval - now
        if wait > 0:
            await asyncio.sleep(wait)


_rate_limiter: Optional[RateLimiter] = None


def _colours_populated(enriched: Optional[dict]) -> bool:
    if not enriched:
        return False
    colours = enriched.get("colours")
    if not colours:
        return False
    return len(colours) > 0


async def enrich_one(
    moment_id: str,
    description: str,
    show_context: dict,
    sem: asyncio.Semaphore,
) -> Optional[dict]:
    """
    enrich_look() calls the synchronous Anthropic client (client.messages.create).
    Run it in a thread so the event loop stays free for other coroutines.
    """
    async with sem:
        await _rate_limiter.acquire()
        try:
            result = await asyncio.to_thread(
                _enrich_look_sync, description, show_context
            )
            return result
        except Exception as e:
            print(f"  ERROR {moment_id}: {type(e).__name__}: {e}")
            return None


def _enrich_look_sync(description: str, show_context: dict) -> dict:
    """Sync wrapper around enrich_look for use with asyncio.to_thread."""
    import asyncio as _asyncio
    return _asyncio.run(enrich_look(raw_description=description, show_context=show_context))


async def main():
    global _rate_limiter
    _rate_limiter = RateLimiter(RPM_LIMIT)

    limit = None
    show_id_filter = None
    for i, arg in enumerate(sys.argv):
        if arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
        if arg == "--show-id" and i + 1 < len(sys.argv):
            show_id_filter = sys.argv[i + 1]

    async with AsyncSessionLocal() as session:
        # Load all ready shows for context lookup
        show_rows = (await session.execute(
            select(Show).where(Show.status == "ready")
        )).scalars().all()
        shows_by_id = {str(s.id): s for s in show_rows}

        # Load target moments — those where colours is missing or empty
        q = select(Moment).where(Moment.show_id.in_(list(shows_by_id.keys())))
        if show_id_filter:
            q = q.where(Moment.show_id == show_id_filter)
        all_moments = (await session.execute(q)).scalars().all()

    # Split: needs enrichment vs already done
    needs = []
    already_done = 0
    for m in all_moments:
        enriched = dict(m.enriched_data) if m.enriched_data else {}
        if _colours_populated(enriched):
            already_done += 1
        else:
            needs.append(m)

    if limit:
        needs = needs[:limit]

    print(f"Total moments examined: {len(all_moments)}")
    print(f"Already enriched (colours populated): {already_done}")
    print(f"Need backfill: {len(needs)}")
    if limit:
        print(f"  (limited to first {limit})")
    print()

    if not needs:
        print("Nothing to do.")
        return

    sem = asyncio.Semaphore(CONCURRENT)
    updated = 0
    failed = 0
    total = len(needs)
    start = time.monotonic()

    async def process(m: Moment):
        nonlocal updated, failed
        enriched_existing = dict(m.enriched_data) if m.enriched_data else {}
        description = m.description or enriched_existing.get("description", "")
        if not description:
            failed += 1
            return

        show = shows_by_id.get(str(m.show_id))
        if not show:
            failed += 1
            return

        show_context = {
            "brand": show.brand,
            "season": show.season,
            "year": show.year,
        }

        result = await enrich_one(str(m.id), description, show_context, sem)
        if result is None:
            failed += 1
            return

        # Merge: keep existing description, overlay structured fields only
        new_enriched = dict(enriched_existing)
        for field in ("colours", "garments", "silhouette", "key_pieces", "search_tags"):
            if field in result:
                new_enriched[field] = result[field]
        # Preserve existing description (don't let enrich_look overwrite a good one)
        if "description" in enriched_existing and enriched_existing["description"]:
            new_enriched["description"] = enriched_existing["description"]

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Moment).where(Moment.id == m.id).values(enriched_data=new_enriched)
            )
            await session.commit()

        updated += 1
        done = updated + failed
        if done % 100 == 0:
            elapsed = time.monotonic() - start
            rate = done / elapsed
            eta = (total - done) / rate if rate > 0 else 0
            print(f"  {done}/{total} done ({failed} failed) — {rate:.1f}/s — ETA {eta/60:.1f} min")

    await asyncio.gather(*[process(m) for m in needs])

    elapsed = time.monotonic() - start
    print(f"\nDone in {elapsed/60:.1f} min. Updated: {updated}, Failed: {failed}")

    # Final verification
    async with AsyncSessionLocal() as session:
        result = (await session.execute(
            text("""
                SELECT
                  COUNT(*) FILTER (WHERE enriched_data IS NOT NULL
                    AND enriched_data->>'colours' IS NOT NULL
                    AND json_array_length(enriched_data->'colours') > 0) AS colours_populated,
                  COUNT(*) AS total
                FROM moments
            """)
        )).one()
    print(f"\nPost-run: colours populated = {result.colours_populated} / {result.total}")


if __name__ == "__main__":
    asyncio.run(main())
