"""
Fashion Archive — Confidence calibration service.

Converts a raw cosine similarity (0.0–1.0) to a calibrated integer 0–100 using
a logistic curve.  The transform is strictly monotonic, so result ORDERING and
precision@k are unchanged; only the displayed value and the suppression cutoff
change.

Logistic:   calibrate(cos) = 100 / (1 + exp(-k * (cos - x0)))

Fitted from the observed corpus distribution (Phase D baseline):
  - genuine concept matches cluster at  cos ≈ 0.08–0.11
  - brand-concept matches cluster at    cos ≈ 0.12–0.14
  - near-exact / historical hits at     cos ≈ 0.14–0.17
  - noise floor at                      cos ≈ 0.03–0.06

Parameter choices (k=40, x0=0.065):
  cos 0.04  →  27  (noise, always suppressed)
  cos 0.05  →  35  (noise)
  cos 0.065 →  50  (inflection — midpoint of the S-curve)
  cos 0.07  →  55  (just above current raw threshold)
  cos 0.09  →  73  (Relevant high end)
  cos 0.10  →  80  (Strong)
  cos 0.12  →  90  (Exact)
  cos 0.14  →  95  (Exact)
  cos 0.16  →  98  (Exact)

Display buckets (unchanged from CLAUDE.md spec):
  90–100 = "Exact match"
  75–89  = "Strong match"
  60–74  = "Relevant"
  < 60   = suppress from results (handled by SEARCH_CONFIDENCE_FLOOR)

Env overrides for tuning without a code change:
  CONF_K   — steepness  (default 40)
  CONF_X0  — inflection (default 0.065)

Floor:
  SEARCH_CONFIDENCE_FLOOR — minimum calibrated confidence returned (int, default 50).
  Results below this are dropped server-side before the response is assembled.
  Set to 60 to match the display-bucket suppression threshold exactly.
"""

import math
import os

# ── Module constants (fitted from Phase D baseline) ──────────────────────────

_DEFAULT_K  = 40.0    # steepness
_DEFAULT_X0 = 0.065   # inflection point (cos at which output == 50)

# ── Runtime parameters (env overrides) ───────────────────────────────────────

def _conf_k() -> float:
    try:
        return float(os.environ.get("CONF_K", _DEFAULT_K))
    except (TypeError, ValueError):
        return _DEFAULT_K


def _conf_x0() -> float:
    try:
        return float(os.environ.get("CONF_X0", _DEFAULT_X0))
    except (TypeError, ValueError):
        return _DEFAULT_X0


def calibrate(raw_cos: float) -> int:
    """
    Map raw cosine similarity [0, 1] → calibrated integer [0, 100].

    Strictly monotonic: if raw_a < raw_b then calibrate(raw_a) <= calibrate(raw_b).
    Clamped to [0, 100].
    """
    k  = _conf_k()
    x0 = _conf_x0()
    score = 100.0 / (1.0 + math.exp(-k * (raw_cos - x0)))
    return max(0, min(100, int(round(score))))


def confidence_floor() -> int:
    """
    Return SEARCH_CONFIDENCE_FLOOR (calibrated units, 0–100).
    Defaults to 50.  Clamped to [0, 100].
    """
    try:
        v = int(os.environ.get("SEARCH_CONFIDENCE_FLOOR", 50))
    except (TypeError, ValueError):
        v = 50
    return max(0, min(100, v))
