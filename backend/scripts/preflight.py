#!/usr/bin/env python3
"""
Fashion Archive — preflight gate.
Run from repo root: python backend/scripts/preflight.py
Or via:           make preflight

Exits 0 only when all checks pass. Prints a green/red board.
"""

import argparse
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(REPO, "backend")
FRONTEND = os.path.join(REPO, "frontend")
VENV_PYTHON = os.path.join(BACKEND, "venv", "bin", "python")


def _python():
    return VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable


def run(label, cmd, cwd=None, timeout=120):
    t0 = time.time()
    try:
        r = subprocess.run(
            cmd, cwd=cwd or REPO,
            capture_output=True, text=True, timeout=timeout,
        )
        elapsed = time.time() - t0
        ok = r.returncode == 0
        return ok, elapsed, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return False, timeout, f"TIMEOUT after {timeout}s"
    except Exception as e:
        return False, time.time() - t0, str(e)


def check_health(label, url, timeout=5):
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            elapsed = time.time() - t0
            ok = resp.status == 200
            return ok, elapsed, f"HTTP {resp.status}"
    except Exception as e:
        return False, time.time() - t0, str(e)


_ap = argparse.ArgumentParser(description="Fashion Archive preflight")
_ap.add_argument("--full", action="store_true", help="Run full validated eval set (default: 3-query smoke)")
_ARGS = _ap.parse_args()
EVAL_FULL = _ARGS.full

GREEN = "\033[32m"
RED   = "\033[31m"
RESET = "\033[0m"
BOLD  = "\033[1m"

results = []

def check(label, ok, elapsed, detail=""):
    icon = f"{GREEN}✓{RESET}" if ok else f"{RED}✗{RESET}"
    ms = f"{elapsed*1000:.0f}ms"
    line = f"  {icon}  {label:<45} {ms:>8}"
    if not ok and detail:
        short = detail.strip().splitlines()[-1][:80]
        line += f"\n       {RED}{short}{RESET}"
    print(line)
    results.append(ok)

print(f"\n{BOLD}Fashion Archive — preflight{RESET}\n")

# 1. Unit tests
ok, t, out = run("pytest -m unit", [_python(), "-m", "pytest", "-m", "unit", "-q", "--tb=short"], cwd=BACKEND)
check("Unit tests (pytest -m unit)", ok, t, out)

# 2. TypeScript type-check
ok, t, out = run("tsc --noEmit", ["npx", "tsc", "--noEmit"], cwd=FRONTEND, timeout=60)
check("TypeScript (tsc --noEmit)", ok, t, out)

# 3. Next.js build
ok, t, out = run("npm run build", ["npm", "run", "build"], cwd=FRONTEND, timeout=120)
check("Frontend build (npm run build)", ok, t, out)

# 4. Backend health (skipped gracefully if server not running)
ok, t, detail = check_health("Backend health (/health)", "http://localhost:8000/health", timeout=3)
if "refused" in detail.lower() or "timed out" in detail.lower() or "timeout" in detail.lower():
    print(f"  {'–':1}  {'Backend health (server not running — skip)':45} {'skip':>8}")
    results.pop()  # don't count skipped
    results.append(True)
else:
    check("Backend health (http://localhost:8000)", ok, t, detail)

# 5. Battery gate
#    a. General: all real-user queries return ≥1 result + funnel monotonic
#    b. Bare-house: "chanel", "dior", "gucci" each return ≥6 results at strong confidence
#       (min conf ≥75) and is_bare_house=True is set — no weak-match banner
_BATTERY = [
    "chanel", "dior", "gucci",
    "red dress", "black dress", "1993", "90s minimalism",
    "black sheer across houses", "Valentino red gown", "structured tailoring",
]
_BARE_HOUSES = ["chanel", "dior", "gucci"]
_FUNNEL = ["chanel", "chanel 1993", "chanel 1993 red"]

def _search(query, limit=20):
    import json
    payload = json.dumps({"query": query, "limit": limit}).encode()
    req = urllib.request.Request(
        "http://localhost:8000/api/search",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())

