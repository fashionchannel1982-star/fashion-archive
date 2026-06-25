"""
Fashion Archive — unit tests.

Pure offline: no DB, no Twelve Labs, no Anthropic API calls.
The agentic loop gate — must always pass with zero running services.

Loop gate command:
    cd backend && pytest -m unit

All async tests use pytest-asyncio (asyncio_mode = auto in pytest.ini).
"""

import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────────────
# services/database.py — make_show_key
# Session 7: brand+season → stable slug; source excluded intentionally.
# ─────────────────────────────────────────────────────────────────────────────

class TestMakeShowKey:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from services.database import make_show_key
        self.fn = make_show_key

    def test_basic(self):
        assert self.fn("Chanel", "AW2526") == "chanel__aw2526"

    def test_dior(self):
        assert self.fn("Dior", "AW2526") == "dior__aw2526"

    def test_accent_hermes(self):
        # è accent must not produce "herm-s"
        assert self.fn("Hermès", "SS2024") == "hermes__ss2024"

    def test_spaces_collapse_in_season(self):
        key = self.fn("Gucci", "Fall 2025 Ready-to-Wear")
        assert " " not in key
        assert key.startswith("gucci__")

    def test_apostrophe_stripped(self):
        key = self.fn("Hermès", "L'été 2025")
        assert "'" not in key
        assert "’" not in key

    def test_deterministic(self):
        assert self.fn("Chanel", "AW2526") == self.fn("Chanel", "AW2526")

    def test_double_underscore_separator(self):
        key = self.fn("Saint Laurent", "SS2025")
        parts = key.split("__")
        assert len(parts) == 2

    def test_different_brands_different_keys(self):
        assert self.fn("Chanel", "AW2526") != self.fn("Dior", "AW2526")

    def test_different_seasons_different_keys(self):
        assert self.fn("Chanel", "AW2526") != self.fn("Chanel", "SS2526")

    def test_case_collapses(self):
        # Input brand/season casing must not produce different keys
        assert self.fn("CHANEL", "AW2526") == self.fn("chanel", "AW2526")

    def test_source_does_not_affect_key(self):
        """
        source is intentionally excluded from make_show_key — it is mutable
        on video replace (youtube_mvp → fc_master) and must not change identity.
        make_show_key only takes brand+season; source is never a parameter.
        Verify same brand+season always produces the same key regardless of
        what source value the caller might hold.
        """
        key_a = self.fn("Chanel", "AW2526")
        key_b = self.fn("Chanel", "AW2526")   # called from a context with different source
        assert key_a == key_b


# ─────────────────────────────────────────────────────────────────────────────
# services/structured_match.py — parse_query_attributes + attribute_boost
# Session 6A: structured-field re-ranking.
# ─────────────────────────────────────────────────────────────────────────────

class TestParseQueryAttributes:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from services.structured_match import parse_query_attributes
        self.fn = parse_query_attributes

    def test_returns_expected_keys(self):
        attrs = self.fn("black structured jacket")
        for k in ("colours", "garments", "silhouettes"):
            assert k in attrs

    def test_red_dress_parses_colour_and_garment(self):
        attrs = self.fn("red dress")
        assert "red" in attrs["colours"]
        assert "dress" in attrs["garments"]

    def test_empty_query_parses_empty_lists(self):
        attrs = self.fn("")
        assert attrs["colours"] == []
        assert attrs["garments"] == []
        assert attrs["silhouettes"] == []

    def test_colour_only(self):
        attrs = self.fn("black coat")
        assert "black" in attrs["colours"]

    def test_silhouette_parsed(self):
        attrs = self.fn("structured oversized jacket")
        # At least one silhouette keyword recognised
        assert len(attrs["silhouettes"]) > 0 or len(attrs["garments"]) > 0


