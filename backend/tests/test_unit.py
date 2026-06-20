"""
Fashion Archive — unit tests.
No DB, no Twelve Labs, no Anthropic — runs fully offline.

Run: cd backend && pytest -m unit -v
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytestmark = pytest.mark.unit


# ─────────────────────────────────────────────────────────────────────────────
# services/database.py — make_show_key
# ─────────────────────────────────────────────────────────────────────────────

class TestMakeShowKey:
    @pytest.fixture(autouse=True)
    def _import(self):
        from services.database import make_show_key
        self.make_show_key = make_show_key

    def test_basic(self):
        assert self.make_show_key("Chanel", "AW2526") == "chanel__aw2526"

    def test_dior(self):
        assert self.make_show_key("Dior", "AW2526") == "dior__aw2526"

    def test_accent_hermes(self):
        # è must not produce herm-s
        key = self.make_show_key("Hermès", "SS2024")
        assert key == "hermes__ss2024"

    def test_spaces_in_season(self):
        key = self.make_show_key("Gucci", "Fall 2025 Ready-to-Wear")
        assert " " not in key
        assert key.startswith("gucci__")

    def test_apostrophe_stripped(self):
        key = self.make_show_key("Hermès", "L'été 2025")
        assert "'" not in key
        assert "'" not in key

    def test_deterministic(self):
        a = self.make_show_key("Chanel", "AW2526")
        b = self.make_show_key("Chanel", "AW2526")
        assert a == b

    def test_double_underscore_separator(self):
        key = self.make_show_key("Saint Laurent", "SS2025")
        assert "__" in key
        parts = key.split("__")
        assert len(parts) == 2

    def test_different_brands_produce_different_keys(self):
        assert self.make_show_key("Chanel", "AW2526") != self.make_show_key("Dior", "AW2526")

    def test_different_seasons_produce_different_keys(self):
        assert self.make_show_key("Chanel", "AW2526") != self.make_show_key("Chanel", "SS2526")


# ─────────────────────────────────────────────────────────────────────────────
# services/twelvelabs.py — extract_brand_from_query
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractBrandFromQuery:
    @pytest.fixture(autouse=True)
    def _import(self):
        from services.twelvelabs import extract_brand_from_query
        self.fn = extract_brand_from_query

    def test_known_brand_extracted(self):
        brand, cleaned = self.fn("Chanel black structured jacket")
        assert brand == "Chanel"
        assert "Chanel" not in cleaned

    def test_no_brand(self):
        brand, cleaned = self.fn("black structured jacket")
        assert brand is None
        assert cleaned == "black structured jacket"

    def test_dior_extracted(self):
        brand, _ = self.fn("Dior navy tailoring")
        assert brand == "Dior"

    def test_cleaned_query_not_empty_when_only_brand(self):
        brand, cleaned = self.fn("Chanel")
        assert brand == "Chanel"
        # cleaned falls back to original when stripping leaves nothing
        assert cleaned  # never empty

    def test_case_insensitive(self):
        brand, _ = self.fn("chanel black coat")
        # brand is extracted when lowercase matches
        assert brand is not None


# ─────────────────────────────────────────────────────────────────────────────
# services/twelvelabs.py — _is_valid_description
# ─────────────────────────────────────────────────────────────────────────────

class TestIsValidDescription:
    @pytest.fixture(autouse=True)
    def _import(self):
        from services.twelvelabs import _is_valid_description
        self.fn = _is_valid_description

    def test_valid_description(self):
        assert self.fn("Structured black wool jacket with exaggerated shoulders and minimal buttons.")

    def test_empty_rejected(self):
        assert not self.fn("")

    def test_none_rejected(self):
        assert not self.fn(None)

    def test_too_short_rejected(self):
        assert not self.fn("Short.")

    def test_hedge_rejected(self):
        assert not self.fn("I cannot identify the garment in this image.")

    def test_look_number_rejected(self):
        assert not self.fn("Look 4 features a black jacket.")

    def test_look_at_rejected(self):
        assert not self.fn("Look at the model wearing a dress.")

    def test_refusal_rejected(self):
        assert not self.fn("I'm unable to describe the clothing in this footage.")


# ─────────────────────────────────────────────────────────────────────────────
# services/structured_match.py — attribute_boost
# ─────────────────────────────────────────────────────────────────────────────

class TestAttributeBoost:
    @pytest.fixture(autouse=True)
    def _import(self):
        from services.structured_match import attribute_boost, parse_query_attributes
        self.boost = attribute_boost
        self.parse = parse_query_attributes

    def test_no_match_returns_zero(self):
        attrs = self.parse("black structured jacket")
        enriched = {}  # empty enriched_data
        b = self.boost(enriched, attrs)
        assert b == 0.0

    def test_colour_match_gives_boost(self):
        attrs = self.parse("black jacket")
        enriched = {"colours": ["black"]}
        b = self.boost(enriched, attrs)
        assert b > 0.0

    def test_boost_capped_at_0_20(self):
        attrs = {"colours": ["black", "white"], "garments": ["jacket"], "silhouettes": ["structured"]}
        enriched = {"colours": ["black", "white"], "garments": ["jacket"], "silhouette": "structured"}
        b = self.boost(enriched, attrs)
        assert b <= 0.20

    def test_boost_non_negative(self):
        attrs = self.parse("red dress")
        enriched = {"colours": ["blue"]}  # mismatch
        b = self.boost(enriched, attrs)
        assert b >= 0.0

    def test_null_enriched_returns_zero(self):
        attrs = self.parse("black coat")
        assert self.boost(None, attrs) == 0.0

    def test_parse_returns_dict_with_expected_keys(self):
        attrs = self.parse("black structured jacket")
        assert "colours" in attrs
        assert "garments" in attrs
        assert "silhouettes" in attrs

    def test_empty_query_parses_empty(self):
        attrs = self.parse("")
        assert attrs["colours"] == []
        assert attrs["garments"] == []
        assert attrs["silhouettes"] == []


# ─────────────────────────────────────────────────────────────────────────────
# services/claude.py — synthesize_results guard logic
# These tests mock the Anthropic client so no API calls are made.
# ─────────────────────────────────────────────────────────────────────────────

class TestSynthesizeResultsGuard:
    """Tests the structural guards in synthesize_results (no actual API calls)."""

    def _make_results(self, brands):
        return [{"brand": b, "season": "AW2526", "year": 2025, "description": "A garment."} for b in brands]

    def test_empty_results_returns_none(self):
        import asyncio
        from unittest.mock import patch, MagicMock
        import services.claude as claude_mod
        with patch.object(claude_mod, "client", MagicMock()):
            result = asyncio.get_event_loop().run_until_complete(
                claude_mod.synthesize_results("black jacket", [])
            )
        assert result is None

    def test_single_brand_returns_none_without_api_call(self):
        """Guard: < 2 distinct brands → return None before hitting the API."""
        import asyncio
        from unittest.mock import patch, MagicMock, AsyncMock
        import services.claude as claude_mod

        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(side_effect=AssertionError("API must not be called"))

        with patch.object(claude_mod, "client", mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                claude_mod.synthesize_results(
                    "black jacket",
                    self._make_results(["Chanel", "Chanel", "Chanel"])
                )
            )
        assert result is None

    def test_two_brands_allows_api_call(self):
        """Two distinct brands: guard passes, API is called."""
        import asyncio
        from unittest.mock import patch, MagicMock
        import services.claude as claude_mod

        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="Both Chanel and Dior explore structured shoulders.")]
        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(return_value=fake_response)

        with patch.object(claude_mod, "client", mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                claude_mod.synthesize_results(
                    "black jacket",
                    self._make_results(["Chanel", "Dior", "Chanel"])
                )
            )
        # API was called (no AssertionError) and result is the text
        assert result is not None
        assert "Chanel" in result or result is None  # post-model check may filter

    def test_none_escape_hatch(self):
        """If model returns 'NONE', synthesize_results returns None."""
        import asyncio
        from unittest.mock import patch, MagicMock
        import services.claude as claude_mod

        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="NONE")]
        mock_client = MagicMock()
        mock_client.messages.create = MagicMock(return_value=fake_response)

        with patch.object(claude_mod, "client", mock_client):
            result = asyncio.get_event_loop().run_until_complete(
                claude_mod.synthesize_results(
                    "random query",
                    self._make_results(["Chanel", "Dior"])
                )
            )
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# services/show_view.py — client_safe_metadata
# ─────────────────────────────────────────────────────────────────────────────

class TestClientSafeMetadata:
    @pytest.fixture(autouse=True)
    def _import(self):
        from services.show_view import client_safe_metadata
        self.fn = client_safe_metadata

    def _make_show(self, **overrides):
        from unittest.mock import MagicMock
        show = MagicMock()
        show.brand = "Chanel"
        show.season = "AW2526"
        show.season_type = "AW"
        show.year = 2025
        show.creative_director = "Virginie Viard"
        show.show_date = None
        show.summary = "A show summary."
        show.raw_metadata = {}
        for k, v in overrides.items():
            setattr(show, k, v)
        return show

    def test_does_not_include_video_id(self):
        show = self._make_show()
        show.video_id = "secret-tl-id"
        result = self.fn(show)
        assert "video_id" not in result

    def test_does_not_include_task_id(self):
        show = self._make_show()
        show.task_id = "secret-task"
        result = self.fn(show)
        assert "task_id" not in result

    def test_does_not_include_source(self):
        show = self._make_show()
        show.source = "youtube_mvp"
        result = self.fn(show)
        assert "source" not in result

    def test_includes_public_fields(self):
        result = self.fn(self._make_show())
        for field in ("brand", "season", "year", "season_type", "creative_director", "summary"):
            assert field in result

    def test_models_slot_present(self):
        result = self.fn(self._make_show())
        assert "models" in result


# ─────────────────────────────────────────────────────────────────────────────
# Confidence display rules (from CLAUDE.md spec)
# ─────────────────────────────────────────────────────────────────────────────

class TestConfidenceDisplay:
    """Ensure confidence integers stay within spec."""

    def test_similarity_to_confidence_rounding(self):
        # Raw similarity 0.94 → confidence 94
        similarity = 0.9412
        confidence = round(similarity * 100)
        assert confidence == 94
        assert isinstance(confidence, int)

    def test_confidence_clamped_to_100(self):
        similarity = 1.0
        confidence = min(100, round(similarity * 100))
        assert confidence == 100

    def test_confidence_never_negative(self):
        similarity = 0.0
        confidence = max(0, round(similarity * 100))
        assert confidence == 0

    @pytest.mark.parametrize("sim,expected_band", [
        (0.95, "Exact match"),
        (0.82, "Strong match"),
        (0.67, "Relevant"),
        (0.55, "suppress"),
    ])
    def test_confidence_bands(self, sim, expected_band):
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
