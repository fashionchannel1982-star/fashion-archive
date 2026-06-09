# Fashion Archive — API Specification
## Internal MVP v2

Base URL (local): `http://localhost:8000`
Base URL (staging, future): `https://api.fashionarchive.com`

All requests and responses are JSON. No authentication at MVP.

---

## Endpoints

---

### GET /health

Health check.

**Response 200**
```json
{
  "status": "ok",
  "version": "2.0.0"
}
```

---

### GET /api/shows

Returns all ingested shows with status and moment counts.

**Response 200**
```json
{
  "shows": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "brand": "Chanel",
      "season": "AW2526",
      "year": 2025,
      "moment_count": 47,
      "status": "ready"
    },
    {
      "id": "8cb12a71-3312-4891-c7ad-9d127e44bfc2",
      "brand": "Dior",
      "season": "AW2526",
      "year": 2025,
      "moment_count": 52,
      "status": "ready"
    },
    {
      "id": "2de99f13-8821-4567-a1bc-6c258d55cfe7",
      "brand": "Gucci",
      "season": "AW2526",
      "year": 2025,
      "moment_count": 61,
      "status": "ready"
    }
  ]
}
```

**Status values**
| Value | Meaning |
|---|---|
| `ready` | Ingested, indexed, searchable |
| `processing` | Twelve Labs indexing in progress |
| `error` | Ingestion failed — check logs |

---

### POST /api/ingest

**INTERNAL ONLY. Not exposed to external partners.**

Queues a video for Twelve Labs ingestion. Run once per show.

**Request body**
```json
{
  "brand": "Chanel",
  "season": "AW2526",
  "year": 2025,
  "video_url": "https://..."
}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| brand | string | yes | Exact match to display name: "Chanel", "Dior", "Gucci" |
| season | string | yes | Format: AW or SS + year digits, e.g. "AW2526" |
| year | integer | yes | First year of season, e.g. 2025 |
| video_url | string | yes | Direct video URL or YouTube URL |

**Response 200**
```json
{
  "show_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "twelve_labs_task_id": "65f7c3b2a1d4e5f6a7b8c9d0",
  "status": "queued"
}
```

**Notes**
- Indexing takes approximately 45–90 minutes per show
- Poll Twelve Labs dashboard or task endpoint to check progress
- Once complete, copy `TWELVE_LABS_INDEX_ID` to `.env`

---

### POST /api/search

Semantic search across all ingested shows.

**Request body**
```json
{
  "query": "black structured shoulders",
  "limit": 20
}
```

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| query | string | yes | — | Natural language. Min 1 character. |
| limit | integer | no | 20 | Max results to return. Max 50. |

**Response 200**
```json
{
  "query": "black structured shoulders",
  "results": [
    {
      "moment_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "show_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "brand": "Chanel",
      "season": "AW2526",
      "year": 2025,
      "timestamp_start": 142.3,
      "timestamp_end": 156.1,
      "description": "Structured black wool jacket, exaggerated padded shoulders, single-button closure, worn over wide-leg trousers.",
      "thumbnail_url": "https://s3.amazonaws.com/fa-mvp/thumbs/chanel-aw2526-142.jpg",
      "confidence": 94,
      "score_raw": 0.9412
    }
  ],
  "total": 12,
  "processing_time_ms": 312
}
```

**Confidence scoring**

| Confidence | Label | Colour |
|---|---|---|
| 90–100 | Exact match | Green (#4ADE80) |
| 75–89 | Strong match | Amber (#FACC15) |
| 60–74 | Relevant | Slate (#94A3B8) |
| < 60 | Suppressed | Not returned |

- `confidence` is always an integer 0–100
- `score_raw` is the raw cosine similarity float (for debugging only — never display to end users)
- Results are sorted by confidence descending
- Results below 60 are suppressed entirely

**Response 400** — empty query
```json
{
  "detail": "Query cannot be empty"
}
```

---

### POST /api/export

Returns a structured JSON metadata card for a single moment. Triggers a browser file download.

**Request body**
```json
{
  "moment_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

**Response 200**

Returns JSON with `Content-Disposition: attachment` header.

```json
{
  "export_version": "1.0",
  "exported_at": "2025-10-01T14:22:00Z",
  "source": "Fashion Archive — Internal MVP",
  "moment": {
    "brand": "Chanel",
    "season": "AW2526",
    "year": 2025,
    "timestamp_start": 142.3,
    "timestamp_end": 156.1,
    "description": "Structured black wool jacket, exaggerated padded shoulders, single-button closure, worn over wide-leg trousers.",
    "thumbnail_url": "https://s3.amazonaws.com/fa-mvp/thumbs/chanel-aw2526-142.jpg"
  }
}
```

Downloaded filename format: `fa-export-{brand-lowercase}-{timestamp}s.json`
Example: `fa-export-chanel-142s.json`

**Response 404** — moment not found
```json
{
  "detail": "Moment not found"
}
```

---

## Error format

All errors follow FastAPI's default format:

```json
{
  "detail": "Human-readable error message"
}
```

---

## Rate limits

None at internal MVP. Phase 2 introduces per-tenant rate limiting.

---

## CORS

At MVP, CORS is open to `http://localhost:3000` only. Update `main.py` when deploying to staging.