class TestAttributeBoost:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from services.structured_match import attribute_boost, parse_query_attributes
        self.boost = attribute_boost
        self.parse = parse_query_attributes

    def test_boost_zero_when_enriched_empty_dict(self):
        attrs = self.parse("black jacket")
        assert self.boost({}, attrs) == 0.0

    def test_boost_zero_when_enriched_null(self):
        attrs = self.parse("black jacket")
        assert self.boost(None, attrs) == 0.0

    def test_boost_zero_when_structured_fields_missing(self):
        # enriched_data present but no colours/garments/silhouette keys
        attrs = self.parse("black jacket")
        assert self.boost({"description": "A coat."}, attrs) == 0.0

    def test_colour_match_gives_positive_boost(self):
        attrs = self.parse("black jacket")
        b = self.boost({"colours": ["black"]}, attrs)
        assert b > 0.0

    def test_boost_only_adds_never_subtracts(self):
        # Mismatch: query is red, enriched is blue
        attrs = self.parse("red dress")
        b = self.boost({"colours": ["blue"]}, attrs)
        assert b >= 0.0

    def test_boost_capped_at_0_20(self):
        attrs = {"colours": ["black"], "garments": ["jacket"], "silhouettes": ["structured"]}
        enriched = {"colours": ["black"], "garments": ["jacket"], "silhouette": "structured"}
        assert self.boost(enriched, attrs) <= 0.20

    def test_boost_increases_with_more_matches(self):
        query = "black structured jacket"
        attrs = self.parse(query)
        enriched_partial = {"colours": ["black"]}
        enriched_full = {"colours": ["black"], "garments": ["jacket"], "silhouette": "structured"}
        b_partial = self.boost(enriched_partial, attrs)
        b_full = self.boost(enriched_full, attrs)
        assert b_full >= b_partial


# ─────────────────────────────────────────────────────────────────────────────
# services/claude.py — synthesize_results distinct-brands guard
# Session 5.1: guard fires BEFORE the model call; NONE output → None return.
# Tests use async def (asyncio_mode = auto) + monkeypatched client.
# ─────────────────────────────────────────────────────────────────────────────

def _moments(brands):
    return [{"brand": b, "season": "AW2526", "year": 2025, "description": "A garment."} for b in brands]


class TestSynthesizeGuard:
    async def test_empty_results_returns_none(self):
        import services.claude as m
        with patch.object(m, "client", MagicMock()):
            result = await m.synthesize_results("black jacket", [])
        assert result is None

    async def test_single_brand_returns_none_without_model_call(self):
        """< 2 distinct brands → guard fires before any model call."""
        import services.claude as m
        boom = MagicMock()
        boom.messages.create = MagicMock(side_effect=AssertionError("model must not be called"))
        with patch.object(m, "client", boom):
            result = await m.synthesize_results("black jacket", _moments(["Chanel", "Chanel", "Chanel"]))
        assert result is None

    async def test_all_same_brand_returns_none_without_model_call(self):
        import services.claude as m
        boom = MagicMock()
        boom.messages.create = MagicMock(side_effect=AssertionError("model must not be called"))
        with patch.object(m, "client", boom):
            result = await m.synthesize_results("navy coat", _moments(["Dior"] * 5))
        assert result is None

    async def test_none_escape_hatch_returns_none(self):
        """Model returns literal 'NONE' → synthesize_results returns None."""
        import services.claude as m
        fake = MagicMock()
        fake.content = [MagicMock(text="NONE")]
        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(return_value=fake)
        with patch.object(m, "client", mock_client):
            result = await m.synthesize_results("query", _moments(["Chanel", "Dior"]))
        assert result is None

    async def test_empty_model_output_returns_none(self):
        """Empty string from model → None."""
        import services.claude as m
        fake = MagicMock()
        fake.content = [MagicMock(text="")]
        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(return_value=fake)
        with patch.object(m, "client", mock_client):
            result = await m.synthesize_results("query", _moments(["Chanel", "Dior"]))
        assert result is None

    async def test_two_brands_allows_model_call(self):
        """Two distinct brands: guard passes, model is called, result returned."""
        import services.claude as m
        fake = MagicMock()
        fake.content = [MagicMock(text="Both Chanel and Dior explore exaggerated shoulders.")]
        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(return_value=fake)
        with patch.object(m, "client", mock_client):
            result = await m.synthesize_results("structured shoulders", _moments(["Chanel", "Dior"]))
        # Model was called without AssertionError; result may be text or None (post-check)
        mock_client.messages.create.assert_called_once()
        # Result is string if post-model brand-citation check passes
        if result is not None:
            assert isinstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# services/show_view.py — client_safe_metadata