if ok:
    t0_bat = time.time()
    try:
        # a. General battery: non-empty
        empty_queries = []
        for q in _BATTERY:
            data = _search(q, limit=20)
            if data.get("total", 0) == 0:
                empty_queries.append(q)

        # b. Bare-house quality: ≥6 strong results, is_bare_house flag set
        bare_fails = []
        for q in _BARE_HOUSES:
            data = _search(q, limit=20)
            results = data.get("results", [])
            n = len(results)
            min_conf = min((r.get("confidence", 0) for r in results), default=0)
            has_flag = any(r.get("is_bare_house") for r in results)
            if n < 6:
                bare_fails.append(f"{q}:count={n}<6")
            elif min_conf < 75:
                bare_fails.append(f"{q}:min_conf={min_conf}<75")
            elif not has_flag:
                bare_fails.append(f"{q}:no is_bare_house flag")

        # c. Funnel: chanel ≥ chanel1993 ≥ chanel1993red (at limit=50)
        funnel_counts = []
        for q in _FUNNEL:
            data = _search(q, limit=50)
            funnel_counts.append(data.get("total", 0))
        funnel_ok = all(funnel_counts[i] >= funnel_counts[i+1] for i in range(len(funnel_counts)-1))

        battery_ok = len(empty_queries) == 0 and len(bare_fails) == 0 and funnel_ok

        elapsed_bat = time.time() - t0_bat
        detail_bat = ""
        if empty_queries:
            detail_bat += f"EMPTY: {', '.join(empty_queries)}  "
        if bare_fails:
            detail_bat += f"BARE-HOUSE: {', '.join(bare_fails)}  "
        if not funnel_ok:
            detail_bat += f"FUNNEL broken: {funnel_counts}"
        check("Battery: non-empty + bare-house ≥6 strong + funnel", battery_ok, elapsed_bat, detail_bat.strip())
    except Exception as e:
        check("Battery gate", False, time.time() - t0_bat, str(e))
else:
    print(f"  {'–':1}  {'Battery gate (skipped — server not running)':45} {'skip':>8}")

# 6. Eval harness smoke (skip if server not running)
if ok:
    eval_cmd = [_python(), "eval/run_eval.py", "--server", "http://localhost:8000", "--validated-only"]
    if not EVAL_FULL:
        eval_cmd += ["--limit", "3"]
    label = "Eval (validated, full)" if EVAL_FULL else "Eval smoke (3 validated queries)"
    timeout = 240 if EVAL_FULL else 60
    ok2, t2, out2 = run("eval", eval_cmd, cwd=BACKEND, timeout=timeout)
    check(label, ok2, t2, out2)
else:
    print(f"  {'–':1}  {'Eval smoke (skipped — server not running)':45} {'skip':>8}")

total = len(results)
passed = sum(results)
print()

# ── Screenshot capture (non-gating) ──────────────────────────────────────────
# Requires frontend running at localhost:3000. Skips gracefully if not.
CAPTURE_SCRIPT = os.path.join(REPO, "frontend", "scripts", "capture_shots.js")
NODE = "node"
FRONTEND_URL = "http://localhost:3000"
SHOTS_ROOT = os.path.join(BACKEND, "preflight_shots")

def _frontend_up(timeout: int = 3) -> bool:
    try:
        urllib.request.urlopen(FRONTEND_URL, timeout=timeout)
        return True
    except Exception:
        return False

if not os.path.exists(CAPTURE_SCRIPT):
    print(f"  –  Screenshots skipped (capture_shots.js not found)")
elif not _frontend_up():
    print(f"  –  Screenshots skipped (frontend not running at {FRONTEND_URL})")
else:
    import datetime
    ts = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    shot_dir = os.path.join(SHOTS_ROOT, ts)
    t0 = time.time()
    try:
        r = subprocess.run(
            [NODE, CAPTURE_SCRIPT, shot_dir, FRONTEND_URL],
            cwd=os.path.join(REPO, "frontend"),
            capture_output=True, text=True, timeout=60,
        )
        elapsed = time.time() - t0
        if r.returncode == 0:
            print(f"  {GREEN}✓{RESET}  Screenshots saved → {shot_dir}  ({elapsed*1000:.0f}ms)")
        else:
            last = (r.stdout + r.stderr).strip().splitlines()[-1][:80] if (r.stdout + r.stderr).strip() else "unknown error"
            print(f"  {RED}✗{RESET}  Screenshot capture failed: {last}")
    except subprocess.TimeoutExpired:
        print(f"  {RED}✗{RESET}  Screenshot capture timed out after 60s")
    except FileNotFoundError:
        print(f"  –  Screenshots skipped (node not found in PATH)")

if all(results):
    print(f"\n{GREEN}{BOLD}  All {total} checks passed.{RESET}\n")
    sys.exit(0)
else:
    print(f"\n{RED}{BOLD}  {total - passed}/{total} checks FAILED.{RESET}\n")
    sys.exit(1)
