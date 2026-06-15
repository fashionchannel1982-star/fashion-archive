"""
Fashion Archive — Live Index Verification (Step 0)
Run before any demo. Prints shows by status, READY count, per-show
moment / embedding / enriched-data counts, and provenance completeness.

Usage:
    cd backend && source venv/bin/activate
    python scripts/verify_index.py
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, func
from services.database import AsyncSessionLocal, Show, Moment


async def main():
    async with AsyncSessionLocal() as s:
        # ── Shows by status ───────────────────────────────
        rows = (await s.execute(
            select(Show.status, func.count(Show.id)).group_by(Show.status)
        )).all()
        total_shows = sum(c for _, c in rows)
        ready = dict(rows).get("ready", 0)

        print("=" * 64)
        print("FASHION ARCHIVE — LIVE INDEX CHECK")
        print("=" * 64)
        print(f"\nShows in DB: {total_shows}")
        for status, c in sorted(rows):
            print(f"   {status:<12} {c}")
        print(f"\n>>> READY shows (the demo number): {ready}")

        # ── Per-show moment + embedding + enrichment counts ─
        print("\n" + "-" * 64)
        print(f"{'brand':<16}{'season':<10}{'moments':>8}{'embed':>7}{'enriched':>9}  prov")
        print("-" * 64)

        shows = (await s.execute(
            select(Show).order_by(Show.brand, Show.year)
        )).scalars().all()

        tot_m = tot_e = tot_en = 0
        missing_prov = []
        for sh in shows:
            m_total = (await s.execute(
                select(func.count(Moment.id)).where(Moment.show_id == sh.id)
            )).scalar() or 0
            m_embed = (await s.execute(
                select(func.count(Moment.id)).where(
                    Moment.show_id == sh.id, Moment.embedding.isnot(None))
            )).scalar() or 0
            m_enr = (await s.execute(
                select(func.count(Moment.id)).where(
                    Moment.show_id == sh.id, Moment.enriched_data.isnot(None))
            )).scalar() or 0

            prov = "".join([
                "C" if sh.creative_director else "·",
                "D" if sh.show_date else "·",
                "S" if sh.source else "·",
            ])
            if prov != "CDS":
                missing_prov.append(f"{sh.brand} {sh.season} ({prov})")

            tot_m += m_total
            tot_e += m_embed
            tot_en += m_enr
            flag = "  ⚠" if (sh.status == "ready" and m_embed == 0) else ""
            print(f"{sh.brand[:15]:<16}{sh.season[:9]:<10}{m_total:>8}{m_embed:>7}{m_enr:>9}  {prov}{flag}")

        print("-" * 64)
        print(f"{'TOTAL':<26}{tot_m:>8}{tot_e:>7}{tot_en:>9}")
        print(f"\nMoments with embeddings (searchable): {tot_e} / {tot_m}")
        print(f"Moments with enriched_data (structured): {tot_en} / {tot_m}")

        if missing_prov:
            print(f"\n⚠ Shows missing provenance fields (CD/Date/Source): {len(missing_prov)}")
            for x in missing_prov:
                print(f"   - {x}")
        else:
            print("\n✓ All shows have full provenance (CD · Date · Source).")

        print("\nLegend: prov = C(creative_director) D(show_date) S(source); '·' = missing.")
        print("⚠ on a row = status 'ready' but no embeddings — invisible to search.")


if __name__ == "__main__":
    asyncio.run(main())