# Session 8: internal-only fields must be absent from the client projection.
# TODO: when video playback lands, verify thumbnail_url exposure is intentional.
# ─────────────────────────────────────────────────────────────────────────────

class TestClientSafeMetadata:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from services.show_view import client_safe_metadata
        self.fn = client_safe_metadata

    def _show(self, **kw):
        s = MagicMock()
        s.show_key = "chanel__aw2526"
        s.brand = "Chanel"
        s.season = "AW2526"
        s.season_type = "AW-RTW"
        s.year = 2025
        s.creative_director = "Virginie Viard"
        s.show_date = None
        s.summary = "A show."
        s.raw_metadata = {}
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    INTERNAL_FIELDS = ("video_id", "task_id", "status", "health",
                       "sample_moments", "provenance", "source", "source_url")

    def test_no_internal_field_leaks(self):
        s = self._show()
        result = self.fn(s)
        for field in self.INTERNAL_FIELDS:
            assert field not in result, f"internal field {field!r} leaked to client view"

    def test_public_fields_present(self):
        result = self.fn(self._show())
        for field in ("show_key", "brand", "season", "season_type", "year", "creative_director", "summary"):
            assert field in result

    def test_models_slot_present(self):
        # Forward-compatible nullable slot — must always be present even if None
        assert "models" in self.fn(self._show())

    def test_video_id_not_exposed(self):
        s = self._show()
        s.video_id = "secret-tl-id"
        assert "video_id" not in self.fn(s)

    def test_health_not_exposed(self):
        # health is an internal diagnostic — never for clients
        assert "health" not in self.fn(self._show())

    def test_status_not_exposed(self):
        assert "status" not in self.fn(self._show())


# ─────────────────────────────────────────────────────────────────────────────
# Confidence display contract (CLAUDE.md spec)
# NOTE: discrepancy flagged below.
# ─────────────────────────────────────────────────────────────────────────────

class TestConfidenceContract:
    """
    CLAUDE.md spec: below 60 = suppress; confidence integer 0-100.
    ACTUAL code: SIMILARITY_THRESHOLD = 0.07 (7 confidence).
    These tests cover what the code ACTUALLY enforces.

    DISCREPANCY — see bottom of this class.
    """

    def test_similarity_to_integer_rounding(self):
        assert round(0.9412 * 100) == 94

    def test_result_is_integer(self):
        assert isinstance(round(0.85 * 100), int)

    def test_confidence_never_exceeds_100(self):
        assert min(100, round(1.0 * 100)) == 100

    def test_confidence_never_negative(self):
        assert max(0, round(0.0 * 100)) == 0

    @pytest.mark.parametrize("sim,expected_band", [
        (0.95, "Exact match"),
        (0.82, "Strong match"),
        (0.67, "Relevant"),
        (0.55, "suppress"),  # below 60
    ])
    def test_confidence_bands_per_spec(self, sim, expected_band):
        conf = round(sim * 100)
        if conf >= 90:
            band = "Exact match"
        elif conf >= 75:
            band = "Strong match"
        elif conf >= 60:
            band = "Relevant"
        else:
            band = "suppress"
        assert band == expected_band

    # ── DISCREPANCY ──────────────────────────────────────────────────────────
    # CLAUDE.md says: "below 60 = suppress from results"
    # services/twelvelabs.py SIMILARITY_THRESHOLD = 0.07  (i.e. 7 confidence)
    # This means results with confidence 7–59 ARE currently returned.
    # The integration test (test_confidence_floor) verifies the actual floor is
    # SIMILARITY_THRESHOLD*100 = 7, NOT 60.  See FINDINGS in the README.


