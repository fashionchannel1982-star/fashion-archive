"""
Two-tier structured-field enrichment cascade.

Tier 1 — Haiku (claude-haiku-4-5-20251001):
  Process all empty-colours/garments rows.

Tier 2 — Sonnet escalation (claude-sonnet-4-20250514):
  After the Haiku pass, build the escalation set:
    rows where EITHER
      (a) Haiku JSON parse failed (_fallback=True), OR
      (b) Haiku returned valid JSON with empty arrays AND the raw
          description contains at least one token from the
          COLOURS or GARMENTS lexicons in services/structured_match.py
  Rows that are empty with zero lexicon matches are left empty —
  genuine null-when-uncertain, do not escalate.

Resumability: always re-queries the DB for empty-colours rows, so
re-running after a mid-flight failure is safe (no double-charging).

Modes
-----
  --calibrate N   Dry-run N rows on Haiku (default 30), no DB write.
  --run           Full cascade, writes to DB.
  --limit N       Cap rows for --run.

Ledger output (after --run):
  Rows resolved by Haiku
  Rows escalated to Sonnet → resolved
  Rows left genuinely empty (with examples)
"""

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import select, update
from services.database import AsyncSessionLocal, Moment, Show
from services.structured_match import COLOURS, GARMENTS

HAIKU_MODEL   = "claude-haiku-4-5-20251001"
SONNET_MODEL  = "claude-sonnet-4-20250514"
CONCURRENCY   = 5    # concurrent calls in-flight
RPM_HAIKU     = 45   # stay under 50 RPM org cap
RPM_SONNET    = 20   # conservative for Sonnet escalation set

# Pre-compiled for lexicon hit-test on raw descriptions
class RateLimiter:
    """Sliding-window token-bucket: at most `rate` calls per 60 s.
    Reserves a slot under the lock, then sleeps outside it so other
    callers can reserve concurrently."""
    def __init__(self, rate: int) -> None:
        self._interval  = 60.0 / rate
        self._lock      = asyncio.Lock()
        self._next_slot = 0.0  # monotonic time

    async def acquire(self) -> None:
        import time
        async with self._lock:
            now = time.monotonic()
            self._next_slot = max(self._next_slot, now) + self._interval
            wait = self._next_slot - self._interval - now
        if wait > 0:
            await asyncio.sleep(wait)


_COLOUR_PAT  = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in COLOURS)  + r")\b", re.IGNORECASE
)
_GARMENT_PAT = re.compile(
    r"\b(" + "|".join(re.escape(g) for g in GARMENTS) + r")\b", re.IGNORECASE
)


def _has_lexicon_token(text: str) -> bool:
    return bool(_COLOUR_PAT.search(text) or _GARMENT_PAT.search(text))


async def fetch_empty_rows(limit: int = 0) -> list:
    """Return (Moment, Show) pairs where colours is still empty."""
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Moment, Show)
            .join(Show, Show.id == Moment.show_id)
            .where(Moment.enriched_data["colours"].as_string() == "[]")
            .order_by(Show.brand, Show.season)
        )
        if limit:
            stmt = stmt.limit(limit)
        rows = (await session.execute(stmt)).all()
    return rows


async def call_enrich(moment: Moment, show: Show, model: str,
                      sem: asyncio.Semaphore, limiter: RateLimiter) -> dict:
    from services.claude import enrich_look
    ctx = {"brand": show.brand, "season": show.season, "year": show.year}
    async with sem:
        await limiter.acquire()
        return await enrich_look(moment.description or "", ctx, model=model)


async def write_result(moment: Moment, result: dict) -> None:
    existing = dict(moment.enriched_data or {})
    merged   = {**existing, **result}
    if not merged.get("description") and existing.get("description"):
        merged["description"] = existing["description"]
    merged.pop("_fallback", None)   # sentinel — never persisted
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Moment).where(Moment.id == moment.id).values(enriched_data=merged)
        )
        await session.commit()


# ── TIER 1 — Haiku ────────────────────────────────────────────────────────────

