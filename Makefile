.PHONY: preflight preflight-full unit build

# Fast preflight: offline checks + 3-query eval smoke (~20s, server must be running).
# make preflight FULL=1   — run the full validated eval set instead (~90s).
preflight:
	@python3 backend/scripts/preflight.py $(if $(FULL),--full,)

preflight-full:
	@python3 backend/scripts/preflight.py --full

# Unit tests only (offline, fast — ~2s)
unit:
	@cd backend && python -m pytest -m unit -q

# Frontend build
build:
	@cd frontend && npm run build