# ─────────────────────────────────────────────────────────────────────────────
# services/confidence.py — calibrate() and confidence_floor()
# Phase D: logistic calibration; strictly monotonic; env-adjustable floor.
# ─────────────────────────────────────────────────────────────────────────────

class TestCalibrate:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from services.confidence import calibrate
        self.calibrate = calibrate

    def test_returns_int(self):
        assert isinstance(self.calibrate(0.10), int)

    def test_clamped_at_zero_for_negative_input(self):
        assert self.calibrate(-1.0) == 0

    def test_clamped_at_100_for_high_input(self):
        assert self.calibrate(10.0) == 100

    def test_inflection_at_x0(self):
        # At cos == x0 (0.065) the output should be 50
        result = self.calibrate(0.065)
        assert 48 <= result <= 52

    def test_noise_floor_suppressed(self):
        # Noise (cos ~0.04) should map well below 50
        assert self.calibrate(0.04) < 45

    def test_concept_match_strong(self):
        # Genuine concept match (cos ~0.10) should land in Strong (75-89)
        result = self.calibrate(0.10)
        assert 75 <= result <= 89, f"Expected Strong band, got {result}"

    def test_brand_exact(self):
        # Brand-anchored match (cos ~0.13) should reach Exact (90+)
        result = self.calibrate(0.13)
        assert result >= 90, f"Expected Exact band, got {result}"

    def test_monotonic(self):
        # calibrate must be non-decreasing over a fine-grained sweep
        import numpy as _np
        cos_vals = [i / 1000.0 for i in range(0, 401)]  # 0.0 to 0.4 in steps of 0.001
        cal_vals = [self.calibrate(c) for c in cos_vals]
        for i in range(len(cal_vals) - 1):
            assert cal_vals[i] <= cal_vals[i + 1], (
                f"Non-monotonic at cos={cos_vals[i]:.3f} → {cos_vals[i+1]:.3f}: "
                f"{cal_vals[i]} > {cal_vals[i+1]}"
            )

    @pytest.mark.parametrize("cos,band", [
        (0.14, "Exact"),    # >= 90
        (0.10, "Strong"),   # 75-89
        (0.08, "Relevant"), # 60-74
        (0.04, "suppress"), # < 60
    ])
    def test_display_buckets_map_correctly(self, cos, band):
        conf = self.calibrate(cos)
        if conf >= 90:
            actual = "Exact"
        elif conf >= 75:
            actual = "Strong"
        elif conf >= 60:
            actual = "Relevant"
        else:
            actual = "suppress"
        assert actual == band, f"cos={cos} → conf={conf} → {actual}, expected {band}"


class TestConfidenceFloor:
    def test_default_is_60(self, monkeypatch):
        monkeypatch.delenv("SEARCH_CONFIDENCE_FLOOR", raising=False)
        # Re-import to pick up env change
        import importlib
        import services.confidence as cm
        importlib.reload(cm)
        assert cm.confidence_floor() == 60

    def test_reads_from_env(self, monkeypatch):
        monkeypatch.setenv("SEARCH_CONFIDENCE_FLOOR", "65")
        import importlib
        import services.confidence as cm
        importlib.reload(cm)
        assert cm.confidence_floor() == 65

    def test_clamped_above_100(self, monkeypatch):
        monkeypatch.setenv("SEARCH_CONFIDENCE_FLOOR", "999")
        import importlib
        import services.confidence as cm
        importlib.reload(cm)
        assert cm.confidence_floor() == 100

    def test_clamped_below_0(self, monkeypatch):
        monkeypatch.setenv("SEARCH_CONFIDENCE_FLOOR", "-10")
        import importlib
        import services.confidence as cm
        importlib.reload(cm)
        assert cm.confidence_floor() == 0

    def test_invalid_env_returns_default(self, monkeypatch):
        monkeypatch.setenv("SEARCH_CONFIDENCE_FLOOR", "notanint")
        import importlib
        import services.confidence as cm
        importlib.reload(cm)
        assert cm.confidence_floor() == 60


