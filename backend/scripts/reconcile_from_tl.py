"""
Reconcile DB from existing Twelve Labs index.
Creates Show + Moment records for videos already indexed in TL
without re-uploading. Safe to re-run — skips shows already in DB.
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

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from services.database import AsyncSessionLocal, Show, Moment, Provenance
from services import twelvelabs

TL_KEY = os.getenv("TWELVE_LABS_API_KEY")
TL_INDEX = os.getenv("TWELVE_LABS_INDEX_ID")
BASE_URL = "https://api.twelvelabs.io/v1.3"

# ── filename → (brand, season, year) ──────────────────────────────────────────
FILENAME_MAP = {
    # fa_upload_ convention
    "fa_upload_alexander_mcqueen_aw2526.mp4":     ("Alexander McQueen", "AW2526", 2025),
    "fa_upload_chanel_ss2526.mp4":                ("Chanel", "SS2526", 2026),
    "fa_upload_dior_aw2425.mp4":                  ("Dior", "AW2425", 2024),
    "fa_upload_dior_ss24.mp4":                    ("Dior", "SS24", 2024),
    "fa_upload_dior_ss25.mp4":                    ("Dior", "SS25", 2025),
    "fa_upload_fendi_aw2526.mp4":                 ("Fendi", "AW2526", 2025),
    "fa_upload_givenchy_aw2526.mp4":              ("Givenchy", "AW2526", 2025),
    "fa_upload_hermes_aw2526.mp4":                ("Hermès", "AW2526", 2025),
    "fa_upload_issey_miyake_aw25.mp4":            ("Issey Miyake", "AW25", 2025),
    "fa_upload_jacquemus_aw2526.mp4":             ("Jacquemus", "AW2526", 2025),
    "fa_upload_jil_sander_aw2627.mp4":            ("Jil Sander", "AW2627", 2026),
    "fa_upload_loewe_aw2526.mp4":                 ("Loewe", "AW2526", 2025),
    "fa_upload_louis_vuitton_aw2526.mp4":         ("Louis Vuitton", "AW2526", 2025),
    "fa_upload_miu_miu_aw2526.mp4":               ("Miu Miu", "AW2526", 2025),
    "fa_upload_prada_aw2526.mp4":                 ("Prada", "AW2526", 2025),
    "fa_upload_rick_owens_aw2526.mp4":            ("Rick Owens", "AW2526", 2025),
    "fa_upload_saint_laurent_aw26.mp4":           ("Saint Laurent", "AW26", 2026),
    "fa_upload_valentino_aw2526.mp4":             ("Valentino", "AW2526", 2025),
    "fa_upload_versace_aw2526.mp4":               ("Versace", "AW2526", 2025),
    "fa_upload_vivienne_westwood_aw1993.mp4":     ("Vivienne Westwood", "Fall 1993", 1993),
    "fa_upload_vivienne_westwood_aw2526.mp4":     ("Vivienne Westwood", "AW2526", 2025),
    # direct filenames
    "Balenciaga Winter 26 Collection, ClairObscur.mp4": ("Balenciaga", "Winter 26", 2026),
    "Bottega Veneta  Fall⧸Winter 2026 ｜ Milan Fashion Week.mp4": ("Bottega Veneta", "FW26", 2026),
    "Burberry ｜ Fall⧸Winter 2025⧸26 ｜ London Fashion Week.mp4": ("Burberry", "FW2526", 2025),
    "CELINE Hiver 2026 show.mp4":                 ("Celine", "Hiver 2026", 2026),
    "CHANEL - Fall ⧸ Winter 2011-2012 Full Fashion Show.mp4": ("Chanel", "FW1112", 2011),
    "CHANEL Fall 2000 Paris - Fashion Channel.mp4": ("Chanel", "Fall 2000", 2000),
    "CHANEL Fall-Winter 2024⧸25 Haute Couture Show — CHANEL Shows.mp4": ("Chanel", "Couture FW2425", 2024),
    "CHANEL Paris Spring Summer 1993 - Fashion Channel.mp4": ("Chanel", "SS1993", 1993),
    "Maison Margiela Haute Couture ｜ Fall⧸Winter 2025⧸26 ｜ Haute Couture Week - 4K.mp4": ("Maison Margiela", "Couture FW2526", 2025),
    "VIVIENNE WESTWOOD Fall 1993 Paris - Fashion Channel.mp4": ("Vivienne Westwood", "Fall 1993", 1993),
    "chanel_1080.mp4":                            ("Chanel", "FW2526", 2025),
    "gucci_1080.mp4":                             ("Gucci", "FW2526", 2025),
    "GUCCI FW 2025 2026_1080p.mp4":               ("Gucci", "FW2526", 2025),
    "chanel_fw2526_1080p.mp4":                    ("Chanel", "FW2526", 2025),
    "dior_fw2526_1080p.mp4":                      ("Dior", "FW2526", 2025),
}


async def fetch_all_tl_videos():
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{BASE_URL}/indexes/{TL_INDEX}/videos?page_limit=50",
            headers={"x-api-key": TL_KEY},
        )
        r.raise_for_status()
        return r.json().get("data", [])


async def get_existing_video_ids(session: AsyncSession) -> set:
    result = await session.execute(select(Show.video_id).where(Show.video_id.isnot(None)))
    return {row[0] for row in result.all()}


async def reconcile_video(session: AsyncSession, video: dict, existing_ids: set):
    vid_id = video["_id"]
    filename = video.get("system_metadata", {}).get("filename", "")

    meta = FILENAME_MAP.get(filename)
    if not meta:
        print(f"  SKIP (unknown filename): {filename}")
        return False

    brand, season, year = meta

    if vid_id in existing_ids:
        print(f"  SKIP (already in DB): {brand} {season}")
        return False

    print(f"  Processing: {brand} {season} [{vid_id}]")

    # Create Show record
    show_id = str(uuid.uuid4())
    show = Show(
        id=show_id,
        brand=brand,
        season=season,
        year=year,
        video_id=vid_id,
        status="processing",
        looks_count=0,
        raw_metadata={"source": "reconcile_from_tl", "filename": filename},
    )
    session.add(show)
    await session.commit()

    # Generate moments via TL
    try:
        looks = await twelvelabs.generate_look_descriptions(vid_id, brand=brand, season=season)
    except Exception as e:
        print(f"    ERROR generating looks: {e}")
        show.status = "failed"
        await session.commit()
        return False

    # Store moments
    for look in looks:
        m = Moment(
            id=str(uuid.uuid4()),
            show_id=show_id,
            look_number=look.get("look_number", 0),
            timestamp_start=look["timestamp_start"],
            timestamp_end=look["timestamp_end"],
            description=look.get("description", ""),
            thumbnail_url=look.get("thumbnail_url"),
            enriched_data={
                "description": look.get("description", ""),
                "garments": look.get("garments", []),
                "colours": look.get("colours", []),
                "silhouette": look.get("silhouette"),
                "key_pieces": look.get("key_pieces", []),
            },
        )
        session.add(m)

    show.looks_count = len(looks)
    show.status = "ready"
    await session.commit()
    print(f"    ✓ {len(looks)} moments created")
    return True


async def main():
    print(f"Reconciling from TL index: {TL_INDEX}")
    videos = await fetch_all_tl_videos()
    print(f"Found {len(videos)} videos in TL\n")

    # Deduplicate: prefer fa_upload_ named files over raw filenames
    seen_brands = {}  # (brand, season) → video
    for v in videos:
        fn = v.get("system_metadata", {}).get("filename", "")
        meta = FILENAME_MAP.get(fn)
        if not meta:
            continue
        key = (meta[0], meta[1])
        existing = seen_brands.get(key)
        if existing is None:
            seen_brands[key] = v
        else:
            # Prefer fa_upload_ names (cleaner, renamed during proper ingest)
            existing_fn = existing.get("system_metadata", {}).get("filename", "")
            if fn.startswith("fa_upload_") and not existing_fn.startswith("fa_upload_"):
                seen_brands[key] = v

    deduped = list(seen_brands.values())
    print(f"After deduplication: {len(deduped)} unique shows\n")

    async with AsyncSessionLocal() as session:
        existing_ids = await get_existing_video_ids(session)
        print(f"Already in DB: {len(existing_ids)} shows\n")

        created = 0
        for v in deduped:
            ok = await reconcile_video(session, v, existing_ids)
            if ok:
                created += 1

    print(f"\nDone. Created DB records for {created} shows.")


if __name__ == "__main__":
    asyncio.run(main())
