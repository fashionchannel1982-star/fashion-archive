# Fashion Archive — MVP v2
## Master Spec for Claude Code

> Internal MVP only. Three shows: Chanel AW25/26, Dior AW25/26, Gucci AW25/26.
> Goal: semantic search over ingested video, with confidence scoring and export.
> Aesthetic: Apple TV meets Google Search — dark, premium, effortless.

---

## What you are building

A semantic video search platform. A user types a natural language query — "black structured shoulders", "Dior navy tailoring", "Gucci maximalist prints" — and gets back ranked video moments from the ingested shows, each with:

- Thumbnail of the moment
- Brand, season, timestamp
- Short AI-generated description of what's happening
- Confidence score (0–100) showing match strength
- Bookmark button (saves to session)
- Export button (downloads a JSON card with full metadata)

No filters at MVP. The search handles nuance. Filters come from user feedback after launch.

---

## Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python 3.11) |
| Database | PostgreSQL 15 + pgvector |
| Video AI | Twelve Labs (Marengo embedding + Pegasus description) |
| Intelligence | Claude API — claude-sonnet-4-20250514 |
| Frontend | Next.js 14 (App Router) |
| Styling | Tailwind CSS |
| Storage | AWS S3 (or local /tmp for MVP) |
| Auth | None at MVP — single internal user |

---

## Environment variables

```
# backend/.env
TWELVE_LABS_API_KEY=
TWELVE_LABS_INDEX_ID=           # created on first ingest run
ANTHROPIC_API_KEY=
DATABASE_URL=postgresql://localhost/fashion_archive
AWS_S3_BUCKET=                  # optional at MVP
AWS_ACCESS_KEY_ID=              # optional at MVP
AWS_SECRET_ACCESS_KEY=          # optional at MVP
```

---

## Project structure

```
fa-mvp-v2/
├── CLAUDE.md                   # this file
├── backend/
│   ├── main.py                 # FastAPI app
│   ├── requirements.txt
│   ├── .env.example
│   └── scripts/
│       ├── init_db.py          # creates tables
│       └── ingest.py           # Twelve Labs ingestion
├── frontend/
│   ├── package.json
│   ├── next.config.js
│   ├── tailwind.config.js
│   └── src/
│       ├── pages/
│       │   ├── index.tsx       # search page
│       │   └── api/            # Next.js API proxy routes (optional)
│       ├── components/
│       │   ├── SearchBar.tsx
│       │   ├── ResultCard.tsx  # includes confidence + export
│       │   └── BookmarkPanel.tsx
│       └── lib/
│           └── api.ts          # typed API client
└── docs/
    ├── ARCHITECTURE.md
    ├── API_SPEC.md
    └── DEMO_BRIEF.md
```

---

## Data model

### `shows` table
```sql
CREATE TABLE shows (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  brand TEXT NOT NULL,           -- 'Chanel', 'Dior', 'Gucci'
  season TEXT NOT NULL,          -- 'AW2526'
  year INTEGER NOT NULL,         -- 2025
  twelve_labs_video_id TEXT,     -- returned by TL after ingest
  summary TEXT,                  -- Claude-generated show summary
  created_at TIMESTAMPTZ DEFAULT now()
);
```

### `moments` table
```sql
CREATE TABLE moments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  show_id UUID REFERENCES shows(id),
  timestamp_start FLOAT NOT NULL,   -- seconds
  timestamp_end FLOAT NOT NULL,
  description TEXT,                  -- Claude/Pegasus-generated
  thumbnail_url TEXT,
  embedding VECTOR(1024),            -- Marengo embedding
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON moments USING ivfflat (embedding vector_cosine_ops);
```

---

## Backend — key endpoints

### POST /api/ingest
Ingests a video into Twelve Labs and stores the index ID.

Request:
```json
{
  "brand": "Chanel",
  "season": "AW2526",
  "year": 2025,
  "video_url": "https://..."
}
```

Response:
```json
{
  "show_id": "uuid",
  "twelve_labs_task_id": "...",
  "status": "queued"
}
```

---

### POST /api/search
Semantic search across ingested moments.

Request:
```json
{
  "query": "black structured shoulders",
  "limit": 20
}
```

Response:
```json
{
  "query": "black structured shoulders",
  "results": [
    {
      "moment_id": "uuid",
      "show_id": "uuid",
      "brand": "Chanel",
      "season": "AW2526",
      "year": 2025,
      "timestamp_start": 142.3,
      "timestamp_end": 156.1,
      "description": "Model walks in structured black wool jacket, exaggerated shoulders, minimal styling.",
      "thumbnail_url": "https://...",
      "confidence": 94,
      "score_raw": 0.94
    }
  ],
  "total": 20,
  "processing_time_ms": 340
}
```

