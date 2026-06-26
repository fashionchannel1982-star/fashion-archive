"""
6B.3 — Query coverage check.
Hits POST /api/search for each curated query; reports result count,
top-5 raw TL scores, and confidence < 30 cut classification.

Run: python scripts/query_coverage_check.py
Requires: uvicorn running on localhost:8000
"""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://localhost:8000"
THRESHOLD_CONF = 30  # spec suppression floor under discussion
QUERIES = [
    "sheer black eveningwear",
    "structured shoulders",
    "tweed in motion",
    "scarlet red tailoring",
    "voluminous draping",
    "monochrome minimalism",
    "red dress",
]


def search(query: str, limit: int = 20) -> dict:
    data = json.dumps({"query": query, "limit": limit}).encode()
    req = urllib.request.Request(
        f"{BASE}/api/search",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def classify(result_count: int, top_scores: list) -> str:
    if result_count == 0:
        return "COVERAGE"
    top = top_scores[0] if top_scores else 0
    if top >= 0.80:
        return "PASS"
    if top >= 0.50:
        return "THRESHOLD"
    return "COVERAGE"


def main():
    print(f"{'Query':<30} {'Results':>8} {'Top-5 raw scores':<45} {'Class'}")
    print("-" * 100)

    all_results = {}
    for q in QUERIES:
        try:
            body = search(q, limit=50)
        except Exception as e:
            print(f"{q:<30} ERROR: {e}")
            continue

        results = body.get("results", [])
        total = body.get("total", len(results))
        top5 = results[:5]
        scores = [r.get("score_raw", 0) for r in top5]
        boosts = [r.get("_boost", 0) for r in top5]
        cls = classify(total, scores)

        scores_str = "  ".join(f"{s:.3f}" for s in scores)
        print(f"{q:<30} {total:>8}  {scores_str:<45}  {cls}")
        print(f"  boosts: {boosts}")
        print(f"  brands: {[r.get('brand','?') for r in top5]}")
        print()
        all_results[q] = {"total": total, "top_scores": scores, "class": cls, "top5": top5}

    print("\n--- SUMMARY ---")
    pass_count = sum(1 for v in all_results.values() if v["class"] == "PASS")
    print(f"PASS: {pass_count}/{len(all_results)}")
    print(f"THRESHOLD: {sum(1 for v in all_results.values() if v['class'] == 'THRESHOLD')}/{len(all_results)}")
    print(f"COVERAGE: {sum(1 for v in all_results.values() if v['class'] == 'COVERAGE')}/{len(all_results)}")


if __name__ == "__main__":
    main()
