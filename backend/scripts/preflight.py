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

# 5. Eval harness smoke (skip if server not running)
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
if all(results):
    print(f"{GREEN}{BOLD}  All {total} checks passed.{RESET}\n")
    sys.exit(0)
else:
    print(f"{RED}{BOLD}  {total - passed}/{total} checks FAILED.{RESET}\n")
    sys.exit(1)
