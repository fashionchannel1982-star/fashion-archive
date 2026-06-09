# Fashion Archive — Demo Brief
## Internal MVP · Chanel, Dior, Gucci AW25/26

---

## Purpose of this brief

This brief defines the three use cases to demonstrate when testing the internal MVP. Each use case represents a real user type FA will serve. Running all three back-to-back takes approximately 10 minutes and tells you whether the semantic search is working as intended.

Before running the demo, confirm the platform is live: all three shows indexed, search returning results, confidence scores displaying.

---

## Use case 1 — Fashion house competitive intelligence (P2 user)

**Who this represents:** A trend strategist at a luxury brand, researching what competitors showed this season. P2 is FA's primary revenue opportunity at launch.

**The scenario:** A Dior strategist wants to understand how Chanel and Gucci approached tailoring this season — specifically structured suiting and strong shoulders — so they can benchmark their own AW25/26 direction.

**Queries to run:**
```
structured tailoring AW 2025
oversized blazer silhouettes
sharp shoulder suiting
power dressing
```

**What good looks like:** Results return moments from across all three shows ranked by confidence. The strategist can see, in seconds, how each house approached the same trend direction. Exact and Strong match results dominate. Export buttons allow them to pull specific moments for an internal deck.

**What this proves:** Semantic search works across brands simultaneously. A fashion house can use FA for cross-brand competitive intelligence in real time — something WGSN cannot do with archive video.

---

## Use case 2 — Academic research (P3 user)

**Who this represents:** A final-year fashion student or academic researcher at a fashion school. P3 launches September 2026 — this use case validates that the archive serves research queries, not just trend queries.

**The scenario:** A student writing a thesis on "maximalism and excess in luxury fashion 2025–2026" wants primary source material — actual runway footage — rather than editorial opinion pieces.

**Queries to run:**
```
maximalist layering Gucci
embellishment and excess
bold print and colour
theatrical styling luxury
```

**What good looks like:** Gucci results dominate (expected — Gucci AW25/26 is the most maximalist of the three shows). High-confidence results include looks with visible embellishment, strong print, or theatrical styling. The student can bookmark relevant moments and export metadata for citation.

**What this proves:** The archive serves academic research queries with the same interface as commercial queries. No filtering needed — the semantic model understands the distinction between "structured tailoring" and "embellishment and excess" without being told.

---

## Use case 3 — Brand or retailer archive search (P2 user)

**Who this represents:** A buyer or product director at a retailer, or a brand archive manager, researching how specific design codes have been executed this season. This validates FA as operational intelligence, not just research.

**The scenario:** A buyer for a luxury multi-brand retailer wants to understand how Dior executed its signature feminine codes in AW25/26 — specifically floaty fabrication, soft silhouettes, and romantic detailing — to inform their buying decisions.

**Queries to run:**
```
Dior romantic feminine AW25
soft draped silhouette evening
sheer fabrication layering
pale palette delicate detail
```

**What good looks like:** Results are predominantly Dior, high confidence. The buyer can see how Dior's AW25/26 collection expresses its house codes without watching the full show. Timestamp information lets them reference specific moments. Export allows them to share with buying team.

**What this proves:** The semantic layer understands house codes and brand-specific aesthetic language. FA is operationally useful for buyers and brand managers, not just researchers — this is the commercial case for P2 pricing.

---

## What to check during the demo

For each query, assess:

1. **Relevance** — Do the top results actually match what was searched? A confidence of 90+ that returns irrelevant content is a calibration problem worth noting.

2. **Cross-brand coverage** — For queries not brand-specific (Use Case 1), are all three houses represented in results?

3. **Confidence distribution** — Are most results landing in the 75–94 range (Strong match) for precise queries? If most results are 60–74 (Relevant), the Twelve Labs index may need reprocessing or the query language needs adjusting.

4. **Description quality** — Do the Claude-refined descriptions accurately describe what's in the clip? Mis-descriptions here mean the Claude enrichment prompt needs tuning.

5. **Speed** — Processing time should be under 500ms for a 20-result query. Anything over 1 second is a performance issue to log.

---

## Known limitations at MVP

- **No video playback** — thumbnail + timestamp only. This is intentional at MVP.
- **Three shows only** — queries will sometimes return no results if too specific. This is data coverage, not a search problem.
- **No filtering** — all nuance handled by semantic search. If users feel they need filtering, note what they were trying to filter on — that's product feedback for Phase 2.
- **Internal only** — no authentication. Do not share the URL outside the founding team.

---

## After the demo

If all three use cases return plausible, high-confidence results: the MVP is validated. Next step is Proof Shot publication and first P2 client outreach.

If search quality is poor: run the three shows through ingestion again with higher-quality source video, or revisit the Claude description refinement prompt.
