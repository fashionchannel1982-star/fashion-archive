# Fashion Archive — Start Here

## Before anything else: fill in your API keys

Open `backend/.env` and fill in these four values:

```
TWELVE_LABS_API_KEY=        ← from https://api.twelvelabs.io → API Keys
TWELVE_LABS_INDEX_ID=       ← leave blank for now, filled after first ingest
ANTHROPIC_API_KEY=          ← from https://console.anthropic.com → API Keys  
DATABASE_URL=postgresql://localhost/fashion_archive
```

AWS keys are optional — leave blank for MVP.

---

## Terminal window 1 — Database

```bash
# Install PostgreSQL if you don't have it
brew install postgresql@15
brew services start postgresql@15

# Create the database
createdb fashion_archive

# Run the schema
cd ~/Desktop/fashion-archive
python3 backend/scripts/init_db.py
```

---

## Terminal window 2 — Backend

```bash
cd ~/Desktop/fashion-archive/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Apply any pending DB migrations (run this every time you pull)
python -m alembic upgrade head

uvicorn main:app --reload --port 8000
```

You should see: `Uvicorn running on http://127.0.0.1:8000`

Test it: open http://localhost:8000/health — should return `{"status":"ok"}`

---

## Terminal window 3 — Frontend

```bash
cd ~/Desktop/fashion-archive/frontend
npm install
npm run dev
```

Open http://localhost:3000 — you should see the Fashion Archive search screen.

---

## Ingest your first show

Once both servers are running and keys are filled in:

```bash
cd ~/Desktop/fashion-archive/backend
source venv/bin/activate
python3 scripts/ingest.py
```

The script will ask you for:
- Video file path (e.g. `/Users/fengze/Downloads/chanel-aw2526.mp4`)
- Brand name (e.g. `Chanel`)
- Season (e.g. `AW2526`)

Ingestion takes 30–90 min per show. While it runs, the search page will show no results — that's normal. Once complete, search "Chanel structured shoulder" and results should appear.

---

## If something breaks

Paste the error into a Claude chat. Most common issues:
- `connection refused` on database → PostgreSQL isn't running (`brew services start postgresql@15`)
- `invalid api key` → check backend/.env spelling
- `module not found` → run `pip install -r requirements.txt` again inside the venv
