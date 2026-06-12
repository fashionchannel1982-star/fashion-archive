"""
Ingest 9 Chanel AW RTW shows (2016-2024) from locally downloaded MP4 files.
Show #10 (AW2526) already in DB — script patches its metadata and skips re-upload.

Run after downloads complete:
  python scripts/ingest_chanel_velocity.py

Requirements:
  - Videos in /tmp/chanel_velocity/chanel_aw20XX.mp4
  - TL index + API key in .env
  - Anthropic credits NOT required (descriptions use TL Pegasus fallback)
"""
import asyncio
import os
import sys
import uuid
import httpx
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, text
from services.database import AsyncSessionLocal, Show, Moment
from services import twelvelabs

STAGING_DIR = Path("/tmp/chanel_velocity")
TL_KEY = os.getenv("TWELVE_LABS_API_KEY")
TL_INDEX = os.getenv("TWELVE_LABS_INDEX_ID")
BASE_URL = "https://api.twelvelabs.io/v1.3"

# The 10-show dataset definition
CHANEL_SHOWS = [
    {
        "season": "AW2016",
        "season_type": "AW-RTW",
        "year": 2016,
        "show_date": "2016-03-08",
        "creative_director": "Karl Lagerfeld",
        "source": "youtube_mvp",
        "source_url": "https://www.youtube.com/watch?v=MpglcX70uO0",
        "is_cd_transition": False,
        "file": "chanel_aw2016.mp4",
    },
    {
        "season": "AW2017",
        "season_type": "AW-RTW",
        "year": 2017,
        "show_date": "2017-03-07",
        "creative_director": "Karl Lagerfeld",
        "source": "youtube_mvp",
        "source_url": "https://www.youtube.com/watch?v=C89jkwr_wXk",
        "is_cd_transition": False,
        "file": "chanel_aw2017.mp4",
    },
    {
        "season": "AW2018",
        "season_type": "AW-RTW",
        "year": 2018,
        "show_date": "2018-03-06",
        "creative_director": "Karl Lagerfeld",
        "source": "youtube_mvp",
        "source_url": "https://www.youtube.com/watch?v=WypRnmXOGWY",
        "is_cd_transition": False,
        "file": "chanel_aw2018.mp4",
    },
    {
        "season": "AW2019",
        "season_type": "AW-RTW",
        "year": 2019,
        "show_date": "2019-03-05",
        "creative_director": "Karl Lagerfeld",
        "source": "youtube_mvp",
        "source_url": "https://www.youtube.com/watch?v=dmL2lTC4oto",
        "is_cd_transition": True,   # Lagerfeld's final show; Viard took bow
        "file": "chanel_aw2019.mp4",
    },
    {
        "season": "AW2020",
        "season_type": "AW-RTW",
        "year": 2020,
        "show_date": "2020-03-03",
        "creative_director": "Virginie Viard",
        "source": "youtube_mvp",
        "source_url": "https://www.youtube.com/watch?v=impxVV_tQKg",
        "is_cd_transition": False,
        "file": "chanel_aw2020.mp4",
    },
    {
        "season": "AW2021",
        "season_type": "AW-RTW",
        "year": 2021,
        "show_date": "2021-03-09",
        "creative_director": "Virginie Viard",
        "source": "youtube_mvp",
        "source_url": "https://www.youtube.com/watch?v=yZIx2-gPTgg",
        "is_cd_transition": False,
        "file": "chanel_aw2021.mp4",
    },
    {
        "season": "AW2022",
        "season_type": "AW-RTW",
        "year": 2022,
        "show_date": "2022-03-08",
        "creative_director": "Virginie Viard",
        "source": "youtube_mvp",
        "source_url": "https://www.youtube.com/watch?v=koDtp6_tpJ0",
        "is_cd_transition": False,
        "file": "chanel_aw2022.mp4",
    },
    {
        "season": "AW2023",
        "season_type": "AW-RTW",
        "year": 2023,
        "show_date": "2023-03-07",
        "creative_director": "Virginie Viard",
        "source": "youtube_mvp",
        "source_url": "https://www.youtube.com/watch?v=aQEYmCyDWoM",
        "is_cd_transition": False,
        "file": "chanel_aw2023.mp4",
    },
    {
        "season": "AW2024",
        "season_type": "AW-RTW",
        "year": 2024,
        "show_date": "2024-03-05",
        "creative_director": "Virginie Viard",
        "source": "youtube_mvp",
        "source_url": "https://www.youtube.com/watch?v=hKB1diiMYqk",
        "is_cd_transition": False,
        "file": "chanel_aw2024.mp4",
    },
    {
        # Show #10 — already in DB as FW2526; only patch metadata
        "season": "AW2025",
        "season_type": "AW-RTW",
        "year": 2025,
        "show_date": "2025-03-04",
        "creative_director": "Chanel creative studio",
        "source": "fc_master",
        "source_url": None,
        "is_cd_transition": True,   # interregnum; Blazy debut Oct 2025
        "file": None,               # skip upload — already indexed
        "existing_season": "FW2526",
    },
]


