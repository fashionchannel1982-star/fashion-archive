"""
Fashion Archive — Demo Query Coverage Probe
Read-only. Hits /api/search for the pinned demo query set and reports:
  - result count
  - min / median / max confidence (calibrated)
  - whether synthesis fired (non-null synthesis line)
  - whether top-5 results have non-null provenance
    (brand / season / year / show_date / creative_director / source)
  - raw cosine distribution for the top-20 (via --raw flag or --baseline)

Usage:
    cd backend
    python scripts/coverage_probe.py
    python scripts/coverage_probe.py --server http://localhost:8001
    python scripts/coverage_probe.py --limit 20   # default 8
    python scripts/coverage_probe.py --baseline   # write pre-calibration JSON snapshot
    python scripts/coverage_probe.py --raw        # include raw score_raw in table
"""

import argparse
import json
import os
import statistics
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── Pinned demo query set ────────────────────────────────────────────────────

HERO_QUERIES = [
    "sheer black evening looks",
    "structured shoulders, sharp tailoring",
    "monochrome white, head to toe",
]

BRAND_QUERIES = [
    "maximalist print colour runway",   # Phase D.5: swapped from "Gucci, bold colour and print" — LOCKED
    "Chanel tweed and tailoring",
    "Dior structured tailoring",       # Phase D.6b: swapped from "Dior, romantic and sheer" — LOCKED (raw_max=0.093, Strong)
]

ATTRIBUTE_QUERIES = [
    "red dress",
]

VERIFY_THEN_KEEP = [
    "a model pausing at the end of the runway",
    "Chanel 1993",
]

ALL_QUERIES = (
    [(q, "hero")      for q in HERO_QUERIES]
    + [(q, "brand")   for q in BRAND_QUERIES]
    + [(q, "attr")    for q in ATTRIBUTE_QUERIES]
    + [(q, "verify")  for q in VERIFY_THEN_KEEP]
)

# ── Provenance fields we require on every result card ────────────────────────

PROV_FIELDS = ["brand", "season", "year", "show_date", "creative_director", "source"]


def search(query: str, limit: int, server: str) -> dict:
    payload = json.dumps({"query": query, "limit": limit}).encode()
    req = urllib.request.Request(
        f"{server}/api/search",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def median(values):
    if not values:
        return None
    return round(statistics.median(values), 1)


def check_provenance(result: dict):
    """Return list of missing provenance field names."""
    missing = []
    for f in PROV_FIELDS:
        if not result.get(f):
            missing.append(f)
    return missing


def probe_query(query: str, kind: str, limit: int, server: str, include_raw: bool = False) -> dict:
    try:
        body = search(query, limit, server)
    except Exception as e:
        return {"query": query, "kind": kind, "error": str(e)}

    results = body.get("results", [])
    synthesis = body.get("synthesis")
    confidences = [r.get("confidence", 0) for r in results]
    raw_scores = [r.get("score_raw", 0) for r in results] if include_raw else []

    top5 = results[:5]
    prov_ok = []
    prov_gaps = []
    for r in top5:
        missing = check_provenance(r)
        if missing:
            prov_gaps.append({"show": f"{r.get('brand','?')} {r.get('season','?')}", "missing": missing})
        else:
            prov_ok.append(r.get("brand", "?"))

    row = {
        "query": query,
        "kind": kind,
        "count": len(results),
        "conf_min": min(confidences) if confidences else None,
        "conf_median": median(confidences),
        "conf_max": max(confidences) if confidences else None,
        "synthesis_fired": bool(synthesis),
        "synthesis_preview": (synthesis or "")[:80] if synthesis else None,
        "top5_prov_ok": len(prov_ok),
        "top5_prov_gaps": prov_gaps,
    }
    if include_raw:
        row["raw_scores"] = raw_scores
        row["raw_min"] = round(min(raw_scores), 5) if raw_scores else None
        row["raw_max"] = round(max(raw_scores), 5) if raw_scores else None
    return row


def print_report(rows: list):
    SEP = "─" * 78
    print(SEP)
    print(f"{'QUERY':<42} {'KIND':<7} {'N':>3}  {'MIN':>4} {'MED':>4} {'MAX':>4}  SYN  PROV")
    print(SEP)

    gaps_summary = []
    verify_verdicts = []

    for r in rows:
        if "error" in r:
            print(f"  ERROR [{r['query']}]: {r['error']}")
            continue

        syn = "✓" if r["synthesis_fired"] else "·"
        prov_ok = r["top5_prov_ok"]
        prov_total = min(r["count"], 5)
        prov_str = f"{prov_ok}/{prov_total}"

        print(
            f"  {r['query'][:42]:<42} {r['kind']:<7} {r['count']:>3}"
            f"  {(r['conf_min'] or 0):>4.0f} {(r['conf_median'] or 0):>4.0f} {(r['conf_max'] or 0):>4.0f}"
            f"  {syn:<4} {prov_str}"
        )

        if r["top5_prov_gaps"]:
            gaps_summary.append((r["query"], r["top5_prov_gaps"]))

        if r["kind"] == "verify":
            verdict = "KEEP" if r["count"] > 0 else "DROP"
            verify_verdicts.append((r["query"], verdict, r["count"]))

    print(SEP)

    # Verify-then-keep verdicts
    if verify_verdicts:
        print("\nVERIFY-THEN-KEEP verdicts:")
        for q, verdict, n in verify_verdicts:
            print(f"  [{verdict}] '{q}'  →  {n} results")

    # Provenance gaps
    if gaps_summary:
        print("\nProvenance gaps in top-5:")
        for query, gaps in gaps_summary:
            print(f"  '{query}':")
            for g in gaps:
                print(f"    {g['show']}: missing {g['missing']}")
    else:
        print("\nProvenance: no gaps in top-5 results ✓")


def main():
    parser = argparse.ArgumentParser(description="FA demo query coverage probe")
    parser.add_argument("--server", default="http://localhost:8000")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--raw", action="store_true", help="Include raw score_raw in output")
    parser.add_argument("--baseline", action="store_true",
                        help="Write a timestamped JSON snapshot to eval/results/")
    args = parser.parse_args()

    # Health check
    try:
        with urllib.request.urlopen(f"{args.server}/health", timeout=5) as r:
            health = json.loads(r.read())
        print(f"Server: {args.server}  status={health.get('status')}  "
              f"version={health.get('version', '?')}\n")
    except Exception as e:
        print(f"ERROR: server not reachable: {e}")
        raise SystemExit(1)

    rows = []
    for query, kind in ALL_QUERIES:
        row = probe_query(query, kind, args.limit, args.server, include_raw=args.raw or args.baseline)
        rows.append(row)

    print_report(rows)

    if args.baseline:
        out_dir = Path(__file__).parent.parent / "eval" / "results"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = out_dir / f"coverage_baseline_{ts}.json"
        snapshot = {
            "snapshot_at": ts,
            "server": args.server,
            "limit": args.limit,
            "rows": rows,
        }
        with open(out_path, "w") as f:
            json.dump(snapshot, f, indent=2)
        print(f"\nBaseline snapshot → {out_path}")


if __name__ == "__main__":
    main()
