# Fashion Archive

Semantic search over runway video footage. Type a natural language query — "black structured shoulders", "Dior navy tailoring" — and get back ranked video moments with thumbnails, provenance, and AI-generated descriptions.

**Corpus:** 42 shows, 18 houses, 1993–2026, 3,280 moments.

---

## One-command run

```bash
make demo
```

Kills any running servers, clears the stale Next.js build, compiles fresh, starts both backend and frontend, and health-checks both. Takes ~20s.

Then open http://localhost:3000.

---

## Other commands

```bash
make dev        # same as demo + backend --reload (hot-reload Python)
make stop       # kill both servers
make preflight  # full gate suite: unit tests, tsc, build, battery, eval smoke, render
make unit       # offline unit tests only (~2s, no servers needed)
```

---

## Requirements

- Python 3.9, PostgreSQL 15, Node 18+
- `pgvector` extension installed (`CREATE EXTENSION vector;`)
- `.env` file in `backend/` (copy from `backend/.env.example`)

```bash
# First-time setup
createdb fashion_archive
cd backend && pip install -r requirements.txt
alembic upgrade head
cd ../frontend && npm install
```

---

## Environment variables

```
# backend/.env
TWELVE_LABS_API_KEY=
TWELVE_LABS_INDEX_ID=
ANTHROPIC_API_KEY=
DATABASE_URL=postgresql://localhost/fashion_archive
CORS_ORIGINS=http://localhost:3000
DISABLE_DOCS=0          # set to 1 for any non-local deployment
```

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for system design, data-flow, and risk assessment.
See [ARCHITECTURE-REVIEW.md](ARCHITECTURE-REVIEW.md) for the honest pre-handoff health report.
