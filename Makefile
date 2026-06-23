.PHONY: preflight unit build

# Run all offline + live checks. Exits 1 on any failure.
preflight:
	@python3 backend/scripts/preflight.py

# Unit tests only (offline, fast — ~2s)
unit:
	@cd backend && python -m pytest -m unit -q

# Frontend build
build:
	@cd frontend && npm run build