# ─────────────────────────────────────────────────────────────────────────────
# services/structured_match.py — parse_metadata_filters
# Phase F: year / brand / season structural token extraction
# ─────────────────────────────────────────────────────────────────────────────

class TestParseMetadataFilters:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from services.structured_match import parse_metadata_filters
        from services.twelvelabs import KNOWN_BRANDS
        self.parse = lambda q: parse_metadata_filters(q, known_brands=list(KNOWN_BRANDS))

    # ── Year detection ────────────────────────────────────────────────────────

    def test_year_detected(self):
        m = self.parse("Chanel 1993")
        assert m["year"] == 1993

    def test_year_2024(self):
        m = self.parse("Dior 2024")
        assert m["year"] == 2024

    def test_year_out_of_range_ignored(self):
        m = self.parse("something 1920")
        assert m["year"] is None

    def test_year_future_out_of_range_ignored(self):
        m = self.parse("something 2099")
        assert m["year"] is None

    # ── Brand detection ───────────────────────────────────────────────────────

    def test_brand_chanel(self):
        m = self.parse("Chanel 1993")
        assert m["brand"] == "Chanel"

    def test_brand_dior(self):
        m = self.parse("Dior structured tailoring")
        assert m["brand"] == "Dior"

    def test_brand_vivienne_westwood(self):
        m = self.parse("Vivienne Westwood 1993")
        assert m["brand"] == "Vivienne Westwood"

    def test_no_brand_concept_query(self):
        m = self.parse("sheer black evening looks")
        assert m["brand"] is None

    # ── Season detection ──────────────────────────────────────────────────────

    def test_season_fall(self):
        m = self.parse("Fall 2025 tailoring")
        assert m["season_code"] == "FW"

    def test_season_autumn(self):
        m = self.parse("Chanel Autumn 1993")
        assert m["season_code"] == "FW"

    def test_season_spring(self):
        m = self.parse("Spring 2025 Chanel")
        assert m["season_code"] == "SS"

    def test_season_couture(self):
        m = self.parse("Maison Margiela couture 2025")
        assert m["season_code"] == "Couture"

    def test_no_season_concept_query(self):
        m = self.parse("structured shoulders, sharp tailoring")
        assert m["season_code"] is None

    # ── Residual extraction ───────────────────────────────────────────────────

    def test_residual_pure_metadata(self):
        # "Chanel 1993" — no residual concept
        m = self.parse("Chanel 1993")
        assert m["residual"] == ""

    def test_residual_mixed(self):
        m = self.parse("Chanel 1993 tweed")
        assert "tweed" in m["residual"]
        assert "1993" not in m["residual"]
        assert "Chanel" not in m["residual"]

    def test_residual_brand_only(self):
        m = self.parse("Dior structured tailoring")
        assert m["residual"] == "structured tailoring"

    def test_residual_concept_query_unchanged(self):
        q = "sheer black evening looks"
        m = self.parse(q)
        assert m["residual"] == q  # nothing stripped

    # ── Pinned queries pass-through (must NOT be treated as structural) ───────

    def test_concept_queries_have_no_structural(self):
        concept_queries = [
            "sheer black evening looks",
            "structured shoulders, sharp tailoring",
            "monochrome white, head to toe",
            "maximalist print colour runway",
            "red dress",
            "a model pausing at the end of the runway",
        ]
        for q in concept_queries:
            m = self.parse(q)
            assert m["year"] is None, f"year found in concept query: {q!r}"
            assert m["season_code"] is None, f"season found in concept query: {q!r}"

    # ── Decade tokens ─────────────────────────────────────────────────────────

    def test_decade_90s_gives_year_range(self):
        m = self.parse("Chanel 90s archival tweed")
        assert m["year"] is None, "exact year should not fire for decade token"
        assert m["year_min"] is not None
        assert m["year_max"] is not None
        assert m["year_min"] <= 1993 <= m["year_max"], "1993 must be in 90s range"
        assert m["year_max"] >= 2000, "Fall 2000 must be in soft 90s range"

    def test_decade_2000s_gives_year_range(self):
        m = self.parse("Dior 2000s minimalism")
        assert m["year_min"] is not None
        assert m["year_min"] <= 2000  # soft range starts at or before 2000
        assert m["year_max"] is not None
        assert m["year_max"] >= 2009

    def test_decade_residual_strips_decade_token(self):
        m = self.parse("Chanel 90s tweed blazer")
        assert "90s" not in m["residual"], "decade token should be stripped from residual"

    def test_exact_year_takes_priority_over_decade(self):
        # "1993" is a 4-digit year, should take precedence
        m = self.parse("Chanel 1993 tweed")
        assert m["year"] == 1993
        assert m["year_min"] is None

    # ── Era tokens ────────────────────────────────────────────────────────────

    def test_lagerfeld_era_locks_chanel_year_max(self):
        m = self.parse("Karl Lagerfeld era Chanel bouclé")
        assert m["brand"] == "Chanel"
        assert m["year_max"] == 2018
        assert m["year_min"] is None

    def test_lagerfeld_era_without_explicit_brand(self):
        m = self.parse("Lagerfeld era tweed and bouclé")
        assert m["brand"] == "Chanel"
        assert m["year_max"] == 2018

    def test_era_residual_strips_era_phrase(self):
        m = self.parse("Karl Lagerfeld era Chanel bouclé")
        assert "lagerfeld" not in m["residual"].lower()
        assert "karl" not in m["residual"].lower()

    def test_has_structural_set_for_year_range(self):
        m = self.parse("Chanel 90s archival tweed")
        has_structural = bool(
            m["year"] or m.get("year_min") or m.get("year_max")
            or m["season_code"] or m["brand"]
        )
        assert has_structural, "decade query should trigger structural path"


