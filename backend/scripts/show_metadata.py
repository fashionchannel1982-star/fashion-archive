"""
Read-only show metadata CLI.

Usage:
  python scripts/show_metadata.py --show-key chanel__aw2024
  python scripts/show_metadata.py --show-id <uuid>
  python scripts/show_metadata.py --all
  python scripts/show_metadata.py --csv output.csv
"""

import asyncio
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select
from services.database import AsyncSessionLocal, Show, Moment
from services.show_view import internal_metadata, client_safe_metadata


async def load_show(ident: str):
    async with AsyncSessionLocal() as session:
        show = (await session.execute(
            select(Show).where(Show.id == ident)
        )).scalar_one_or_none()
        if show is None:
            show = (await session.execute(
                select(Show).where(Show.show_key == ident)
            )).scalar_one_or_none()
        if show is None:
            return None, []

        moments = (await session.execute(
            select(Moment).where(Moment.show_id == show.id).order_by(Moment.look_number)
        )).scalars().all()

        from sqlalchemy.orm import selectinload
        show = (await session.execute(
            select(Show).where(Show.id == show.id).options(selectinload(Show.provenance))
        )).scalar_one()

    return show, list(moments)


async def load_all_shows():
    async with AsyncSessionLocal() as session:
        from sqlalchemy.orm import selectinload
        shows = (await session.execute(
            select(Show).options(selectinload(Show.provenance)).order_by(Show.brand, Show.season)
        )).scalars().all()
        # Fetch moment counts in bulk
        from sqlalchemy import func
        counts = dict((await session.execute(
            select(Moment.show_id, func.count(Moment.id)).group_by(Moment.show_id)
        )).all())
    return shows, counts


def _bar(n: int, total: int, width: int = 20) -> str:
    if total == 0:
        return "─" * width
    filled = round(width * n / total)
    return "█" * filled + "░" * (width - filled)


def print_card(meta: dict) -> None:
    """Pretty-print a full internal metadata card."""
    h = meta["health"]
    print()
    print(f"  {'─' * 56}")
    print(f"  {meta['brand']}  {meta['season']}")
    print(f"  show_key  {meta['show_key']}")
    print(f"  id        {meta['id']}")
    print(f"  {'─' * 56}")
    print(f"  status            {meta['status']}")
    print(f"  source            {meta['source']}  ({meta.get('source_url','') or ''})")
    print(f"  season_type       {meta['season_type']}")
    print(f"  year              {meta['year']}")
    print(f"  creative_director {meta['creative_director']}")
    print(f"  show_date         {meta['show_date']}")
    print(f"  is_cd_transition  {meta['is_cd_transition']}")
    print(f"  video_id          {meta['video_id']}")
    print(f"  task_id           {meta['task_id']}")
    print(f"  provenance_complete  {meta['provenance_complete']}")
    prov = meta.get("provenance")
    if prov:
        print(f"  provenance.source_name  {prov['source_name']}")
        print(f"  provenance.access_tier  {prov['access_tier']}")
    print(f"  {'─' * 56}")
    print(f"  HEALTH")
    total = h["total_moments"]
    print(f"  moments      {total}")
    print(f"  embeddings   {h['with_embedding']:4d} / {total}  {_bar(h['with_embedding'], total)}  {h['embedding_pct']}%")
    print(f"  enriched     {h['with_enriched_data']:4d} / {total}  {_bar(h['with_enriched_data'], total)}  {h['enriched_pct']}%")
    print(f"  code_tags    {h['with_code_tags']:4d} / {total}  {_bar(h['with_code_tags'], total)}  {h['code_tag_pct']}%")
    for w in h["warnings"]:
        print(f"  ⚠  {w}")
    if meta["sample_moments"]:
        print(f"  {'─' * 56}")
        print(f"  SAMPLE MOMENTS (first {len(meta['sample_moments'])})")
        for m in meta["sample_moments"]:
            desc = (m["description"] or "")[:70]
            emb = "✓" if m["has_embedding"] else "✗"
            print(f"  [{m['look_number']:3d}] {m['timestamp_start']:7.1f}s  emb:{emb}  {desc}")
    print(f"  {'─' * 56}")
    if meta.get("summary"):
        print(f"  SUMMARY")
        for line in (meta["summary"] or "").splitlines()[:4]:
            print(f"  {line}")
        print(f"  {'─' * 56}")
    print()


def print_catalogue(shows, counts: dict) -> None:
    """One line per show."""
    header = f"{'show_key':<52} {'status':<12} {'source':<12} {'CD':<28} {'moments':>7}"
    print(header)
    print("─" * len(header))
    for s in shows:
        key = s.show_key or "(no key)"
        status = s.status or ""
        src = s.source or ""
        cd = (s.creative_director or "")[:27]
        mc = counts.get(s.id, 0)
        print(f"{key:<52} {status:<12} {src:<12} {cd:<28} {mc:>7}")


def write_csv(shows, counts: dict, path: str) -> None:
    fields = [
        "show_key", "id", "brand", "season", "season_type", "year",
        "creative_director", "show_date", "source", "source_url",
        "status", "moment_count", "is_cd_transition", "has_summary",
        "provenance_source_name", "provenance_access_tier",
        "youtube_url",
    ]
    rows = []
    for s in shows:
        prov = s.provenance
        rows.append({
            "show_key": s.show_key or "",
            "id": s.id,
            "brand": s.brand,
            "season": s.season,
            "season_type": s.season_type or "",
            "year": s.year,
            "creative_director": s.creative_director or "",
            "show_date": (s.show_date.date() if hasattr(s.show_date, 'date') else s.show_date).isoformat() if s.show_date else "",
            "source": s.source or "",
            "source_url": s.source_url or "",
            "status": s.status or "",
            "moment_count": counts.get(s.id, 0),
            "is_cd_transition": s.is_cd_transition if s.is_cd_transition is not None else "",
            "has_summary": bool(s.summary),
            "provenance_source_name": prov.source_name if prov else "",
            "provenance_access_tier": prov.access_tier if prov else "",
            "youtube_url": s.youtube_url or "",
        })
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows → {path}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only show metadata CLI")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--show-key", metavar="KEY")
    group.add_argument("--show-id", metavar="UUID")
    group.add_argument("--all", action="store_true")
    group.add_argument("--csv", metavar="PATH")
    args = parser.parse_args()

    if args.show_key or args.show_id:
        ident = args.show_key or args.show_id
        show, moments = await load_show(ident)
        if show is None:
            print(f"Not found: {ident!r}", file=sys.stderr)
            sys.exit(1)
        meta = internal_metadata(show, moments)
        print_card(meta)

    elif args.all:
        shows, counts = await load_all_shows()
        print_catalogue(shows, counts)

    elif args.csv:
        shows, counts = await load_all_shows()
        write_csv(shows, counts, args.csv)


if __name__ == "__main__":
    asyncio.run(main())
