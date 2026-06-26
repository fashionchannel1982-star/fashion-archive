# Fashion Archive — Demo Run-of-Show Checklist

> Reproducible cold. Follow this exactly, in order. Each step has a "good" criterion and a fallback.

---

## Pre-demo setup (5 min before)

| | Step | Good |
|---|---|---|
| ☐ | Start backend: `cd ~/Desktop/fashion-archive/backend && source venv/bin/activate && uvicorn main:app --port 8000` | Terminal shows `Application startup complete` |
| ☐ | Start frontend: `cd ~/Desktop/fashion-archive/frontend && npm run dev` | Terminal shows `Ready on http://localhost:3000` |
| ☐ | Open `http://localhost:3000` in Chrome, full-screen | Hero clip autoplays (muted) in the landing frame |
| ☐ | Clear search field if anything is typed | Blank input, hero visible, Explore button visible |
| ☐ | Check backend health: `curl localhost:8000/health` | `{"status":"ok","version":"2.0.0"}` |

**If hero clip doesn't play:** page still works — the input, Explore panel, and shows grid are all visible. Proceed normally. Hero is optional-by-construction.

---

## Act 1 — Concept queries (fire the synthesis line)

### Query 1: `sheer black evening looks`
*Purpose: show cross-house semantic search; synthesis line proves the archive sees across brands.*

| | Step | What "good" looks like | Fallback |
|---|---|---|---|
| ☐ | Click **Explore ↓** | Panel opens with 9 queries listed | Type directly into search box |
| ☐ | Click **"sheer black evening looks"** | Results grid appears (≥10 cards, mix of brands) | n/a |
| ☐ | Watch synthesis slot above grid | Gold "Intelligence" pullquote appears within ~3 s, citing ≥2 houses by name | If it takes >5 s, it's still coming; grid is already usable |
| ☐ | Hover over a card | Play button (▶) fades in on thumbnail | — |
| ☐ | Click a card thumbnail | Video modal opens, clip begins playing | If modal shows "Clip unavailable", dismiss with × and pick another card |
| ☐ | Show progress bar at modal bottom | Thin warm-white bar advances across the clip duration | — |
| ☐ | Dismiss modal with **×** or **Esc** | Modal closes, grid reappears intact | — |

---

### Query 2: `structured shoulders, sharp tailoring`
*Purpose: silhouette/garment concept; shows how the archive interprets construction across houses.*

| | Step | What "good" looks like | Fallback |
|---|---|---|---|
| ☐ | Click Explore ↓ → **"structured shoulders, sharp tailoring"** | Results load; brands include at least 2 distinct houses | — |
| ☐ | Synthesis line | Pullquote cites ≥2 houses — different houses to Query 1 | — |
| ☐ | Point at confidence badges | Dots: green = Exact (90+), amber = Strong (75–89), slate = Relevant (60–74) | — |
| ☐ | Save a card | ✦ Save button highlights; Saved count appears in top bar | — |

---

### Query 3: `maximalist print colour runway`
*Purpose: strongest semantic chip (top conf 93); vivid visual contrast to tailoring queries.*

| | Step | What "good" looks like | Fallback |
|---|---|---|---|
| ☐ | Type or chip: **"maximalist print colour runway"** | Grid fills with print-heavy, colourful looks | — |
| ☐ | Synthesis line | Pullquote about print/colour narrative appears | — |
| ☐ | Click a card → play | Clip of print look; modal plays and loops within 2-min clip window | — |

---

## Act 2 — Brand precision queries (no synthesis — correct behaviour)

### Query 4: `Chanel tweed and tailoring`
*Purpose: show brand-filtered depth; deliberately no synthesis (single-house = correct behaviour).*

| | Step | What "good" looks like | Fallback |
|---|---|---|---|
| ☐ | Chip: **"Chanel tweed and tailoring"** | Results: all Chanel, mix of years | — |
| ☐ | Synthesis slot | **Empty** — no synthesis line. This is intentional: single-house queries don't synthesise. Call it out. | — |
| ☐ | Point at Archive badge | Cards with year < 2020 show a subtle "YYYY · Archive" badge | — |
| ☐ | Click a card → play | Chanel clip plays | — |

---