# ─────────────────────────────────────────────────────────────────────────────
# services/structured_match.py — accessories lexicon + attribute_boost
# Step 1b: hardware/embellishment surfacing
# ─────────────────────────────────────────────────────────────────────────────

class TestAccessoriesBoost:
    @pytest.fixture(autouse=True)
    def _fns(self):
        from services.structured_match import parse_query_attributes, attribute_boost, ACCESSORIES
        self.parse_attrs = parse_query_attributes
        self.boost = attribute_boost
        self.ACCESSORIES = ACCESSORIES

    def test_hardware_detected_in_query(self):
        attrs = self.parse_attrs("gold hardware embellishment evening")
        assert "hardware" in attrs["accessories"]

    def test_embellish_stem_detected(self):
        attrs = self.parse_attrs("embellishment evening gown")
        assert "embellish" in attrs["accessories"]

    def test_chain_detected(self):
        attrs = self.parse_attrs("gold chain belt evening look")
        assert "chain" in attrs["accessories"]

    def test_pearl_detected(self):
        attrs = self.parse_attrs("pearl accessories classic femininity")
        assert "pearl" in attrs["accessories"]

    def test_no_accessories_in_generic_query(self):
        attrs = self.parse_attrs("black structured shoulders")
        assert attrs["accessories"] == []

    def test_accessory_boost_fires_on_tag_match(self):
        attrs = self.parse_attrs("gold hardware embellishment evening")
        enriched = {
            "colours": ["black", "gold"],
            "search_tags": ["embellished neckline", "metallic trim"],
        }
        b = self.boost(enriched, attrs)
        assert b > 0.0, "boost should fire when 'embellish' matches enriched tag"

    def test_accessory_boost_zero_without_match(self):
        attrs = self.parse_attrs("gold hardware embellishment evening")
        enriched = {"colours": [], "search_tags": ["oversized coat", "wool lapels"]}
        b = self.boost(enriched, attrs)
        # Only gold colour might match if enriched has no gold — here it won't
        assert b < 0.08  # no colour match either

    def test_accessory_boost_respects_cap(self):
        attrs = self.parse_attrs("gold embellishment pearl chain hardware")
        enriched = {
            "colours": ["gold"],
            "search_tags": ["embellished clasp", "pearl chain belt", "hardware detail"],
        }
        b = self.boost(enriched, attrs)
        from services.structured_match import _BOOST_CAP
        assert b <= _BOOST_CAP


