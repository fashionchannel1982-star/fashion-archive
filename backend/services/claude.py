"""
Fashion Archive — Claude Service
AI enrichment layer. Runs after Twelve Labs ingestion.
Uses claude-sonnet-4-6 for look enrichment and editorial generation.
"""

import os
import anthropic
import logging

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
MODEL = "claude-sonnet-4-6"


async def enrich_look(raw_description: str, show_context: dict) -> dict:
    """
    Take a raw Pegasus description and enrich it with structured fashion intelligence.
    Returns structured data: clean description, garments, colours, silhouette, key pieces.
    """
    if not raw_description or not raw_description.strip():
        return {
            "description": "",
            "garments": [],
            "colours": [],
            "silhouette": "",
            "key_pieces": [],
            "search_tags": [],
        }

    brand = show_context.get("brand", "")
    season = show_context.get("season", "")
    year = show_context.get("year", "")

    prompt = f"""You are a fashion intelligence system analysing a {brand} {season} {year} runway look.

Raw description from video analysis:
{raw_description}

Return a JSON object with these fields:
{{
  "description": "Clean one-sentence description under 25 words. Fashion vocabulary only. No model descriptions. E.g. 'Structured black wool blazer with exaggerated shoulders over wide-leg ivory trousers, gold hardware belt.'",
  "garments": ["list", "of", "garment", "types"],
  "colours": ["list", "of", "colours"],
  "silhouette": "one word or short phrase: e.g. 'oversized', 'tailored', 'draped', 'structured'",
  "key_pieces": ["standout or signature pieces worth noting"],
  "search_tags": ["terms a fashion researcher might search for to find this look"]
}}

Return only the JSON object. No preamble, no explanation."""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        text = response.content[0].text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception as e:
        logger.warning(f"Claude enrichment failed: {e}")
        return {
            "description": raw_description[:200] if raw_description else "",
            "garments": [],
            "colours": [],
            "silhouette": "",
            "key_pieces": [],
            "search_tags": [],
        }


async def synthesize_results(query: str, moments: list[dict]) -> dict:
    """
    One grounded sentence over the structured metadata of the top results.
    Returns {"synthesis": str, "grounded": bool, "cited_moment_ids": [...]}.
    Guardrail: assert only what the cited clips support; if evidence is thin
    or contradictory, say so rather than inventing a trend.
    """
    if not moments:
        return {"synthesis": "", "grounded": False, "cited_moment_ids": []}

    top = moments[:8]
    cited_ids = [m.get("moment_id", "") for m in top]

    lines = []
    for m in top:
        brand = m.get("brand", "")
        season = m.get("season", "")
        desc = m.get("description", "")
        enriched = m.get("enriched") or {}
        colours = ", ".join(enriched.get("colours", [])) if enriched.get("colours") else ""
        silhouette = enriched.get("silhouette", "")
        parts = [f"{brand} {season}:", desc]
        if colours:
            parts.append(f"Colours: {colours}.")
        if silhouette:
            parts.append(f"Silhouette: {silhouette}.")
        lines.append(" ".join(parts))

    moments_block = "\n".join(f"- {l}" for l in lines)

    system = (
        "You are a fashion intelligence system that synthesises search results "
        "into one grounded editorial observation. Never fabricate or generalise "
        "beyond what the cited clips explicitly support."
    )

    prompt = f"""Query: "{query}"

Archive clips returned:
{moments_block}

Write ONE sentence in fashion-editor voice that captures what these specific clips share or signal.
Name the houses they come from. If the set is too thin or the clips are too disparate to support a credible read, reply with ONLY: INSUFFICIENT_EVIDENCE

One sentence only. No preamble."""

    try:
        import anthropic as _anthropic
        _client = _anthropic.AsyncAnthropic()
        msg = await _client.messages.create(
            model=MODEL,
            max_tokens=200,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if "INSUFFICIENT_EVIDENCE" in text or len(text) < 20:
            return {
                "synthesis": "Not enough consistent evidence across these results to call a trend.",
                "grounded": False,
                "cited_moment_ids": cited_ids,
            }
        return {"synthesis": text, "grounded": True, "cited_moment_ids": cited_ids}
    except Exception as e:
        logger.warning(f"synthesize_results failed: {e}")
        return {"synthesis": "", "grounded": False, "cited_moment_ids": []}


async def generate_show_editorial(show_context: dict, looks: list) -> str:
    """
    Generate a professional editorial summary of a show from its looks.
    Used as the show-level summary displayed in the archive.
    """
    brand = show_context.get("brand", "")
    season = show_context.get("season", "")
    year = show_context.get("year", "")

    if not looks:
        return ""

    # Build a summary of looks for context
    look_summaries = []
    for look in looks[:20]:  # Cap at 20 to stay within context
        enriched = look.get("enriched_data", {})
        desc = enriched.get("description") if enriched else look.get("description", "")
        if desc:
            look_summaries.append(f"- {desc}")

    looks_text = "\n".join(look_summaries)

    prompt = f"""You are a senior fashion editor writing an archive summary for {brand} {season} {year}.

Here are the looks from this show:
{looks_text}

Write a professional editorial summary of 100-150 words covering:
- Overall creative direction and mood
- Key themes and silhouettes
- Dominant colour story
- Standout moments or signature pieces
- How this collection fits the house's creative trajectory

Write as a fashion editor. No bullet points. Flowing prose."""

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        logger.warning(f"Editorial generation failed: {e}")
        return ""