### Query 5: `Dior structured tailoring`
*Purpose: compare brand voice (Dior vs Chanel); same no-synthesis behaviour.*

| | Step | What "good" looks like | Fallback |
|---|---|---|---|
| ☐ | Chip: **"Dior structured tailoring"** | All Dior results (2024 and 2025 shows) | — |
| ☐ | Synthesis slot | Empty — same single-house suppression | — |
| ☐ | Compare card style with Chanel results | Dior cards show different creative director credit | — |

---

## Act 3 — Motion and archive queries

### Query 6: `a model pausing at the end of the runway`
*Purpose: motion/gesture recognition — show this isn't keyword matching.*

| | Step | What "good" looks like | Fallback |
|---|---|---|---|
| ☐ | Chip or type: **"a model pausing at the end of the runway"** | Results show end-of-runway moments; cross-house | — |
| ☐ | Synthesis line | Gold pullquote appears | — |
| ☐ | Click → play | Clip shows the actual pause/turn moment | — |

---

### Query 7: `archive runway drama`
*Purpose: strongest chip overall (conf 98); archive aesthetic appeal — great visual moment.*

| | Step | What "good" looks like | Fallback |
|---|---|---|---|
| ☐ | Chip: **"archive runway drama"** | High-confidence results (green Exact badges prominent); primarily older archive | — |
| ☐ | Synthesis line | Rich narrative about archive aesthetic | — |

---

## Act 4 — Colour / statement

### Query 8: `red dress`
*Purpose: garment + colour combination; shows structured-match boost putting actual red dresses first.*

| | Step | What "good" looks like | Fallback |
|---|---|---|---|
| ☐ | Chip: **"red dress"** | Top 2 results are actual red or deep-red dresses (not red coats or black dresses) | — |
| ☐ | Note the result descriptions | Colours/garments visible on hover or in description text | — |

---

## Act 5 — Navigation features

### Timeline
| | Step | What "good" looks like | Fallback |
|---|---|---|---|
| ☐ | From landing: click **"Follow a house through time"** tile | Navigates to `/timeline` | Type `localhost:3000/timeline` |
| ☐ | Timeline loads | Points plotted for Chanel across years; creative director transitions marked | — |
| ☐ | Back to search | Browser back; hero and input restored | — |

### Explore panel on zero results
| | Step | What "good" looks like | Fallback |
|---|---|---|---|
| ☐ | Type: `xyzzy` | "Nothing matched in the archive" message; Explore panel reappears beneath it | — |
| ☐ | Click any chip in the re-appeared panel | Search runs normally, results load | — |

### Video modal error state (if needed during demo)
| | Step | What "good" looks like |
|---|---|---|
| ☐ | If any clip fails to load | Modal shows "Clip unavailable / The stream could not be loaded — close and try again" in legible grey text; × and Esc both dismiss cleanly |

---

## Recovery notes

| Situation | Recovery |
|---|---|
| Synthesis takes > 10 s | It timed out silently — grid already usable; just move on |
| Synthesis shows wrong text | Not possible: synthesis is suppressed unless it cites actual result brands |
| Video modal hangs loading | The `load()` async function is fire-and-forget; click × to dismiss — no cleanup needed |
| Backend returns 500 | Red "Archive unreachable" banner appears above results; restart `uvicorn` |
| Hero clip not autoplaying | Browser blocked autoplay — click the play button once; thereafter autoplay works per-tab |
| Whole page blank | Check that both `uvicorn` (port 8000) and Next.js dev server (port 3000) are running |

---

## Chip order reference

```
sheer black evening looks          — concept / cross-house → synthesis fires
structured shoulders, sharp tailoring — concept / cross-house → synthesis fires
minimal all-black, head to toe     — concept / cross-house → synthesis fires
maximalist print colour runway     — concept / cross-house → synthesis fires
Chanel tweed and tailoring         — brand / single-house  → NO synthesis (correct)
Dior structured tailoring          — brand / single-house  → NO synthesis (correct)
a model pausing at the end of the runway — motion → synthesis fires
archive runway drama               — archive / cross-house → synthesis fires
red dress                          — colour+garment → structured boost active
```

All 9 verified PASS (top conf 77–98, ≥15 results above floor). Last verified 2026-06-26.
