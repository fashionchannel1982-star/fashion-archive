"""
Fashion Archive — Relevance Eval Harness
=========================================
Scores the live search pipeline against golden_queries.yaml.

Hits POST /api/search so the full path is exercised:
  TL embedding → pgvector cosine → confidence < threshold cut → 6A re-rank

Usage:
    cd backend
    python eval/run_eval.py                    # all rows, k=5
    python eval/run_eval.py --k 10             # top-10
    python eval/run_eval.py --validated-only   # validated rows only
    python eval/run_eval.py --server http://localhost:8001

Output:
    Prints per-query table + aggregates to stdout.
    Writes timestamped JSON to eval/results/eval_<ts>.json.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Allow running as `python eval/run_eval.py` from backend/
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


# ── Config ───────────────────────────────────────────────────────────────────

GOLDEN_PATH = Path(__file__).parent / "golden_queries.yaml"
RESULTS_DIR = Path(__file__).parent / "results"
DEFAULT_SERVER = "http://localhost:8000"


# ── Search call ──────────────────────────────────────────────────────────────

def search(query: str, limit: int, server: str) -> list[dict]:
    """POST /api/search; return the results list. Raises on non-200."""
    payload = json.dumps({"query": query, "limit": limit}).encode()
    req = urllib.request.Request(
        f"{server}/api/search",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read())
            return body.get("results", [])
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"Search returned {e.code}: {body[:200]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach {server}: {e.reason}")


# ── Metrics ──────────────────────────────────────────────────────────────────

def compute_metrics(results: list[dict], expect: list[str], k: int) -> dict:
    """
    precision@k  = |relevant ∩ top-k| / k
    recall@k     = |relevant ∩ top-k| / |relevant|
    hit@k        = 1 if any expected show appears in top-k, else 0

    'relevant' is determined by show_key match against the expect list.
    Results that lack show_key are skipped in matching.
    """
    top_k = results[:k]
    retrieved_keys = {r.get("show_key", "") for r in top_k if r.get("show_key")}

    # Fall back to brand match if show_key absent (older results)
    retrieved_brands = {r.get("brand", "").lower() for r in top_k if r.get("brand")}

    def is_relevant(expected: str) -> bool:
        if expected in retrieved_keys:
            return True
        # brand-level fallback: "chanel" matches any chanel__* show
        brand_part = expected.split("__")[0] if "__" in expected else expected
        return brand_part in retrieved_brands

    hits = sum(1 for e in expect if is_relevant(e))
    precision = hits / k if k > 0 else 0.0
    recall = hits / len(expect) if expect else 0.0
    hit_at_k = 1 if hits > 0 else 0

    return {
        "precision_at_k": round(precision, 4),
        "recall_at_k": round(recall, 4),
        "hit_at_k": hit_at_k,
        "hits": hits,
        "k": k,
        "expected_count": len(expect),
        "retrieved_keys": sorted(retrieved_keys),
    }


# ── Aggregates ───────────────────────────────────────────────────────────────

def aggregate(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    n = len(rows)
    return {
        "n": n,
        "mean_precision_at_k": round(sum(r["precision_at_k"] for r in rows) / n, 4),
        "mean_recall_at_k": round(sum(r["recall_at_k"] for r in rows) / n, 4),
        "hit_rate_at_k": round(sum(r["hit_at_k"] for r in rows) / n, 4),
    }


# ── Formatting ───────────────────────────────────────────────────────────────

def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def print_table(results: list[dict], k: int):
    header = f"{'Query':<38} {'Kind':<11} {'P@k':>5} {'R@k':>5} {'H@k':>5} {'Val':>4}"
    print(header)
    print("─" * len(header))
    for r in results:
        val = "✓" if r.get("validated") else "·"
        print(
            f"{_trunc(r['query'], 38):<38} "
            f"{r['kind']:<11} "
            f"{r['precision_at_k']:>5.2f} "
            f"{r['recall_at_k']:>5.2f} "
            f"{r['hit_at_k']:>5} "
            f"{val:>4}"
        )


def print_aggregates(label: str, agg: dict):
    if agg["n"] == 0:
        print(f"{label}: no rows")
        return
    print(
        f"{label} (n={agg['n']}):  "
        f"mean P@k={agg['mean_precision_at_k']:.3f}  "
        f"mean R@k={agg['mean_recall_at_k']:.3f}  "
        f"hit-rate={agg['hit_rate_at_k']:.3f}"
    )


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fashion Archive relevance eval")
    parser.add_argument("--k", type=int, default=5, help="Top-k to evaluate (default 5)")
    parser.add_argument("--validated-only", action="store_true",
                        help="Only score validated: true entries")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap number of queries evaluated (useful for fast smoke runs)")
    parser.add_argument("--server", default=DEFAULT_SERVER,
                        help=f"Backend URL (default {DEFAULT_SERVER})")
    parser.add_argument("--golden", default=str(GOLDEN_PATH),
                        help="Path to golden_queries.yaml")
    args = parser.parse_args()

    # Load golden set
    golden_path = Path(args.golden)
    if not golden_path.exists():
        print(f"ERROR: golden set not found at {golden_path}", file=sys.stderr)
        sys.exit(1)

    with open(golden_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    queries = data.get("queries", [])
    if args.validated_only:
        queries = [q for q in queries if q.get("validated", False)]
        print(f"Validated-only mode: {len(queries)} rows", end="")
    else:
        print(f"All rows mode: {len(queries)} total "
              f"({sum(1 for q in queries if q.get('validated', False))} validated)", end="")

    if args.limit is not None:
        queries = queries[: args.limit]
        print(f" — capped to {len(queries)} by --limit")
    else:
        print()

    if not queries:
        print("No queries to evaluate.")
        sys.exit(0)

    # Ping health
    try:
        with urllib.request.urlopen(f"{args.server}/health", timeout=5) as r:
            health = json.loads(r.read())
        print(f"Server: {args.server}  status={health.get('status')}  "
              f"version={health.get('version', '?')}\n")
    except Exception as e:
        print(f"ERROR: server not reachable at {args.server}: {e}", file=sys.stderr)
        sys.exit(1)

    # Evaluate
    row_results = []
    errors = []
    for entry in queries:
        query = entry.get("query", "")
        expect = entry.get("expect", [])
        kind = entry.get("kind", "unknown")
        validated = entry.get("validated", False)

        try:
            results = search(query, limit=args.k, server=args.server)
            m = compute_metrics(results, expect, args.k)
            row_results.append({
                "query": query,
                "kind": kind,
                "validated": validated,
                "expect": expect,
                **m,
                "top_k_shows": [r.get("show_key", r.get("brand", "?")) for r in results[:args.k]],
                "top_k_confidence": [r.get("confidence", 0) for r in results[:args.k]],
            })
        except Exception as e:
            errors.append({"query": query, "error": str(e)})
            print(f"  ERROR [{query}]: {e}", file=sys.stderr)

    if not row_results:
        print("No results to aggregate.")
        sys.exit(1)

    # Print per-query table
    print_table(row_results, args.k)
    print()

    # Aggregates — all rows
    agg_all = aggregate(row_results)
    print_aggregates(f"ALL rows  (k={args.k})", agg_all)

    # Aggregates — validated only
    validated_rows = [r for r in row_results if r.get("validated")]
    agg_val = aggregate(validated_rows)
    print_aggregates(f"VALIDATED (k={args.k})", agg_val)

    if errors:
        print(f"\n{len(errors)} query errors (see stderr)")

    # Persist JSON
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"eval_{ts}.json"
    output = {
        "run_at": ts,
        "k": args.k,
        "server": args.server,
        "golden": str(golden_path),
        "validated_only": args.validated_only,
        "aggregates": {
            "all": agg_all,
            "validated": agg_val,
        },
        "rows": row_results,
        "errors": errors,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults written → {out_path}")


if __name__ == "__main__":
    main()
