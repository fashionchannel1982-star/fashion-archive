.PHONY: demo dev preflight preflight-full unit build stop

# ── One-command clean start ───────────────────────────────────────────────────
# Kills stale processes on ports 3000/8000, clears .next/, rebuilds the
# frontend, then starts both servers and health-checks them.
#
# IMPORTANT: never run `next start` without a preceding `npm run build`.
# `next start` loads its route manifest at startup — a subsequent build
# replaces chunks on disk but the running process serves stale 404s,
# silently breaking all JS and leaving the page blank.  `make demo` and
# `make dev` enforce build → start atomically so this is structurally
# impossible.
demo:
	@./scripts/start.sh

dev:
	@./scripts/start.sh --dev

# ── Kill all services ─────────────────────────────────────────────────────────
stop:
	@echo "Stopping backend (8000) and frontend (3000)..."
	@lsof -ti :8000 | xargs kill -9 2>/dev/null || true
	@lsof -ti :3000 | xargs kill -9 2>/dev/null || true
	@echo "Done."

# ── Gates ─────────────────────────────────────────────────────────────────────
# Fast preflight: offline checks + 3-query eval smoke (~20s, server must be running).
# make preflight FULL=1   — run the full validated eval set instead (~90s).
preflight:
	@python3 backend/scripts/preflight.py $(if $(FULL),--full,)

preflight-full:
	@python3 backend/scripts/preflight.py --full

# Unit tests only (offline, fast — ~2s)
unit:
	@cd backend && python -m pytest -m unit -q

# ── Build only ────────────────────────────────────────────────────────────────
# Use `make demo` to build AND start. Use `make build` only when you need
# the compiled output without starting the server.
build:
	@cd frontend && npm run build