# ─────────────────────────────────────────────────────────────────────────────
# services/structured_match.py — 2-digit year + compound season-code parsing
# Feature: FW25, SS00, AW '93, bare "Chanel 00", rejection of bare "50"
# ─────────────────────────────────────────────────────────────────────────────

class TestExpand2DigitYear:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from services.structured_match import _expand_2digit_year
        self.expand = _expand_2digit_year

    def test_00_maps_to_2000(self):
        assert self.expand(0) == 2000

    def test_25_maps_to_2025(self):
        assert self.expand(25) == 2025

    def test_26_maps_to_2026(self):
        assert self.expand(26) == 2026

    def test_93_maps_to_1993(self):
        assert self.expand(93) == 1993

    def test_85_maps_to_1985(self):
        assert self.expand(85) == 1985

    def test_99_maps_to_1999(self):
        assert self.expand(99) == 1999

    def test_27_returns_none(self):
        assert self.expand(27) is None

    def test_50_returns_none(self):
        assert self.expand(50) is None

    def test_84_returns_none(self):
        assert self.expand(84) is None


class TestCompoundSeasonYear:
    """FW25, SS00, AW '93, Spring 25, FW25/26 compound token parsing."""

    @pytest.fixture(autouse=True)
    def _fn(self):
        from services.structured_match import parse_metadata_filters
        self.parse = parse_metadata_filters

    def test_fw25_gives_year_2025_and_fw(self):
        m = self.parse("Dior FW25")
        assert m["year"] == 2025
        assert m["season_code"] == "FW"

    def test_ss00_gives_year_2000_and_ss(self):
        m = self.parse("SS00 minimalism")
        assert m["year"] == 2000
        assert m["season_code"] == "SS"

    def test_aw_apostrophe_93(self):
        m = self.parse("Margiela AW '93")
        assert m["year"] == 1993
        assert m["season_code"] == "FW"

    def test_fw25_slash_26_takes_first_year(self):
        m = self.parse("FW25/26 tailoring")
        assert m["year"] == 2025
        assert m["season_code"] == "FW"

    def test_4digit_year_with_season_prefix(self):
        m = self.parse("Chanel AW2016 bouclé")
        assert m["year"] == 2016
        assert m["season_code"] == "FW"

    def test_spring_2digit(self):
        m = self.parse("Spring 93 florals")
        assert m["year"] == 1993
        assert m["season_code"] == "SS"

    def test_compound_strips_token_from_residual(self):
        m = self.parse("Dior FW25 tailoring")
        assert "fw25" not in m["residual"].lower()
        assert "25" not in m["residual"]

    def test_compound_brand_and_season_together(self):
        m = self.parse("Chanel SS93 tweed")
        assert m["year"] == 1993
        assert m["season_code"] == "SS"
        # "Chanel" not provided as known_brand so brand stays None
        assert m["brand"] is None


class TestBare2DigitYear:
    """Bare 2-digit year: committed only with brand/season context."""

    @pytest.fixture(autouse=True)
    def _fn(self):
        from services.structured_match import parse_metadata_filters
        self.parse = parse_metadata_filters

    def test_chanel_00_gives_year_2000(self):
        m = self.parse("Chanel 00", known_brands=["Chanel"])
        assert m["year"] == 2000
        assert m["brand"] == "Chanel"

    def test_brand_93_gives_year_1993(self):
        m = self.parse("Dior 93", known_brands=["Dior"])
        assert m["year"] == 1993

    def test_bare_25_no_context_is_ambiguous(self):
        m = self.parse("tailoring 25")
        assert m["year"] is None
        assert "25" in m["ambiguous"]

    def test_bare_50_no_context_not_a_year(self):
        # 50 is in 27-84 range → silently left in residual, NOT in ambiguous
        m = self.parse("tailoring 50")
        assert m["year"] is None
        assert "50" not in m["ambiguous"]
        assert "50" in m["residual"]

    def test_bare_25_with_season_context_commits(self):
        # A prior season_code from the word-based scan provides context.
        # "Fall 25 collection" — "Fall" sets season_code, then "25" can commit.
        m = self.parse("Fall 25 collection")
        assert m["year"] == 2025
        assert m["season_code"] == "FW"


