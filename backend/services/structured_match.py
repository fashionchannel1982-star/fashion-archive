"""
Structured-field re-ranking for search.
Pure functions, no I/O. Called post-retrieval to reorder candidates.
"""

import re
from typing import Optional

# ── Metadata filter parsing ───────────────────────────────────────────────────

_YEAR_RE = re.compile(r"\b(\d{4})\b")

# ── Compound-season regex extended ───────────────────────────────────────────
# Covers: FW25, AW25, SS24, HC25 (Haute Couture), resort25, cruise25
# Also matches bare season words used as prefixes before a year in step 1a.

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
    "resort": "SS",    # Resort / Cruise lands between SS and FW; map to SS for filtering
    "cruise": "SS",
    "couture": "Couture",
    "haute": "Couture",
    "hc": "Couture",
}

# Structural stop-words removed from residual (but not mapped to season)
_STRUCTURAL_STOPS: frozenset = frozenset({"ready-to-wear", "rtw", "collection"})

# Decade tokens: "90s" → (year_min, year_max).  Soft ±2 so "90s" includes Fall 2000.
# Compound season+year tokens: FW25, SS00, AW '93, Spring 25, FW25/26
# Group 1 = season word/abbrev, Group 2 = 2- or 4-digit year
_SEASON_YY_RE = re.compile(
    r"(?<!\w)"
    r"(fw|aw|a-w|ss|s-s|hc|fall|autumn|winter|spring|summer|resort|cruise|couture)"
    r"[\s’’’]*"
    r"(\d{4}|\d{2})"
    r"(?:/\d{2,4})?"   # optional /YY or /YYYY range — discard, take first year
    r"(?!\w)",
    re.IGNORECASE,
)

# Season abbreviation/word → canonical code for _SEASON_YY_RE results
_SEASON_CODE_MAP: dict = {
    "fw": "FW", "aw": "FW", "a-w": "FW",
    "fall": "FW", "autumn": "FW", "winter": "FW",
    "ss": "SS", "s-s": "SS",
    "spring": "SS", "summer": "SS",
    "resort": "SS", "cruise": "SS",
    "hc": "Couture",
    "couture": "Couture",
}

# Bare 2-digit number not adjacent to other digits
_BARE_2DIGIT_RE = re.compile(r"(?<!\d)\b(\d{2})\b(?!\d)")

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

# Cross-house / comparison phrases that signal brand diversity intent.
# Matched before brand detection; stripped from the query so the residual
# embeds only the visual concept.  Their presence sets cross_house=True.
_META_PHRASE_RE = re.compile(
    r"\b(across\s+(?:houses|brands)|over\s+the\s+decades?|evolution\s+of"
    r"|compar(?:e[sd]?|ing)|vs\.?|versus|through\s+the\s+(?:years?|decades?))\b",
    re.IGNORECASE,
)

# Designer-era phrases → (brand_lock or None, year_min or None, year_max or None).
# Matched case-insensitively as substrings before brand detection.
_ERA_TOKENS: list = [
    ("lagerfeld era", "Chanel", None, 2018),
    ("karl lagerfeld", "Chanel", None, 2018),
    ("virgil era", "Louis Vuitton", 2018, 2021),
    ("mcqueen era", "Alexander McQueen", None, 2010),
]


def _expand_2digit_year(yy: int) -> Optional[int]:
    """Map a 2-digit year to 4-digit using corpus range.
    00–26 → 2000–2026;  85–99 → 1985–1999;  27–84 → None (out of corpus)."""
    if 0 <= yy <= 26:
        return 2000 + yy
    if 85 <= yy <= 99:
        return 1900 + yy
    return None