**Confidence scoring logic:**
- Raw cosine similarity from pgvector (0.0–1.0) → multiply by 100 → round to integer
- Bucket display: 90–100 = "Exact match", 75–89 = "Strong match", 60–74 = "Relevant", below 60 = suppress from results
- Never show raw float to user — always display as integer 0–100

---

### GET /api/shows
Returns all ingested shows with status.

Response:
```json
{
  "shows": [
    {
      "id": "uuid",
      "brand": "Chanel",
      "season": "AW2526",
      "year": 2025,
      "moment_count": 47,
      "status": "ready"
    }
  ]
}
```

---

### POST /api/export
Returns a structured JSON export card for a given moment. Used by the Export button.

Request:
```json
{
  "moment_id": "uuid"
}
```

Response (also triggers browser download):
```json
{
  "export_version": "1.0",
  "exported_at": "2025-10-01T14:22:00Z",
  "source": "Fashion Archive Internal MVP",
  "moment": {
    "brand": "Chanel",
    "season": "AW2526",
    "year": 2025,
    "timestamp_start": 142.3,
    "timestamp_end": 156.1,
    "description": "Model walks in structured black wool jacket, exaggerated shoulders, minimal styling.",
    "confidence": 94,
    "thumbnail_url": "https://..."
  }
}
```

---

## Ingestion pipeline

Run `backend/scripts/ingest.py` once per show. It:

1. Creates the Twelve Labs index (if not exists) — store the index ID in `.env`
2. Uploads the video to Twelve Labs
3. Waits for indexing to complete (poll every 30s)
4. Runs Pegasus to generate descriptions for each segment
5. Pulls embeddings via Marengo
6. Stores everything in PostgreSQL

For three shows (~45 min average runway footage each), expect total ingestion time: **2–4 hours**.

---

## Frontend — search page behaviour

1. Page loads → single search input, centred, dark background, no results visible
2. User types query → 300ms debounce → POST /api/search
3. Results appear below as a grid (3 columns desktop, 1 column mobile)
4. Each card shows: thumbnail, brand + season pill, timestamp, description (2 lines), confidence badge, bookmark icon, export icon
5. Bookmark → saves to localStorage → visible in slide-out panel (top right)
6. Export → triggers GET /api/export/{moment_id} → auto-downloads JSON file named `fa-export-{brand}-{timestamp}.json`

---

## Design tokens

```css
--bg-primary: #0A0A0A;
--bg-card: #141414;
--bg-hover: #1C1C1C;
--text-primary: #F5F5F0;
--text-secondary: #8A8A85;
--accent: #EDE8DC;          /* warm off-white — screen accent */
--confidence-high: #4ADE80; /* green — 90-100 */
--confidence-mid: #FACC15;  /* amber — 75-89 */
--confidence-low: #94A3B8;  /* slate — 60-74 */
--font-display: 'Cormorant', serif;
--font-body: 'Space Grotesk', sans-serif;
--radius: 8px;
```

Import fonts:
```html
<link href="https://fonts.googleapis.com/css2?family=Cormorant:wght@300;400;500&family=Space+Grotesk:wght@300;400;500&display=swap" rel="stylesheet">
```

---

## Claude intelligence layer

After Twelve Labs returns segment descriptions, run each through Claude with this prompt:

```
You are a fashion intelligence system. Given a video segment description from a runway show, produce a clean, precise one-sentence description suitable for a search result. Focus on: garment type, silhouette, colour, fabric texture (if identifiable), styling details. Do not describe the model or runway. Keep under 25 words.

Segment: {pegasus_description}
```

Use `claude-sonnet-4-20250514`. Batch process all segments after ingestion — not at query time.

---

## What NOT to build at MVP

- User authentication (single internal user, no login)
- Filtering UI (search handles it)
- Trend signals / analytics (Phase 2)
- Somnia attribution layer (Phase 2)
- Multi-tenancy (Phase 2)
- Video playback (thumbnails + timestamps only at MVP)

---

## First run checklist

- [ ] `createdb fashion_archive`
- [ ] `python backend/scripts/init_db.py`
- [ ] Add Twelve Labs API key to `.env`
- [ ] Add Anthropic API key to `.env`
- [ ] Run `python backend/scripts/ingest.py` for each of the three shows
- [ ] Copy `TWELVE_LABS_INDEX_ID` from output into `.env`
- [ ] `cd frontend && npm install && npm run dev`
- [ ] `cd backend && uvicorn main:app --reload`
- [ ] Open http://localhost:3000
- [ ] Search "Chanel structured shoulder" — confirm results return
