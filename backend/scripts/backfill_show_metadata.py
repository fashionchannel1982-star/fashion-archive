"""
Fashion Archive — Show Metadata Backfill
Writes creative_director, canonical season label, and source from the
human-verified CSV source of truth.

Default: dry-run (read-only). Pass --commit to write to DB.

Usage:
    cd backend && source venv/bin/activate
    python scripts/backfill_show_metadata.py                  # dry-run
    python scripts/backfill_show_metadata.py --commit         # write to DB
    python scripts/backfill_show_metadata.py --csv /path/to/show-creative-directors.csv
"""

import asyncio
import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from sqlalchemy import select, update
from services.database import AsyncSessionLocal, Show


# ─────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────

DEFAULT_CSV = os.path.join(
    os.path.dirname(__file__),
    "../../../",
    "Claude/Projects/Fashion Archive/FA Docs/show-creative-directors.csv",
)

# DB brand → CSV brand (only aliases needed; exact matches are fine)
BRAND_ALIASES = {
    "dior":              "christian dior",
    "mcqueen":           "alexander mcqueen",
    "margiela":          "maison margiela",
    "westwood":          "vivienne westwood",
    "ysl":               "saint laurent",
    "céline":            "celine",
}


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────

def normalise_brand(db_brand: str) -> str:
    """Lowercase + apply alias map for matching."""
    lo = db_brand.lower().strip()
    return BRAND_ALIASES.get(lo, lo)


def parse_db_season(season: str, db_year: int) -> tuple[str, int]:
    """
    Returns (family, wear_year) from a messy DB season label.

    family: "Spring" | "Fall" | "Couture"
    wear_year: the year the CSV uses (e.g. AW2526 → 2025, SS2526 → 2026)

    Two-year compressed labels (XXYY, consecutive):
      - AW/FW: wear_year = 2000+XX (first / earlier year)
      - SS:    wear_year = 2000+YY (second / later year)
    Anything else: wear_year = db_year (fallback → also try db_year-1 at call site)
    """
    s = season.lower()

    # Family
    if "couture" in s:
        family = "Couture"
    elif any(k in s for k in ("ss", "spring", "printemps", "été", "ete")):
        family = "Spring"
    else:
        family = "Fall"

    # Try to find a consecutive two-digit pair  e.g. "2526", "2627", "1112", "2425"
    m = re.search(r'(\d{2})(\d{2})', season)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if b == a + 1:                          # consecutive → two-season label
            if family == "Spring":
                return family, 2000 + b        # SS: second year is the wear year
            else:
                return family, 2000 + a        # AW/FW/Couture: first year
    # Fallback: use db_year (caller will also try db_year-1 if no match)
    return family, db_year


def parse_csv_season(canon: str) -> tuple[str, int]:
    """
    Parse canonical CSV season string e.g. "Fall 2025 Ready-to-Wear".
    Returns (family, wear_year).
    """
    c = canon.lower()
    if "couture" in c:
        family = "Couture"
    elif "spring" in c:
        family = "Spring"
    else:
        family = "Fall"
    m = re.search(r'(\d{4})', canon)
    wear_year = int(m.group(1)) if m else 0
    return family, wear_year