def parse_metadata_filters(query: str, known_brands: Optional[list] = None) -> dict:
    """
    Parse structural metadata tokens from a free-text query.

    Returns a dict:
      year:        int or None    — exact year (4-digit or resolved 2-digit)
      year_min:    int or None    — lower bound from a decade/era token
      year_max:    int or None    — upper bound from a decade/era token
      brand:       str or None    — matched brand name (from known_brands)
      season_code: str or None    — 'FW', 'SS', or 'Couture'
      residual:    str            — query with structural tokens removed
      ambiguous:   list[str]      — tokens that looked structural but were uncertain

    2-digit year disambiguation (requires brand or season context):
      00–26 → 2000–2026;  85–99 → 1985–1999;  27–84 → not a year.
    Compound season codes (FW25, SS00, AW '93, FW25/26) are parsed in one pass.
    """
    text = query
    ambiguous: list = []
    year_min: Optional[int] = None
    year_max: Optional[int] = None
    season_code: Optional[str] = None  # may be set by step 1a before brand detection

    # 0a. Meta-phrases: cross-house/comparison intent (strip before embedding)
    cross_house = bool(_META_PHRASE_RE.search(text))
    if cross_house:
        text = _META_PHRASE_RE.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip()

    # 0. Era phrases (before year / brand detection; longest phrase first)
    brand_lock: Optional[str] = None
    for phrase, era_brand, era_min, era_max in sorted(
        _ERA_TOKENS, key=lambda t: len(t[0]), reverse=True
    ):
        if phrase in text.lower():
            brand_lock = era_brand
            year_min = era_min
            year_max = era_max
            text = re.sub(re.escape(phrase), " ", text, flags=re.IGNORECASE)
            break

    # 1a. Compound season+year tokens: FW25, SS00, AW '93, Spring 25, FW25/26
    #     Must run before the bare-digit passes so fused tokens aren't double-counted.
    year: Optional[int] = None
    csm = _SEASON_YY_RE.search(text)
    if csm:
        raw_season = csm.group(1).lower()
        raw_year_str = csm.group(2)
        raw_year_int = int(raw_year_str)
        if len(raw_year_str) == 4:
            y4 = raw_year_int if 1985 <= raw_year_int <= 2026 else None
        else:
            y4 = _expand_2digit_year(raw_year_int)
        if y4 is not None:
            year = y4
            sc = _SEASON_CODE_MAP.get(raw_season)
            if sc and season_code is None:
                season_code = sc
            text = text[: csm.start()] + " " + text[csm.end():]

    # 1. Detect exact 4-digit year (skip if already set by step 1a)
    if year is None:
        for m in _YEAR_RE.finditer(text):
            y = int(m.group(1))
            if 1985 <= y <= 2026:
                year = y
                text = text[: m.start()] + text[m.end():]
                break

    # 1b. Detect decade token ("90s", "2000s") — only if no year found yet
    if year is None and year_min is None:
        dm = _DECADE_RE.search(text)
        if dm:
            full = dm.group(1)
            decade_key = full[-2:] if len(full) >= 2 else full.zfill(2)
            if decade_key in _DECADE_RANGES:
                year_min, year_max = _DECADE_RANGES[decade_key]
                text = text[: dm.start()] + text[dm.end():]

    # 2. Detect brand (longest match wins to avoid 'Dior' shadowing 'Christian Dior').
    #    Also checks _BRAND_ALIASES so "hermes" → "Hermès" (DB-canonical spelling).
    brand: Optional[str] = brand_lock
    if not brand:
        from services.twelvelabs import _BRAND_ALIASES
        all_candidates = sorted(
            list(known_brands or []) + list(_BRAND_ALIASES.keys()),
            key=len,
            reverse=True,
        )
        q_lower = text.lower()
        for b in all_candidates:
            if b.lower() in q_lower:
                brand = _BRAND_ALIASES.get(b.lower(), b)
                text = re.sub(re.escape(b), "", text, flags=re.IGNORECASE)
                break
    else:
        if known_brands:
            for b in sorted(known_brands or [], key=lambda b: len(b), reverse=True):
                if b.lower() == (brand_lock or "").lower():
                    text = re.sub(re.escape(b), "", text, flags=re.IGNORECASE)
                    break

    # 3. Detect season tokens word-by-word (fallback when not set by step 1a)
    text = re.sub(r"\bready[-\s]to[-\s]wear\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\brtw\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bcollection\b", " ", text, flags=re.IGNORECASE)

    tokens = text.split()
    kept: list = []
    for tok in tokens:
        clean = tok.strip(".,;:").lower()
        if clean in _SEASON_MAP:
            sc = _SEASON_MAP[clean]
            if season_code is None:
                season_code = sc
            elif season_code != sc:
                ambiguous.append(tok)
            # drop from residual either way
        else:
            kept.append(tok)

    residual = " ".join(kept)
    residual = re.sub(r"\s+", " ", residual).strip(" .,;")

    # 3b. Bare 2-digit year — conservative: only commit when brand or season_code
    #     gives unambiguous context.  27–84 is out of corpus; just leave in residual.
    if year is None and year_min is None:
        bm = _BARE_2DIGIT_RE.search(residual)
        if bm:
            yy = int(bm.group(1))
            y4 = _expand_2digit_year(yy)
            if y4 is not None:
                if brand or season_code:
                    year = y4
                    residual = residual[: bm.start()] + residual[bm.end():]
                    residual = re.sub(r"\s+", " ", residual).strip(" .,;")
                else:
                    # plausible year but no context — mark ambiguous, keep in residual
                    ambiguous.append(bm.group(0))
            # if y4 is None (27–84) leave silently in residual — not a year candidate

    return {
        "year": year,
        "year_min": year_min,
        "year_max": year_max,
        "brand": brand,
        "season_code": season_code,
        "residual": residual,
        "ambiguous": ambiguous,
        "cross_house": cross_house,
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