async def upload_to_tl(file_path: Path, brand: str, season: str, year: int, show_id: str) -> str:
    """Upload a local file to Twelve Labs and return the task_id."""
    return await twelvelabs.ingest_local_file(
        str(file_path),
        metadata={"brand": brand, "season": season, "year": year, "show_id": show_id},
    )


async def wait_for_tl(task_id: str, timeout_minutes: int = 90) -> dict:
    """Poll TL until task is ready. Returns video metadata."""
    import time
    deadline = time.time() + timeout_minutes * 60
    async with httpx.AsyncClient(timeout=20) as c:
        while time.time() < deadline:
            r = await c.get(f"{BASE_URL}/tasks/{task_id}", headers={"x-api-key": TL_KEY})
            data = r.json()
            status = data.get("status", "unknown")
            if status == "ready":
                return data
            if status in ("failed", "error"):
                raise RuntimeError(f"TL task {task_id} failed: {data}")
            print(f"  TL status: {status} — waiting 30s...")
            await asyncio.sleep(30)
    raise TimeoutError(f"TL task {task_id} timed out after {timeout_minutes}m")


async def get_moments_for_show(video_id: str, brand: str, season: str) -> list:
    """Pull look timestamps from TL search and store with placeholder descriptions."""
    looks = []
    look_queries = [
        "model walking runway fashion look",
        "designer outfit catwalk",
    ]
    seen = set()
    for q in look_queries:
        clips = await twelvelabs._search_clips_for_video(video_id, q, page_limit=50)
        for clip in clips:
            bucket = round(clip["start"] / 3) * 3
            if bucket not in seen:
                seen.add(bucket)
                looks.append(clip)
    looks.sort(key=lambda c: c["start"])

    result = []
    for i, clip in enumerate(looks, 1):
        desc = f"{brand} {season} — look at {clip['start']:.0f}s"
        result.append({
            "look_number": i,
            "timestamp_start": clip["start"],
            "timestamp_end": clip["end"],
            "description": desc,
            "thumbnail_url": None,
        })
    return result


async def patch_show_metadata(session, show: Show, meta: dict):
    """Set velocity fields on an existing show record."""
    show.season_type = meta["season_type"]
    show.creative_director = meta["creative_director"]
    show.source = meta["source"]
    show.source_url = meta.get("source_url")
    show.is_cd_transition = bool(meta["is_cd_transition"])
    if meta.get("show_date"):
        show.show_date = datetime.fromisoformat(meta["show_date"]).replace(tzinfo=timezone.utc)
    await session.commit()


