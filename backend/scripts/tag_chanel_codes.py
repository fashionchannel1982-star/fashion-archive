"""
Tag Chanel house codes on all AW-RTW velocity moments.

Strategy:
  1. Text keyword match on any non-placeholder descriptions (fast, no API cost)
  2. TL visual search per code per show (works without Claude credits)
     — searches within each show's video for visual signatures of each code
     — timestamps that overlap with moments get tagged

House codes:
  tweed       — tweed / bouclé jacket, suit, coat
  two_tone    — two-tone colorblocking, cap-toe shoes
  camellia    — camellia flower motif
  pearls      — pearls (necklace, trim, embellishment)
  chains      — chain belt, bag strap, chain trim
  quilting    — quilted fabric

Run: python scripts/tag_chanel_codes.py [--tl-search] [--text-only]
     --tl-search  : use TL visual search for codes (default: text + TL)
     --text-only  : skip TL search, text matching only
"""
import asyncio
import re
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, text
from services.database import AsyncSessionLocal, Show, Moment
from services.twelvelabs import _search_clips_for_video

# ── Text patterns (applied when descriptions exist) ───────────────────────────
CODE_PATTERNS = {
    "tweed":    re.compile(r"tweed|bouclé|boucle|woven jacket|wool jacket|check jacket|plaid jacket|tweed suit|bouclé coat|textured jacket", re.I),
    "two_tone": re.compile(r"two[- ]tone|bi[- ]colour|bicolor|colour.{0,20}block|contrasting toe|cap toe|color block|colour contrast", re.I),
    "camellia": re.compile(r"camellia|camelia", re.I),
    "pearls":   re.compile(r"\bpearl\b|pearled|pearl trim|pearl necklace|pearl embellish|pearl detail", re.I),
    "chains":   re.compile(r"\bchain\b|gold chain|chain belt|chain strap|chain trim|chain detail|chain necklace", re.I),
    "quilting": re.compile(r"quilt|quilted|diamond stitch|diamond pattern fabric|diamond quilting", re.I),
}

# ── TL visual search queries per code ─────────────────────────────────────────
CODE_QUERIES = {
    "tweed":    ["tweed jacket Chanel", "bouclé jacket", "textured woven jacket runway", "check wool jacket fashion"],
    "two_tone": ["two tone shoes Chanel", "cap toe slingback shoes", "color block outfit runway", "contrasting trim jacket"],
    "camellia": ["camellia flower brooch Chanel", "flower pin lapel", "floral embellishment jacket"],
    "pearls":   ["pearl necklace runway", "pearl embellishment", "pearl trim outfit", "layered pearl jewellery"],
    "chains":   ["chain belt waist", "chain bag strap", "gold chain necklace runway", "interlocking chain detail"],
    "quilting": ["quilted bag Chanel", "quilted fabric jacket", "diamond quilted pattern", "quilted coat"],
}

CHANEL_CODES = list(CODE_PATTERNS.keys())


def text_detect(description: str) -> dict:
    if not description or "look at" in description:  # skip placeholders
        return {}
    return {c: bool(p.search(description)) for c, p in CODE_PATTERNS.items()}


async def tl_search_tags(show: Show, video_id: str) -> dict[str, set]:
    """
    For each house code, run TL visual searches within this video.
    Returns {code: set_of_timestamp_buckets} where a hit was found.
    """
    code_timestamps: dict[str, set] = {c: set() for c in CHANEL_CODES}
    for code, queries in CODE_QUERIES.items():
        for q in queries:
            clips = await _search_clips_for_video(video_id, q, page_limit=30)
            for clip in clips:
                # Bucket to nearest 5-second window
                bucket = round(clip["start"] / 5) * 5
                code_timestamps[code].add(bucket)
        await asyncio.sleep(0.5)  # rate limit courtesy
    return code_timestamps


def moment_overlaps(m: Moment, timestamps: set) -> bool:
    """True if any TL hit timestamp falls within this moment's window."""
    for ts in timestamps:
        if m.timestamp_start - 5 <= ts <= m.timestamp_end + 5:
            return True
    return False