async def tier1_haiku(rows: list, sem: asyncio.Semaphore,
                      limiter: RateLimiter) -> dict:
    """
    Phase A — gather all API results concurrently (rate-limited).
    Phase B — write to DB sequentially (avoids pool exhaustion).
    Returns outcomes dict: moment.id → (Moment, Show, result).
    """
    total    = len(rows)
    api_done = 0
    outcomes = {}

    # ── Phase A: API calls (concurrent, rate-limited) ─────────────────
    async def fetch(m: Moment, s: Show) -> None:
        nonlocal api_done
        try:
            result = await call_enrich(m, s, HAIKU_MODEL, sem, limiter)
        except Exception as e:
            print(f"  HAIKU API ERR {m.id}: {type(e).__name__}: {e}")
            result = {"colours": [], "garments": [], "_fallback": True}
        outcomes[m.id] = (m, s, result)
        api_done += 1
        if api_done % 25 == 0:
            resolved = sum(1 for _, _, r in outcomes.values() if r.get("colours"))
            print(f"  Haiku API: {api_done}/{total} resolved={resolved}")

    await asyncio.gather(*[fetch(m, s) for m, s in rows])

    # ── Phase B: sequential DB writes ─────────────────────────────────
    write_ok = 0
    write_err = 0
    for m_id, (m, s, result) in outcomes.items():
        try:
            await write_result(m, result)
            if result.get("colours"):
                write_ok += 1
        except Exception as e:
            write_err += 1
            print(f"  HAIKU WRITE ERR {m_id}: {type(e).__name__}: {e}")

    resolved = sum(1 for _, _, r in outcomes.values() if r.get("colours"))
    print(f"  Haiku pass: {resolved} resolved, {total - resolved} empty, "
          f"{write_err} write errors\n")
    return outcomes


# ── TIER 2 — Sonnet escalation ────────────────────────────────────────────────

async def tier2_sonnet(outcomes: dict, sem: asyncio.Semaphore,
                       limiter: RateLimiter) -> tuple:
    """
    Builds escalation set from Haiku outcomes.
    Returns (escalated_count, sonnet_resolved, genuine_empty list).
    """
    escalate   = []
    genuine    = []   # empty + no lexicon hit — left as-is

    for m_id, (m, s, result) in outcomes.items():
        if result.get("colours"):
            continue   # Haiku resolved it
        raw  = m.description or ""
        fallback = result.get("_fallback", False)
        hit  = _has_lexicon_token(raw)

        if fallback or hit:
            escalate.append((m, s))
        else:
            genuine.append((m, s, raw))

    if not escalate:
        print("  No escalation candidates — all empties are genuine nulls.")
        return 0, 0, genuine

    print(f"  Escalating {len(escalate)} rows to Sonnet "
          f"({sum(1 for m,s,r in outcomes.values() if r.get('_fallback'))} fallbacks + "
          f"{len(escalate) - sum(1 for m,s,r in outcomes.values() if r.get('_fallback') and _has_lexicon_token(m.description or ''))} lexicon hits)…\n")

    sonnet_resolved = 0
    sonnet_empty    = 0
    sonnet_cache    = {}  # m.id → result

    # Phase A: concurrent API calls
    async def escalate_fetch(m: Moment, s: Show) -> None:
        try:
            result = await call_enrich(m, s, SONNET_MODEL, sem, limiter)
        except Exception as e:
            print(f"  SONNET ERR {m.id}: {type(e).__name__}: {e}")
            result = {"colours": [], "garments": [], "_fallback": True}
        sonnet_cache[m.id] = (m, result)

    await asyncio.gather(*[escalate_fetch(m, s) for m, s in escalate])

    # Phase B: sequential writes
    for m_id, (m, result) in sonnet_cache.items():
        await write_result(m, result)
        if result.get("colours"):
            sonnet_resolved += 1
        else:
            sonnet_empty += 1

    print(f"  Sonnet pass done: {sonnet_resolved} resolved, {sonnet_empty} still empty")
    return len(escalate), sonnet_resolved, genuine


# ── FULL RUN ──────────────────────────────────────────────────────────────────