def load_csv(path: str) -> list[dict]:
    """Load CSV rows; all verified=YES (caller trusts the file entirely)."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if r.get("verified", "").strip().upper() == "YES"]


# ─────────────────────────────────────────
# BUILD LOOKUP  brand_lower → {(family,year): row}
# ─────────────────────────────────────────

def build_csv_index(rows: list[dict]) -> dict:
    """
    Returns:
        { csv_brand_lower: { (family, wear_year): row } }
    """
    idx = {}
    for row in rows:
        brand = row["brand"].strip().lower()
        family, year = parse_csv_season(row["season"].strip())
        idx.setdefault(brand, {})[(family, year)] = row
    return idx


def _use_year_minus_one(season: str) -> bool:
    """
    Return True when the season label uses the END-year convention, meaning
    the stored year is one higher than the canonical wear year.

    Triggers:
      - French/Italian labels (Hiver, été, etc.) — always end-year
      - Labels with NO 4-digit year (e.g. "AW26", "FW26", "Winter 26", "SS25")

    Does NOT trigger for labels with a 4-digit year (e.g. "AW2016", "AW2024",
    "Fall 2000") — those express the wear year directly.
    """
    s = season.lower()
    if any(k in s for k in ("hiver", "été", "ete", "printemps", "automne")):
        return True
    # No 4-digit year in label → single 2-digit end-year style
    if not re.search(r'\d{4}', season):
        return True
    return False


def match_show(show: Show, csv_index: dict):
    """
    Try to find the CSV row for a DB show.
    Tries primary wear_year first; if label is an end-year style also tries
    primary-1. Returns the matching CSV row dict, or None.
    """
    csv_brand = normalise_brand(show.brand)
    brand_map = csv_index.get(csv_brand)
    if brand_map is None:
        return None

    family, primary_year = parse_db_season(show.season, show.year)

    candidates = [primary_year]
    if _use_year_minus_one(show.season):
        candidates.append(primary_year - 1)

    for try_year in candidates:
        row = brand_map.get((family, try_year))
        if row:
            return row
    return None


# ─────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────

async def run(csv_path: str, commit: bool):
    rows = load_csv(csv_path)
    csv_index = build_csv_index(rows)

    async with AsyncSessionLocal() as session:
        shows = (await session.execute(
            select(Show).order_by(Show.brand, Show.year)
        )).scalars().all()

    # ── Dry-run table ─────────────────────────────────────────────────────────
    W = 22  # column width

    print("=" * 90)
    print("BACKFILL DRY-RUN — show-creative-directors.csv  →  shows table")
    print("=" * 90)
    print(f"{'DB brand':<{W}} {'DB season':<{W}} {'→ CSV season':<{W}} {'→ CD'}")
    print("-" * 90)

    matched_db_show_ids: set[str] = set()
    matched_csv_keys: set[tuple] = set()   # (brand_lower, family, year)
    updates: list[dict] = []

    for sh in shows:
        row = match_show(sh, csv_index)
        if row:
            csv_brand = row["brand"].strip()
            canon_season = row["season"].strip()
            cd = row["creative_director"].strip()

            matched_db_show_ids.add(sh.id)
            csv_brand_lo = csv_brand.lower()
            f, y = parse_csv_season(canon_season)
            matched_csv_keys.add((csv_brand_lo, f, y))

            # Flag 0-moment shows
            zero_flag = " ⚠ 0 moments" if sh.looks_count == 0 else ""
            cd_changed = "  ← overwrites" if (sh.creative_director and sh.creative_director != cd) else ""
            print(f"  ✓ {sh.brand:<{W}} {sh.season:<{W}} {canon_season:<{W}} {cd}{cd_changed}{zero_flag}")

            updates.append({
                "id": sh.id,
                "creative_director": cd,
                "season": canon_season,
                "source": sh.source or "youtube_mvp",
            })
        else:
            # No CSV match — flag it
            existing_cd = sh.creative_director or "—"
            print(f"  ⚠ {sh.brand:<{W}} {sh.season:<{W}} {'(no CSV match)':<{W}} keeping CD={existing_cd!r}")

    # ── CSV rows that matched no DB show ──────────────────────────────────────
    print()
    print("── CSV rows with NO matching DB show ──────────────────────────────────")
    any_unmatched_csv = False
    for row in rows:
        csv_brand_lo = row["brand"].strip().lower()
        canon = row["season"].strip()
        f, y = parse_csv_season(canon)
        if (csv_brand_lo, f, y) not in matched_csv_keys:
            any_unmatched_csv = True
            print(f"  ⚠ {row['brand']} | {canon} | {row['creative_director']}")
    if not any_unmatched_csv:
        print("  (none — all CSV rows matched a DB show)")

    # ── Summary ───────────────────────────────────────────────────────────────
    unmatched_db = [sh for sh in shows if sh.id not in matched_db_show_ids]
    print()
    print(f"Matched:        {len(updates)} DB shows → CSV rows")
    print(f"DB no-match:    {len(unmatched_db)} shows (CD left untouched)")
    print(f"CSV no-match:   {sum(1 for r in rows if (r['brand'].strip().lower(), *parse_csv_season(r['season'].strip())) not in matched_csv_keys)} rows")
    print()

    if not commit:
        print("Dry-run complete. Pass --commit to write changes.")
        return

    # ── Commit ───────────────────────────────────────────────────────────────
    print("Writing to DB…")
    async with AsyncSessionLocal() as session:
        for u in updates:
            await session.execute(
                update(Show)
                .where(Show.id == u["id"])
                .values(
                    creative_director=u["creative_director"],
                    season=u["season"],
                    source=u["source"],
                )
            )
        await session.commit()

    print(f"Done. Updated {len(updates)} shows.")


def main():
    parser = argparse.ArgumentParser(description="Backfill show metadata from CSV source of truth.")
    parser.add_argument("--commit", action="store_true", help="Write changes to DB (default: dry-run)")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Path to show-creative-directors.csv")
    args = parser.parse_args()

    csv_path = os.path.expanduser(args.csv)
    if not os.path.exists(csv_path):
        # Try resolving relative to home
        alt = os.path.join(os.path.expanduser("~"), "Claude/Projects/Fashion Archive/FA Docs/show-creative-directors.csv")
        if os.path.exists(alt):
            csv_path = alt
        else:
            print(f"ERROR: CSV not found at {csv_path}\nPass --csv /path/to/show-creative-directors.csv")
            sys.exit(1)

    print(f"CSV: {csv_path}")
    print(f"Mode: {'COMMIT' if args.commit else 'DRY-RUN'}\n")
    asyncio.run(run(csv_path, args.commit))


if __name__ == "__main__":
    main()
