#!/usr/bin/env bash
# Fashion Archive — one-command clean start.
# Usage:  ./scripts/start.sh [--dev]
#
# What it does:
#   1. Kills any process holding ports 8000 (backend) and 3000 (frontend).
#   2. Clears .next/ so next start never serves stale chunks.
#   3. Builds the frontend (next build).
#   4. Starts the backend (uvicorn, no --reload in demo mode).
#   5. Starts the frontend (next start).
#   6. Health-checks both until ready, then prints URLs.
#
# The stale-chunk trap: `next start` loads its internal route manifest
# at process startup.  A subsequent `npm run build` replaces the chunks
# on disk, but the running process still serves the OLD manifest →
# every JS bundle request returns 404 and React never hydrates.
# The fix is structural: ALWAYS build → start, never start alone.
#
# --dev flag: starts backend with --reload (hot-reload on .py saves).
# Frontend still uses `next start` (next dev / Turbopack is broken).

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$REPO/backend"
FRONTEND="$REPO/frontend"
BACKEND_PORT=8000
FRONTEND_PORT=3000
DEV_MODE=false

for arg in "$@"; do
  [[ "$arg" == "--dev" ]] && DEV_MODE=true
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "  ${GREEN}▶${NC}  $*"; }
warn()    { echo -e "  ${YELLOW}⚠${NC}  $*"; }
fatal()   { echo -e "  ${RED}✗${NC}  $*" >&2; exit 1; }

# ── 1. Free ports ─────────────────────────────────────────────────────────────
free_port() {
  local port=$1
  local pids
  pids=$(lsof -ti :"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    warn "Killing processes on port $port: $pids"
    echo "$pids" | xargs kill -9 2>/dev/null || true
    sleep 1
  fi
}
info "Freeing ports $BACKEND_PORT and $FRONTEND_PORT..."
free_port $BACKEND_PORT
free_port $FRONTEND_PORT

# ── 2. Clear stale .next build ────────────────────────────────────────────────
info "Clearing stale .next/..."
rm -rf "$FRONTEND/.next"

# ── 3. Build frontend ─────────────────────────────────────────────────────────
info "Building frontend (next build)..."
cd "$FRONTEND"
npm run build 2>&1 | tail -4

# ── 4. Start backend ─────────────────────────────────────────────────────────
info "Starting backend (port $BACKEND_PORT)..."
VENV="$BACKEND/venv"
[[ -d "$VENV" ]] || fatal "virtualenv not found at $VENV — run: cd backend && python3 -m venv venv && pip install -r requirements.txt"
source "$VENV/bin/activate"
cd "$BACKEND"
if [[ "$DEV_MODE" == "true" ]]; then
  uvicorn main:app --port $BACKEND_PORT --reload &
else
  uvicorn main:app --port $BACKEND_PORT &
fi
BACKEND_PID=$!

# ── 5. Start frontend ─────────────────────────────────────────────────────────
info "Starting frontend (next start, port $FRONTEND_PORT)..."
cd "$FRONTEND"
npm start &
FRONTEND_PID=$!

# ── 6. Health check ───────────────────────────────────────────────────────────
wait_for() {
  local url=$1 name=$2 retries=20
  for ((i=1; i<=retries; i++)); do
    if curl -sf "$url" > /dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  fatal "$name did not become ready after ${retries}s (url: $url)"
}

info "Waiting for backend..."
wait_for "http://localhost:$BACKEND_PORT/health" "Backend"
info "Waiting for frontend..."
wait_for "http://localhost:$FRONTEND_PORT" "Frontend"

echo ""
echo -e "  ${GREEN}✓${NC}  Backend  → http://localhost:$BACKEND_PORT"
echo -e "  ${GREEN}✓${NC}  Frontend → http://localhost:$FRONTEND_PORT"
if [[ "$DEV_MODE" == "true" ]]; then
  echo -e "  ${YELLOW}DEV mode${NC}: backend hot-reload enabled (frontend requires rebuild on .tsx changes)"
fi
echo ""
echo "  Press Ctrl-C to stop both servers."

# Wait for either process to exit
wait $BACKEND_PID $FRONTEND_PID
