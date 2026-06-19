"""
Structured-field re-ranking for search.
Pure functions, no I/O. Called post-retrieval to reorder candidates.
"""

import re

# ── Lexicons ──────────────────────────────────────────────────────────────────

COLOURS: frozenset = frozenset({
    "black", "white", "red", "scarlet", "crimson", "pink", "rose",
    "navy", "blue", "cobalt", "electric",
    "green", "emerald", "sage", "forest",
    "yellow", "gold", "mustard",
    "orange", "rust", "terracotta",
    "purple", "violet", "lilac", "mauve",
    "brown", "camel", "tan", "khaki", "beige", "ecru", "cream", "ivory",
    "silver", "grey", "gray", "charcoal",
    "nude", "blush", "coral",
})

# Synonym → additional normalised tags (originals always kept)
COLOUR_SYNONYMS: dict = {
    "scarlet": "red",
    "crimson": "red",
    "cobalt": "blue",
    "electric": "blue",
    "emerald": "green",
    "sage": "green",
    "forest": "green",
    "mustard": "yellow",
    "rust": "orange",
    "terracotta": "orange",
    "violet": "purple",
    "lilac": "purple",
    "mauve": "purple",
    "charcoal": "grey",
    "gray": "grey",
    "ecru": "beige",
    "ivory": "cream",
    "blush": "pink",
    "coral": "pink",
    "rose": "pink",
}

GARMENTS: frozenset = frozenset({
    "dress", "gown", "coat", "jacket", "blazer", "trouser", "trousers",
    "skirt", "suit", "knit", "shirt", "blouse", "top", "cape",
    "jumpsuit", "romper", "shorts", "vest", "waistcoat", "cardigan",
    "sweater", "pullover", "turtleneck", "polo", "bodysuit",
    "maxi", "mini", "midi", "wrap", "shift", "slip", "sheath",
    "parka", "trench", "overcoat", "peacoat", "bomber", "anorak",
    "pants", "leggings", "culottes", "palazzo",
})

SILHOUETTES: frozenset = frozenset({
    "structured", "oversized", "tailored", "draped", "voluminous",
    "fitted", "relaxed", "slim", "wide", "flared", "boxy", "cocoon",
    "a-line", "column", "straight", "sculptural", "asymmetric",
    "layered", "deconstructed", "minimalist", "maximalist",
})

# ── Parse ─────────────────────────────────────────────────────────────────────

def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z\-]*", text.lower())


def parse_query_attributes(query: str) -> dict:
    """
    Return {"colours": [...], "garments": [...], "silhouettes": [...]}
    of lowercase tokens found in the query. Includes synonym expansions.
    """
    toks = set(_tokens(query))

    colours: list[str] = []
    for t in toks:
        if t in COLOURS:
            colours.append(t)
            if t in COLOUR_SYNONYMS:
                colours.append(COLOUR_SYNONYMS[t])
    colours = list(dict.fromkeys(colours))  # deduplicate, preserve order

    garments = [t for t in toks if t in GARMENTS]
    silhouettes = [t for t in toks if t in SILHOUETTES]

    return {"colours": colours, "garments": garments, "silhouettes": silhouettes}


# ── Boost ─────────────────────────────────────────────────────────────────────

_COLOUR_INC = 0.08
_GARMENT_INC = 0.08
_SILHOUETTE_INC = 0.05
_BOOST_CAP = 0.20


def _any_match(needles: list[str], haystack: str) -> bool:
    """True if any needle appears as a case-insensitive substring of haystack."""
    h = haystack.lower()
    return any(n in h for n in needles)


def attribute_boost(enriched: dict, attrs: dict) -> float:
    """
    Return a score increment [0, _BOOST_CAP] based on how well the enriched
    structured fields match the parsed query attributes.
    enriched keys: colours (list), garments (list), silhouette (str),
                   key_pieces (list), search_tags (list).
    """
    if not attrs or not enriched:
        return 0.0

    colours_q = attrs.get("colours") or []
    garments_q = attrs.get("garments") or []
    silhouettes_q = attrs.get("silhouettes") or []

    boost = 0.0

    # Colour match — check enriched.colours list and search_tags
    if colours_q:
        colour_blob = " ".join(
            (enriched.get("colours") or [])
            + (enriched.get("search_tags") or [])
        )
        if colour_blob and _any_match(colours_q, colour_blob):
            boost += _COLOUR_INC

    # Garment match — check enriched.garments, key_pieces, search_tags
    if garments_q:
        garment_blob = " ".join(
            (enriched.get("garments") or [])
            + (enriched.get("key_pieces") or [])
            + (enriched.get("search_tags") or [])
        )
        if garment_blob and _any_match(garments_q, garment_blob):
            boost += _GARMENT_INC

    # Silhouette match — enriched.silhouette (str) and search_tags
    if silhouettes_q:
        silhouette_blob = " ".join(filter(None, [
            enriched.get("silhouette") or "",
            " ".join(enriched.get("search_tags") or []),
        ]))
        if silhouette_blob and _any_match(silhouettes_q, silhouette_blob):
            boost += _SILHOUETTE_INC

    return min(boost, _BOOST_CAP)
