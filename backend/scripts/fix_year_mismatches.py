#!/usr/bin/env python3
"""
One-shot data fix: Balenciaga, Celine, Saint Laurent stored year=2026
but their season name says "Fall 2025 Ready-to-Wear" and their show_date
is 2025-02-28 to 2025-03-04.  The convention in this corpus is:
season year = year in the season NAME, not the calendar year of the show.
Fix: set year = 2025 for these three shows.

Run once:
    cd backend && python scripts/fix_year_mismatches.py
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()


async def main():
    from services.database import AsyncSessionLocal, Show
    from sqlalchemy import select, update

    fixes = [
        # (brand, season substring to match, wrong year, correct year)
        ("Balenciaga", "Fall 2025", 2026, 2025),
        ("Celine",     "Fall 2025", 2026, 2025),
        ("Saint Laurent", "Fall 2025", 2026, 2025),
    ]

    async with AsyncSessionLocal() as session:
        for brand, season_substr, wrong_year, correct_year in fixes:
            rows = (await session.execute(
                select(Show)
                .where(Show.brand == brand)
                .where(Show.year == wrong_year)
                .where(Show.season.like(f"%{season_substr}%"))
            )).scalars().all()

            if not rows:
                print(f"  – {brand}: no match (already fixed or not found)")
                continue

            for show in rows:
                print(f"  ✓ {show.brand} '{show.season}': year {show.year} → {correct_year}  (show_key: {show.show_key})")
                show.year = correct_year
                # Rebuild show_key to reflect new year (season name stays the same)
                from services.database import make_show_key
                show.show_key = make_show_key(show.brand, show.season)

        await session.commit()
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
