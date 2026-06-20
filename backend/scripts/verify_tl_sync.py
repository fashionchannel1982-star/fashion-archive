"""
TL ↔ DB sync verifier.

Lists all videos in the Twelve Labs index and compares against shows.video_id
for ready shows. Reports:

  (a) TL orphans  — videos in TL with no matching ready show in DB.
      These pollute index-wide search and must be deleted manually.
  (b) DB ghosts   — ready shows whose video_id is absent from TL.
      These return no results in search.

Run after every replace:
    python scripts/verify_tl_sync.py
"""

import asyncio
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

import httpx
from services.database import AsyncSessionLocal, Show
from sqlalchemy import select
import services.twelvelabs as tl

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("verify_tl_sync")


async def list_tl_videos() -> list[dict]:
    """Page through all videos in the TL index."""
    videos = []
    page = 1
    page_limit = 50
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            r = await client.get(
                f"{tl.TWELVE_LABS_BASE_URL}/indexes/{tl._get_index_id()}/videos",
                headers={"x-api-key": tl._get_api_key()},
                params={"page": page, "page_limit": page_limit},
            )
            if r.status_code != 200:
                print(f"ERROR: TL list returned {r.status_code}: {r.text[:200]}")
                break
            data = r.json()
            batch = data.get("data", [])
            videos.extend(batch)
            if len(batch) < page_limit:
                break
            page += 1
    return videos


async def check_sync() -> tuple:
    """
    Importable core: returns (orphans, ghosts) as sets of video_id strings.
    orphans — in TL but no matching ready show in DB (pollute search).
    ghosts  — ready shows whose video_id is absent from TL (return 0 results).
    """
    tl_videos = await list_tl_videos()
    tl_video_ids = {v["_id"] for v in tl_videos}

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Show.id, Show.brand, Show.season, Show.video_id, Show.show_key, Show.source)
            .where(Show.status == "ready")
        )).all()

    db_video_ids = {video_id for _, _, _, video_id, _, _ in rows if video_id}

    orphans = tl_video_ids - db_video_ids
    ghosts = db_video_ids - tl_video_ids
    return orphans, ghosts


async def main() -> None:
    print("=" * 60)
    print("TL ↔ DB SYNC REPORT")
    print("=" * 60)

    print("Fetching TL index…", end=" ", flush=True)
    tl_videos = await list_tl_videos()
    print(f"{len(tl_videos)} videos found")

    orphans, ghosts = await check_sync()

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(Show.id, Show.brand, Show.season, Show.video_id, Show.show_key, Show.source)
            .where(Show.status == "ready")
        )).all()
    db_video_map = {video_id: (sid, br, se, sk, so) for sid, br, se, video_id, sk, so in rows if video_id}

    print()
    print(f"DB ready shows with video_id: {len(db_video_map)}")
    print(f"TL index videos:              {len({v['_id'] for v in tl_videos})}")
    print()

    if orphans:
        print(f"⚠  TL ORPHANS ({len(orphans)}) — in TL index but no matching ready show:")
        print("   These pollute search. Delete with:")
        print(f"   DELETE endpoint: DELETE {tl.TWELVE_LABS_BASE_URL}/indexes/{tl.INDEX_ID}/videos/<video_id>")
        for vid in sorted(orphans):
            # Try to find matching TL video metadata
            meta = next((v for v in tl_videos if v["_id"] == vid), {})
            filename = meta.get("metadata", {}).get("filename", "")
            print(f"   ORPHAN  {vid}  {filename}")
    else:
        print("✓  No TL orphans — index clean")

    print()
    if ghosts:
        print(f"⚠  DB GHOSTS ({len(ghosts)}) — ready shows whose video_id is absent from TL:")
        print("   These will return 0 search results.")
        for vid in sorted(ghosts):
            show_id, brand, season, show_key, source = db_video_map[vid]
            print(f"   GHOST   {vid}  {brand} {season}  ({show_key})")
    else:
        print("✓  No DB ghosts — all ready shows have a TL video")

    print()
    if not orphans and not ghosts:
        print("✓  CLEAN — TL index and DB are fully in sync")
        sys.exit(0)
    else:
        print("✗  SYNC ISSUES FOUND — resolve before next replace")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
