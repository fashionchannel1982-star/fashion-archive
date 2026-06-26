"""
Eval regression guard — pytest -m eval
=======================================
Runs the validated golden set and asserts mean precision@k ≥ a floor.

NOT part of the unit tier (no offline gate).
NOT part of the integration default run.
Only run deliberately:

    cd backend && pytest -m eval -v

Floor is intentionally unset until the first validated baseline is locked.
Set EVAL_PRECISION_FLOOR in the environment or edit the constant below.

Requires:
  - backend running at localhost:8000
  - TWELVE_LABS_API_KEY set (or the sync test skips)
  - eval/golden_queries.yaml with at least one validated: true row
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Floor — set this after first validated baseline is locked ────────────────
# Leave as None to make the test a no-op floor check (still measures + prints).
EVAL_PRECISION_FLOOR: float = None  # e.g. 0.30 after baseline is set


K = 5
SERVER = os.getenv("EVAL_SERVER", "http://localhost:8000")


def _backend_up() -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"{SERVER}/health", timeout=3):
            return True
    except Exception:
        return False


@pytest.mark.eval
def test_validated_precision_floor():
    """
    Runs validated golden queries through the live search pipeline and
    asserts mean precision@k ≥ EVAL_PRECISION_FLOOR.

    Skips if: backend down, no validated rows, or floor not yet set.
    """
    if not _backend_up():
        pytest.skip(f"backend not running at {SERVER}")

    try:
        import yaml
    except ImportError:
        pytest.skip("pyyaml not installed (pip install pyyaml)")

    golden_path = Path(__file__).parent / "golden_queries.yaml"
    with open(golden_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    validated = [q for q in data.get("queries", []) if q.get("validated", False)]
    if not validated:
        pytest.skip("No validated: true rows in golden_queries.yaml — validate some first")

    if EVAL_PRECISION_FLOOR is None:
        pytest.skip("EVAL_PRECISION_FLOOR not set — set it after first validated baseline")

    # Import scorer internals directly (avoids subprocess)
    from eval.run_eval import search, compute_metrics, aggregate

    rows = []
    for entry in validated:
        results = search(entry["query"], limit=K, server=SERVER)
        m = compute_metrics(results, entry.get("expect", []), K)
        rows.append(m)

    agg = aggregate(rows)
    mean_p = agg["mean_precision_at_k"]

    print(f"\nEval result: mean P@{K}={mean_p:.3f}  floor={EVAL_PRECISION_FLOOR:.3f}  n={agg['n']}")

    assert mean_p >= EVAL_PRECISION_FLOOR, (
        f"Precision regression: mean P@{K}={mean_p:.3f} < floor={EVAL_PRECISION_FLOOR:.3f}. "
        f"Run python eval/run_eval.py --validated-only to investigate."
    )