async def tag_show(show: Show, use_tl: bool) -> dict:
    async with AsyncSessionLocal() as session:
        moments = (await session.execute(
            select(Moment).where(Moment.show_id == show.id)
        )).scalars().all()

    if not moments:
        return {"total": 0, "hits": {c: 0 for c in CHANEL_CODES}}

    # TL visual search (once per show, not per moment)
    tl_hits: dict[str, set] = {c: set() for c in CHANEL_CODES}
    if use_tl and show.video_id:
        print(f"    Running TL visual search for {show.season}...")
        tl_hits = await tl_search_tags(show, show.video_id)
        for code, ts_set in tl_hits.items():
            print(f"      {code}: {len(ts_set)} visual hits")

    hit_counts = {c: 0 for c in CHANEL_CODES}
    async with AsyncSessionLocal() as session:
        for m in moments:
            tags: dict[str, bool] = {}
            # Text detection first
            text_tags = text_detect(m.description or "")
            for code in CHANEL_CODES:
                from_text = text_tags.get(code, False)
                from_tl = moment_overlaps(m, tl_hits[code]) if use_tl else False
                tags[code] = from_text or from_tl

            # Re-fetch moment in this session and update
            m_db = await session.get(Moment, m.id)
            if m_db:
                m_db.code_tags = tags
                for code, hit in tags.items():
                    if hit:
                        hit_counts[code] += 1

        await session.commit()

    return {"total": len(moments), "hits": hit_counts}


async def main(use_tl: bool):
    async with AsyncSessionLocal() as session:
        shows = (await session.execute(
            select(Show)
            .where(Show.brand == "Chanel")
            .where(Show.season_type == "AW-RTW")
            .where(Show.status == "ready")
            .order_by(Show.year)
        )).scalars().all()

    if not shows:
        print("No Chanel AW-RTW shows ready. Run ingest_chanel_velocity.py first.")
        return

    mode = "TL visual + text" if use_tl else "text-only"
    print(f"Tagging {len(shows)} shows ({mode})\n")

    report_rows = []
    for show in shows:
        print(f"  {show.season} ({show.looks_count} looks)...")
        stats = await tag_show(show, use_tl=use_tl)
        codes_str = "  ".join(f"{c}:{stats['hits'][c]}" for c in CHANEL_CODES)
        print(f"    → {codes_str}")
        report_rows.append((show.season, show.year, stats))

    # ── Summary report ───────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("CHANEL HOUSE CODE DETECTION REPORT")
    print("=" * 90)
    hdr = f"{'Season':10}  {'Looks':>5}  " + "  ".join(f"{c:>9}" for c in CHANEL_CODES)
    print(hdr)
    print("-" * 90)
    for season, year, stats in report_rows:
        total = stats["total"]
        row = f"{season:10}  {total:>5}  "
        for c in CHANEL_CODES:
            h = stats["hits"][c]
            pct = h / total * 100 if total else 0
            row += f"  {h:>3}({pct:>3.0f}%)"
        print(row)

    print("\nPER-CODE PRESENCE (across all 10 shows):")
    all_totals = sum(s["total"] for _, _, s in report_rows)
    weak_shows = []
    for code in CHANEL_CODES:
        total_hits = sum(s["hits"][code] for _, _, s in report_rows)
        shows_with_any = sum(1 for _, _, s in report_rows if s["hits"][code] > 0)
        pct_overall = total_hits / all_totals * 100 if all_totals else 0
        flag = "" if shows_with_any >= 8 else f"  ⚠ only {shows_with_any}/10 shows"
        if shows_with_any < 5:
            weak_shows.append(code)
        print(f"  {code:12}  {total_hits:4} hits ({pct_overall:.1f}% of looks)  in {shows_with_any}/10 shows{flag}")

    if weak_shows:
        print(f"\n⚠ Weak detection (< 5/10 shows): {', '.join(weak_shows)}")
        print("  → Top up Anthropic credits and run regenerate_descriptions.py to improve text coverage")
        print("  → Or re-run with --tl-search for visual-only detection")

    print("\nDone — code_tags populated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(use_tl=not args.text_only))
