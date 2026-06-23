"""
Structured-field re-ranking for search.
Pure functions, no I/O. Called post-retrieval to reorder candidates.
"""

import re
from typing import Optional

# ── Metadata filter parsing ───────────────────────────────────────────────────

_YEAR_RE = re.compile(r"\b(\d{4})\b")

# Season tokens → canonical code ('FW' | 'SS' | 'Couture')
_SEASON_MAP: dict = {
    "fall": "FW",
    "autumn": "FW",
    "winter": "FW",
    "fw": "FW",
    "aw": "FW",
    "spring": "SS",
    "summer": "SS",
    "ss": "SS",
    "couture": "Couture",
    "haute": "Couture",
}

# Structural stop-words removed from residual (but not mapped to season)
_STRUCTURAL_STOPS: frozenset = frozenset({"ready-to-wear", "rtw", "collection"})

# Decade tokens: "90s" → (year_min, year_max).  Soft ±2 so "90s" includes Fall 2000.
_DECADE_RE = re.compile(r"\b(\d{2,4})s\b", re.IGNORECASE)
_DECADE_RANGES: dict = {
    "60": (1958, 1971),
    "70": (1968, 1981),
    "80": (1978, 1991),
    "90": (1988, 2001),
    "00": (1998, 2011),
    "10": (2008, 2021),
    "20": (2018, 2029),
}

# Designer-era phrases → (brand_lock or None, year_min or None, year_max or None).
# Matched case-insensitively as substrings before brand detection.
_ERA_TOKENS: list = [
    ("lagerfeld era", "Chanel", None, 2018),
    ("karl lagerfeld", "Chanel", None, 2018),
    ("virgil era", "Louis Vuitton", 2018, 2021),
    ("mcqueen era", "Alexander McQueen", None, 2010),
]


def parse_metadata_filters(query: str, known_brands: Optional[list] = None) -> dict:
    """
    Parse structural metadata tokens from a free-text query.

    Returns a dict:
      year:        int or None    — exact 4-digit year in range 1985–2026
      year_min:    int or None    — lower bound from a decade/era token
      year_max:    int or None    — upper bound from a decade/era token
      brand:       str or None    — matched brand name (from known_brands)
      season_code: str or None    — 'FW', 'SS', or 'Couture'
      residual:    str            — query with structural tokens removed
      ambiguous:   list[str]      — tokens that looked structural but were uncertain
    """
    text = query
    ambiguous: list = []
    year_min: Optional[int] = None
    year_max: Optional[int] = None

    # 0. Era phrases (before year / brand detection; longest phrase first)
    brand_lock: Optional[str] = None
    for phrase, era_brand, era_min, era_max in sorted(
        _ERA_TOKENS, key=lambda t: len(t[0]), reverse=True
    ):
        if phrase in text.lower():
            brand_lock = era_brand
            year_min = era_min
            year_max = era_max
            # Remove the era phrase from text
            text = re.sub(re.escape(phrase), " ", text, flags=re.IGNORECASE)
            break

    # 1. Detect exact 4-digit year
    year: Optional[int] = None
    for m in _YEAR_RE.finditer(text):
        y = int(m.group(1))
        if 1985 <= y <= 2026:
            year = y
            text = text[: m.start()] + text[m.end():]
            break  # take first qualifying year only

    # 1b. Detect decade token ("90s", "2000s") — only if no exact year found
    if year is None and year_min is None:
        dm = _DECADE_RE.search(text)
        if dm:
            full = dm.group(1)
            # "90s" → "90"; "2000s" → last two digits "00"; "2010s" → "10"
            decade_key = full[-2:] if len(full) >= 2 else full.zfill(2)
            if decade_key in _DECADE_RANGES:
                year_min, year_max = _DECADE_RANGES[decade_key]
                text = text[: dm.start()] + text[dm.end():]

    # 2. Detect brand (longest match wins to avoid 'Dior' shadowing 'Christian Dior')
    # Era-locked brand overrides detection but detected brand still allowed to match
    brand: Optional[str] = brand_lock
    if not brand:
        brands = sorted(known_brands or [], key=lambda b: len(b), reverse=True)
        q_lower = text.lower()
        for b in brands:
            if b.lower() in q_lower:
                brand = b
                text = re.sub(re.escape(b), "", text, flags=re.IGNORECASE)
                break
    else:
        # Remove brand_lock name from text if present
        if known_brands:
            for b in sorted(known_brands or [], key=lambda b: len(b), reverse=True):
                if b.lower() == (brand_lock or "").lower():
                    text = re.sub(re.escape(b), "", text, flags=re.IGNORECASE)
                    break

    # 3. Detect season tokens (word by word; multi-word "ready-to-wear" treated as stop)
    text = re.sub(r"\bready[-\s]to[-\s]wear\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\brtw\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bcollection\b", " ", text, flags=re.IGNORECASE)

    season_code: Optional[str] = None
    tokens = text.split()
    kept: list = []
    for tok in tokens:
        clean = tok.strip(".,;:").lower()
        if clean in _SEASON_MAP:
            sc = _SEASON_MAP[clean]
            if season_code and season_code != sc:
                # conflicting signals — flag ambiguous, keep first
                ambiguous.append(tok)
            else:
                season_code = sc
            # drop this token from residual
        else:
            kept.append(tok)

    residual = " ".join(kept)
    residual = re.sub(r"\s+", " ", residual).strip(" .,;")

    return {
        "year": year,
        "year_min": year_min,
        "year_max": year_max,
        "brand": brand,
        "season_code": season_code,
        "residual": residual,
        "ambiguous": ambiguous,
    }



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

# Accessory / embellishment stems (matched as substrings in enriched tags)
ACCESSORIES: frozenset = frozenset({
    "hardware", "embellish", "chain", "clasp", "buckle", "medallion",
    "pearl", "jewel", "bead", "sequin", "crystal", "rhinestone",
    "brooch", "hardware", "zipper", "toggle", "hook",
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
    Return {"colours": [...], "garments": [...], "silhouettes": [...], "accessories": [...]}
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

    # Accessories: match query tokens as prefixes of ACCESSORIES stems
    accessories: list[str] = []
    q_lower = query.lower()
    for stem in ACCESSORIES:
        if stem in q_lower:
            accessories.append(stem)
    accessories = list(dict.fromkeys(accessories))

    return {
        "colours": colours,
        "garments": garments,
        "silhouettes": silhouettes,
        "accessories": accessories,
    }


# ── Boost ─────────────────────────────────────────────────────────────────────

_COLOUR_INC = 0.08
_GARMENT_INC = 0.08
_SILHOUETTE_INC = 0.05
_ACCESSORY_INC = 0.06
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

    # Accessory match — check search_tags for hardware/embellishment/chain etc.
    accessories_q = attrs.get("accessories") or []
    if accessories_q:
        tag_blob = " ".join(
            (enriched.get("search_tags") or [])
            + (enriched.get("key_pieces") or [])
        ).lower()
        if tag_blob and _any_match(accessories_q, tag_blob):
            boost += _ACCESSORY_INC

    return min(boost, _BOOST_CAP)