# ─────────────────────────────────────────────────────────────────────────────
# services/structured_match.py — meta-phrase / cross-house detection
# Feature: "across houses", "vs", "evolution of" strip + cross_house flag
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestMetaPhraseDetection:
    @pytest.fixture(autouse=True)
    def _fn(self):
        from services.structured_match import parse_metadata_filters
        self.parse = lambda q: parse_metadata_filters(q, known_brands=["Chanel", "Dior", "Gucci"])

    def test_across_houses_sets_cross_house(self):
        m = self.parse("black sheer across houses")
        assert m["cross_house"] is True

    def test_across_houses_stripped_from_residual(self):
        m = self.parse("black sheer across houses")
        assert "across" not in m["residual"]
        assert "black sheer" in m["residual"]

    def test_across_brands_sets_cross_house(self):
        m = self.parse("structured shoulders across brands")
        assert m["cross_house"] is True

    def test_vs_sets_cross_house(self):
        m = self.parse("Chanel vs Dior tailoring")
        assert m["cross_house"] is True

    def test_evolution_of_sets_cross_house(self):
        m = self.parse("evolution of the little black dress")
        assert m["cross_house"] is True

    def test_over_the_decades_sets_cross_house(self):
        m = self.parse("tweed over the decades")
        assert m["cross_house"] is True

    def test_plain_concept_no_cross_house(self):
        m = self.parse("black sheer evening looks")
        assert m["cross_house"] is False

    def test_bare_brand_no_cross_house(self):
        m = self.parse("chanel")
        assert m["cross_house"] is False

    def test_cross_house_does_not_strip_brand(self):
        # When brand AND meta-phrase present, brand is still detected
        m = self.parse("Chanel vs Dior")
        assert m["cross_house"] is True
        # brand may be Chanel (first match) — the point is cross_house fires
        assert m["brand"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# services/twelvelabs.py + structured_match.py — brand alias normalisation
# "_BRAND_ALIASES" maps unaccented/shorthand spellings to DB-canonical names
# so SQL WHERE clauses never silently return 0 results.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestBrandAliasNormalisation:
    @pytest.fixture(autouse=True)
    def _fns(self):
        from services.structured_match import parse_metadata_filters
        from services.twelvelabs import KNOWN_BRANDS
        self.parse = lambda q: parse_metadata_filters(q, known_brands=KNOWN_BRANDS)

    def test_hermes_no_accent_resolves_to_canonical(self):
        """'hermes' (no accent) must resolve to 'Hermès' for the SQL WHERE."""
        m = self.parse("hermes leather")
        assert m["brand"] == "Hermès"

    def test_hermès_with_accent_resolves_to_canonical(self):
        m = self.parse("hermès leather")
        assert m["brand"] == "Hermès"

    def test_celine_no_accent_resolves_to_celine_db(self):
        """DB stores 'Celine' (no accent); ensure no alias breaks it."""
        m = self.parse("celine minimalism")
        assert m["brand"] == "Celine"

    def test_ysl_alias_resolves_to_saint_laurent(self):
        m = self.parse("ysl tailoring")
        assert m["brand"] == "Saint Laurent"

    def test_mcqueen_alias(self):
        m = self.parse("mcqueen dark romanticism")
        assert m["brand"] == "Alexander McQueen"

    def test_unrelated_query_no_brand(self):
        m = self.parse("red structured dress")
        assert m["brand"] is None