async def run(limit: int = 0) -> None:
    print(f"TWO-TIER CASCADE  Haiku → Sonnet escalation")
    print(f"  Tier 1: {HAIKU_MODEL}")
    print(f"  Tier 2: {SONNET_MODEL}  (escalation only)\n")

    rows = await fetch_empty_rows(limit=limit)
    if not rows:
        print("No empty-colours rows — nothing to do.")
        return

    total = len(rows)
    print(f"Rows targeted: {total}\n")

    sem            = asyncio.Semaphore(CONCURRENCY)
    haiku_limiter  = RateLimiter(RPM_HAIKU)
    sonnet_limiter = RateLimiter(RPM_SONNET)

    # Tier 1
    print("── TIER 1: Haiku ─────────────────────────────────────")
    outcomes = await tier1_haiku(rows, sem, haiku_limiter)

    haiku_resolved = sum(1 for _, _, r in outcomes.values() if r.get("colours"))

    # Tier 2
    print("── TIER 2: Sonnet escalation ─────────────────────────")
    escalated, sonnet_resolved, genuine_empty = await tier2_sonnet(outcomes, sem, sonnet_limiter)

    # ── LEDGER ────────────────────────────────────────────────────────────
    print(f"\n{'='*56}")
    print(f"LEDGER")
    print(f"  Rows targeted:                    {total}")
    print(f"  Resolved by Haiku  (Tier 1):      {haiku_resolved}")
    print(f"  Escalated to Sonnet:              {escalated}")
    print(f"    └─ Resolved by Sonnet (Tier 2): {sonnet_resolved}")
    print(f"  Left genuinely empty:             {len(genuine_empty)}")
    print(f"{'='*56}")

    if genuine_empty:
        print("\nGenuinely-empty examples (no colour/garment lexicon match):")
        for m, s, raw in genuine_empty[:5]:
            print(f"  [{s.brand} {s.season}] {raw[:100]}")


# ── CALIBRATE ─────────────────────────────────────────────────────────────────

async def calibrate(n: int = 30) -> None:
    print(f"CALIBRATION — {n} rows on Haiku, DRY RUN (no DB writes)\n")
    rows = await fetch_empty_rows(limit=n)
    if not rows:
        print("No empty-colours rows found.")
        return

    sem     = asyncio.Semaphore(CONCURRENCY)
    limiter = RateLimiter(RPM_HAIKU)
    results = []

    async def go(m: Moment, s: Show) -> None:
        result = await call_enrich(m, s, HAIKU_MODEL, sem, limiter)
        results.append({
            "brand": s.brand, "season": s.season,
            "raw": m.description or "",
            "colours":   result.get("colours", []),
            "garments":  result.get("garments", []),
            "silhouette": result.get("silhouette", ""),
            "fallback":  result.get("_fallback", False),
        })

    await asyncio.gather(*[go(m, s) for m, s in rows])

    print("=" * 72)
    shown = 0
    vague_shown = False
    for r in results:
        is_vague = len(r["raw"].split()) < 12 or not r["raw"].strip()
        if shown < 5 or (is_vague and not vague_shown):
            tag = " [VAGUE]" if is_vague else (" [FALLBACK]" if r["fallback"] else "")
            print(f"{r['brand']} {r['season']}{tag}")
            print(f"  raw:        {r['raw'][:120]}")
            print(f"  colours:    {r['colours']}")
            print(f"  garments:   {r['garments']}")
            print(f"  silhouette: {r['silhouette']}")
            print()
            shown += 1
            if is_vague:
                vague_shown = True

    with_colours = sum(1 for r in results if r["colours"])
    fallbacks    = sum(1 for r in results if r["fallback"])
    print(f"Summary: {with_colours}/{len(results)} got colours | {fallbacks} fallbacks")


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

def main() -> None:
    mode  = None
    limit = 0
    n_cal = 30

    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--calibrate":
            mode = "calibrate"
            if i + 1 < len(sys.argv) and sys.argv[i + 1].isdigit():
                n_cal = int(sys.argv[i + 1])
                i += 1
        elif arg == "--run":
            mode = "run"
        elif arg == "--limit" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
            i += 1
        i += 1

    if mode == "calibrate":
        asyncio.run(calibrate(n_cal))
    elif mode == "run":
        asyncio.run(run(limit=limit))
    else:
        print("Usage: python scripts/enrich_structured_fields.py "
              "--calibrate [N] | --run [--limit N]")
        sys.exit(1)


if __name__ == "__main__":
    main()
