"""
One-shot script: normalise show metadata for consistency.

Changes made:
1. Chanel AW20XX seasons → "Fall 20XX Ready-to-Wear" (season + show_key)
2. season_type populated for all NULL rows:
     Fall/AW/FW RTW        → AW-RTW
     Spring/SS RTW         → SS-RTW
     Couture / Haute       → Couture
3. Chanel Fall 2024 season_type 'FW' → 'AW-RTW'

Safe to re-run: updates are idempotent (WHERE clauses only match the old values).
"""

import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from services.database import AsyncSessionLocal, make_show_key
from sqlalchemy import text


def _season_type_from_season(season: str) -> str:
    s = season.lower()
    if "couture" in s or "haute" in s:
        return "Couture"
    if "spring" in s or "ss" in s or "summer" in s:
        return "SS-RTW"
    if "fall" in s or "autumn" in s or "winter" in s or "aw" in s or "fw" in s:
        return "AW-RTW"
    return "AW-RTW"  # safe default for ambiguous RTW


async def main() -> None:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(
            "SELECT id, brand, season, year, season_type, show_key FROM shows ORDER BY brand, year"
        ))).fetchall()

        changes = []

        for r in rows:
            new_season = r.season
            new_season_type = r.season_type
            new_show_key = r.show_key

            # ── 1. Normalise Chanel AW20XX → "Fall 20XX Ready-to-Wear" ─────────
            aw_match = re.fullmatch(r"AW(\d{4})", r.season)
            if aw_match:
                y = int(aw_match.group(1))
                new_season = f"Fall {y} Ready-to-Wear"
                new_show_key = make_show_key(r.brand, new_season)
                new_season_type = "AW-RTW"

            # ── 2. Populate NULL season_type ─────────────────────────────────────
            if new_season_type is None:
                new_season_type = _season_type_from_season(new_season)

            # ── 3. Fix stray 'FW' → 'AW-RTW' ────────────────────────────────────
            if new_season_type == "FW":
                new_season_type = "AW-RTW"

            changed = (
                new_season != r.season
                or new_season_type != r.season_type
                or new_show_key != r.show_key
            )
            if changed:
                changes.append({
                    "id": r.id,
                    "brand": r.brand,
                    "old_season": r.season,
                    "new_season": new_season,
                    "old_season_type": r.season_type,
                    "new_season_type": new_season_type,
                    "old_key": r.show_key,
                    "new_key": new_show_key,
                })

        if not changes:
            print("Nothing to change — metadata already normalised.")
            return

        print(f"{len(changes)} show(s) to update:\n")
        for c in changes:
            print(f"  {c['brand']}")
            if c['old_season'] != c['new_season']:
                print(f"    season:      {c['old_season']!r} → {c['new_season']!r}")
            if c['old_season_type'] != c['new_season_type']:
                print(f"    season_type: {c['old_season_type']!r} → {c['new_season_type']!r}")
            if c['old_key'] != c['new_key']:
                print(f"    show_key:    {c['old_key']!r} → {c['new_key']!r}")

        confirm = input("\nApply? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

        for c in changes:
            await session.execute(text("""
                UPDATE shows
                SET season = :season, season_type = :stype, show_key = :key
                WHERE id = :id
            """), {
                "season": c["new_season"],
                "stype": c["new_season_type"],
                "key": c["new_key"],
                "id": c["id"],
            })

        await session.commit()
        print(f"\n✓ {len(changes)} show(s) updated.")


if __name__ == "__main__":
    asyncio.run(main())
