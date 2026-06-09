# Fashion Archive — Architecture
## Internal MVP v2

---

## Overview

Fashion Archive is a semantic video intelligence platform. The architecture is designed around one core constraint: **the data lake is independently owned by Fashion Archive, separate from any joint venture or technology partner.**

Technology partners (Emerce, and anyone else) connect via API only. They never touch the database.

---

## System diagram

```
                    ┌─────────────────────────────┐
                    │     TWELVE LABS             │
                    │  Marengo (embeddings)        │
                    │  Pegasus (descriptions)      │
                    └────────────┬────────────────┘
                                 │ ingest / index
                    ┌────────────▼────────────────┐
                    │     FASHION ARCHIVE          │
                    │     DATA LAKE                │
                    │                              │
                    │  PostgreSQL + pgvector        │
                    │  shows, moments, embeddings  │
                    │  AWS S3 (thumbnails, clips)  │
                    └────────────┬────────────────┘
                                 │ read-only API
                    ┌────────────▼────────────────┐
                    │     FASTAPI BACKEND          │
                    │                              │
                    │  /api/search                 │
                    │  /api/export                 │
                    │  /api/shows                  │
                    │  /api/ingest (internal only) │
                    └────────────┬────────────────┘
                                 │
              ┌──────────────────┴──────────────────┐
              │                                     │
   ┌──────────▼──────────┐              ┌───────────▼──────────┐
   │  NEXT.JS FRONTEND   │              │  EXTERNAL API ACCESS │
   │  (internal MVP)     │              │  (future: Emerce,    │
   │                     │              │   P2 clients, P3)    │
   │  Search page        │              │                      │
   │  Result cards       │              │  Scoped credentials  │
   │  Bookmarks          │              │  Rate limited        │
   │  Export             │              │  No DB access        │
   └─────────────────────┘              └──────────────────────┘
```

---

## Components

### Twelve Labs
- **Role**: Video indexing and semantic search
- **Models used**: Marengo 2.6 (embedding), Pegasus 1.1 (description generation)
- **What it indexes**: Each show is uploaded once. Twelve Labs creates a searchable index of visual and audio content.
- **What Fashion Archive stores**: The `twelve_labs_video_id` per show, used to match search results back to our moments table.

### PostgreSQL + pgvector
- **Role**: Primary data store and vector similarity search
- **Tables**: `shows`, `moments` (see CLAUDE.md for schemas)
- **Vector search**: Used as fallback/hybrid search alongside Twelve Labs API results
- **Isolation**: Row-level security prepared for multi-tenant Phase 2

### FastAPI Backend
- **Role**: The single point of access to the data lake
- **Critical rule**: `/api/ingest` is internal only — not exposed to any external partner
- **Auth at MVP**: None (internal use). Phase 2 adds API key auth per tenant.

### Claude API
- **Role**: Intelligence and description refinement layer
- **Usage at MVP**: Post-ingestion enrichment — runs each Pegasus description through Claude to produce clean, searchable one-sentence descriptions
- **Not used at query time** at MVP (avoids latency). Phase 2 adds real-time intelligence queries.

### Next.js Frontend
- **Role**: Internal search interface
- **Design**: Apple TV aesthetic (dark, premium, minimal) × Google search (single input, instant results)
- **New in v2**: Confidence scoring display, bookmark panel, JSON export

---

## Data flow

### Ingestion (one-time per show)
```
1. Run ingest.py with video URL
2. Twelve Labs indexes the video (2–4 hours for three shows)
3. Pegasus generates segment descriptions
4. Claude refines descriptions (batch, post-processing)
5. Embeddings + descriptions stored in PostgreSQL
6. Thumbnails extracted and stored in S3
```

### Search query (real-time)
```
1. User types query in search box (300ms debounce)
2. POST /api/search → FastAPI
3. FastAPI calls Twelve Labs semantic search API
4. Returns clip IDs + confidence scores
5. FastAPI looks up matching moments in PostgreSQL
6. Suppresses results below confidence 60
7. Returns ranked results to frontend
8. Frontend renders result cards with confidence badges
```

### Export
```
1. User clicks Export on a result card
2. POST /api/export {moment_id}
3. FastAPI fetches full moment metadata from DB
4. Returns JSON export card
5. Browser triggers file download: fa-export-{brand}-{timestamp}s.json
```

---

## Security principles

1. **Data lake sovereignty**: FA owns the PostgreSQL instance, the S3 bucket, the Twelve Labs account. No partner has credentials.
2. **API-only external access**: Emerce and future P2/P3 clients get scoped API keys only. No database credentials ever leave FA.
3. **Ingestion endpoint is internal**: `/api/ingest` is not documented in the external API spec and is blocked at the gateway layer in production.
4. **Somnia attribution** (Phase 2): On-chain provenance layer sits outside the data lake. It reads from the data lake via a separate read-only service account. Never writes to it.

---

## Phase 2 additions (not MVP)

| Feature | Trigger |
|---|---|
| Multi-tenant auth (Clerk/Auth0) | First P2 paying client |
| Trend signals / Signals view | After 10+ shows ingested |
| Video playback (Mux/Cloudflare Stream) | P2 client request |
| Somnia attribution layer | Post-seed funding |
| Filtering UI | User feedback from MVP testing |
| Real-time Claude intelligence queries | P2 product requirement |