async def ingest_show(meta: dict) -> bool:
    """Ingest one show — upload if needed, create moments, patch metadata."""
    async with AsyncSessionLocal() as session:
        # Check if already in DB by season
        existing_season = meta.get("existing_season", meta["season"])
        result = await session.execute(
            select(Show).where(Show.brand == "Chanel").where(Show.season == existing_season)
        )
        show = result.scalar_one_or_none()

        if show and show.status == "ready":
            moments_count = (await session.execute(
                text(f"SELECT COUNT(*) FROM moments WHERE show_id='{show.id}'")
            )).scalar()
            if moments_count > 0:
                print(f"  SKIP: {meta['season']} already ready with {moments_count} moments")
                await patch_show_metadata(session, show, meta)
                return True

        # New show — need to upload and ingest
        file_path = STAGING_DIR / meta["file"] if meta["file"] else None

        if file_path and not file_path.exists():
            print(f"  MISSING file: {file_path} — skipping (download not yet complete?)")
            return False

        if not show:
            show = Show(
                id=str(uuid.uuid4()),
                brand="Chanel",
                season=meta["season"],
                year=meta["year"],
                status="queued",
                looks_count=0,
                source_url=meta.get("source_url"),
            )
            session.add(show)
            await session.commit()

        print(f"  Uploading {meta['file']} to TL...")
        try:
            task_id = await upload_to_tl(file_path, "Chanel", meta["season"], meta["year"], show.id)
            show.task_id = task_id
            show.status = "processing"
            await session.commit()
        except Exception as e:
            print(f"  UPLOAD FAILED: {e}")
            show.status = "failed"
            await session.commit()
            return False

        print(f"  Waiting for TL indexing (task {task_id})...")
        try:
            tl_data = await wait_for_tl(task_id)
            video_id = tl_data.get("video_id") or tl_data.get("_id")
            show.video_id = video_id
            show.status = "ready"
            await session.commit()
        except Exception as e:
            print(f"  TL WAIT FAILED: {e}")
            show.status = "failed"
            await session.commit()
            return False

    # Generate moments (separate session to avoid long-held transactions)
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Show).where(Show.id == show.id))
        show = result.scalar_one()

        print(f"  Extracting looks from {show.video_id}...")
        moments = await get_moments_for_show(show.video_id, "Chanel", meta["season"])
        for m_data in moments:
            m = Moment(
                id=str(uuid.uuid4()),
                show_id=show.id,
                look_number=m_data["look_number"],
                timestamp_start=m_data["timestamp_start"],
                timestamp_end=m_data["timestamp_end"],
                description=m_data["description"],
                enriched_data={"description": m_data["description"]},
            )
            session.add(m)
        show.looks_count = len(moments)
        await patch_show_metadata(session, show, meta)
        await session.commit()
        print(f"  ✓ {len(moments)} moments created")
    return True


async def main():
    print(f"Chanel Velocity Ingestion — {len(CHANEL_SHOWS)} shows\n")

    # Check which files are available
    available = []
    for meta in CHANEL_SHOWS:
        if meta["file"] is None:
            available.append(meta)
            continue
        fp = STAGING_DIR / meta["file"]
        if fp.exists():
            size_mb = fp.stat().st_size / 1e6
            print(f"  ✓ {meta['file']} ({size_mb:.0f}MB)")
            available.append(meta)
        else:
            print(f"  ✗ {meta['file']} — not found, will skip")

    print(f"\nProceeding with {len(available)} available shows\n")

    for i, meta in enumerate(available, 1):
        print(f"[{i}/{len(available)}] Chanel {meta['season']}")
        ok = await ingest_show(meta)
        print(f"  {'✓ Done' if ok else '✗ Failed'}\n")

    # Print summary
    async with AsyncSessionLocal() as session:
        rows = await session.execute(
            select(Show).where(Show.brand == "Chanel").where(Show.season_type == "AW-RTW")
        )
        shows = rows.scalars().all()
        print(f"\n=== CHANEL AW-RTW VELOCITY DATASET ({len(shows)}/10) ===")
        for s in sorted(shows, key=lambda x: x.year):
            mc = (await session.execute(
                text(f"SELECT COUNT(*) FROM moments WHERE show_id='{s.id}'")
            )).scalar()
            cd = "★CD" if s.is_cd_transition == "True" else ""
            print(f"  {s.season:10}  {s.year}  {s.status:10}  {mc:3} looks  {s.creative_director or '—'}  {cd}")


if __name__ == "__main__":
    asyncio.run(main())
