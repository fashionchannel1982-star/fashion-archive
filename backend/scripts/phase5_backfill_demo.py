"""
Phase 5 — Backfill descriptions, midpoint embeddings, and thumbnails.

By default targets the 3 demo shows (Chanel FW2526, Gucci FW2526, Versace AW2526).
Pass --all-shows to run the full corpus (ask user first — burns TL + Anthropic credit).

Two passes (run separately for speed):

  FAST pass — embeddings + thumbnails at midpoint only, no Pegasus:
    python scripts/phase5_backfill_demo.py --embeddings-only [--all-shows]

  SLOW pass — Pegasus descriptions for invalid/placeholder moments only:
    python scripts/phase5_backfill_demo.py --descriptions-only [--all-shows]

  COMBINED (original, slowest):
    python scripts/phase5_backfill_demo.py [--all-shows]

Rules:
  - Brand/season never sent to Pegasus — metadata supplies attribution
  - Embedding extracted at midpoint ((start+end)/2), not start frame
  - Thumbnails always saved at midpoint frame
  - Descriptions: only invalid/placeholder by default; --all-descriptions to redo all
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, update
from services.database import AsyncSessionLocal, Moment, Show
from pathlib import Path
from services.twelvelabs import (
    get_hls_url, extract_frame, embed_image, save_thumbnail,
    describe_frame_with_claude, _is_valid_description,
)

THUMB_DIR = Path(__file__).parent.parent / "static" / "thumbnails"

DEMO_SHOW_IDS = [
    "28f0c2ab-eb85-44de-82ac-36ca6fdca301",  # Chanel FW2526
    "3f8d9ef5-0cef-4483-a47b-d9bdd6c86f04",  # Gucci FW2526
    "127b8ad4-b0b3-4500-9bbf-1e3e48b89e95",  # Versace AW2526
]

CONCURRENT_EMBED = 8   # embed + frame only: fast, TL handles it fine
CONCURRENT_DESC  = 20  # Claude Haiku vision: fast, Claude API handles high concurrency


# ─── EMBEDDING + THUMBNAIL PASS (FAST) ────────────────────────────────────────

async def embed_moment(moment: Moment, hls_url: str, sem: asyncio.Semaphore) -> bool:
    async with sem:
        midpoint = (moment.timestamp_start + moment.timestamp_end) / 2.0
        frame = await extract_frame(hls_url, midpoint)
        if not frame:
            return False
        vec = await embed_image(frame)
        if not vec:
            return False
        thumb_url = save_thumbnail(str(moment.id), frame)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Moment).where(Moment.id == moment.id)
                .values(embedding=vec, thumbnail_url=thumb_url)
            )
            await session.commit()
        return True


async def embed_show(show: Show) -> dict:
    hls_url = await get_hls_url(show.video_id)
    if not hls_url:
        print(f"  No HLS URL — skipping")
        return {"total": 0, "done": 0, "errors": 0}

    async with AsyncSessionLocal() as session:
        moments = (await session.execute(
            select(Moment).where(Moment.show_id == show.id)
        )).scalars().all()

    total = len(moments)
    print(f"  {total} moments — embed at midpoint (CONCURRENT={CONCURRENT_EMBED})")
    sem = asyncio.Semaphore(CONCURRENT_EMBED)
    done = 0
    errors = 0

    async def run_one(m: Moment):
        nonlocal done, errors
        try:
            ok = await embed_moment(m, hls_url, sem)
            if ok:
                done += 1
            else:
                errors += 1
        except Exception as e:
            errors += 1
            print(f"  ERROR {m.id}: {type(e).__name__}: {e}")
        if (done + errors) % 20 == 0:
            print(f"    {done + errors}/{total}")

    await asyncio.gather(*[run_one(m) for m in moments])
    print(f"  ✓ embed: {done}/{total}, errors: {errors}")
    return {"total": total, "done": done, "errors": errors}


# ─── DESCRIPTION PASS (SLOW) ──────────────────────────────────────────────────

async def describe_moment(moment: Moment, show: Show, enriched_snapshot: dict, sem: asyncio.Semaphore) -> bool:
    async with sem:
        # Use local thumbnail — no TL call needed, Claude Haiku is faster + better
        thumb_path = THUMB_DIR / f"{moment.id}.jpg"
        if not thumb_path.exists():
            return False
        image_bytes = thumb_path.read_bytes()
        desc = await describe_frame_with_claude(image_bytes)
        if not _is_valid_description(desc):
            desc = None

        new_enriched = dict(enriched_snapshot)
        if desc:
            new_enriched["description"] = desc
        elif "description" in new_enriched:
            del new_enriched["description"]
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(Moment).where(Moment.id == moment.id)
                .values(description=desc, enriched_data=new_enriched)
            )
            await session.commit()
        return desc is not None


async def describe_show(show: Show, all_moments: bool) -> dict:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Moment).where(Moment.show_id == show.id)
        )).scalars().all()
        moment_data = [(m, dict(m.enriched_data or {})) for m in rows]

    targets = moment_data if all_moments else [
        (m, e) for m, e in moment_data if not _is_valid_description(m.description or "")
    ]

    if not targets:
        print(f"  all descriptions valid — skipping")
        return {"total": len(moment_data), "done": 0, "errors": 0}

    print(f"  {len(targets)}/{len(moment_data)} to describe (CONCURRENT={CONCURRENT_DESC})")
    sem = asyncio.Semaphore(CONCURRENT_DESC)
    done = 0
    errors = 0
    total = len(targets)

    async def run_one(m: Moment, e: dict):
        nonlocal done, errors
        try:
            ok = await describe_moment(m, show, e, sem)
            if ok:
                done += 1
            else:
                errors += 1
        except Exception as ex:
            errors += 1
            print(f"  ERROR {m.id}: {type(ex).__name__}: {ex}")
        if (done + errors) % 10 == 0:
            print(f"    {done + errors}/{total} ({errors} null)")

    await asyncio.gather(*[run_one(m, e) for m, e in targets])
    print(f"  ✓ desc: {done}/{total} updated, {errors} null")
    return {"total": len(moment_data), "done": done, "errors": errors}


# ─── MAIN ─────────────────────────────────────────────────────────────────────

async def main():
    embeddings_only   = "--embeddings-only"   in sys.argv
    descriptions_only = "--descriptions-only" in sys.argv
    all_shows         = "--all-shows"         in sys.argv
    all_descriptions  = "--all-descriptions"  in sys.argv

    async with AsyncSessionLocal() as session:
        q = select(Show).where(Show.status == "ready").where(Show.video_id.isnot(None))
        if not all_shows:
            q = q.where(Show.id.in_(DEMO_SHOW_IDS))
        shows = (await session.execute(q)).scalars().all()

    scope = "ALL shows" if all_shows else "3 demo shows"
    mode  = "embeddings+thumbnails only" if embeddings_only else \
            "descriptions only" if descriptions_only else \
            "combined (desc + embed + thumb)"
    print(f"Phase 5 backfill — {scope}, {mode}\n")

    grand = {"total": 0, "done": 0, "errors": 0}

    for show in shows:
        print(f"{show.brand} {show.season}")
        if not show.video_id:
            print("  no video_id — skipping")
            continue

        if not descriptions_only:
            stats = await embed_show(show)
            grand["total"] += stats["total"]
            grand["done"]   += stats["done"]
            grand["errors"] += stats["errors"]

        if not embeddings_only:
            stats = await describe_show(show, all_descriptions)
            grand["done"]   += stats["done"]
            grand["errors"] += stats["errors"]

        print()

    print("─" * 50)
    print(f"TOTAL  shows={len(shows)}  done={grand['done']}  errors={grand['errors']}")


if __name__ == "__main__":
    asyncio.run(main())
