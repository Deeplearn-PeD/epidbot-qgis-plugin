"""Tests for the pure helpers of :mod:`core.layer_export`."""

import json

import pytest

from core.layer_export import (DEFAULT_INLINE_BUDGET, LayerExportError,
                               build_identifiers_geojson, compact_json,
                               over_budget, pick_join_key, size_bytes)


class TestPickJoinKey:
    """Semantic join-key detection for attribute write-back."""

    def test_cd_mun_wins_by_priority(self):
        assert pick_join_key(["nome", "uf", "CD_MUN"]) == "CD_MUN"

    def test_case_insensitive_match_returns_original_spelling(self):
        assert pick_join_key(["Municipality_Code"]) == "Municipality_Code"

    def test_fid_like_ids_lose_to_semantic_keys(self):
        names = ["id", "gid", "co_municip"]
        assert pick_join_key(names) == "co_municip"

    def test_plain_id_is_last_resort(self):
        assert pick_join_key(["name", "id"]) == "id"

    def test_no_candidates(self):
        assert pick_join_key(["nome", "casos"]) is None

    def test_empty(self):
        assert pick_join_key([]) is None


class TestIdentifiersGeojson:
    """Geometry-less identifier payloads for write-back joins."""

    def test_structure_and_ids(self):
        payload = build_identifiers_geojson([
            (1, {"CD_MUN": "1100205"}),
            (2, {"CD_MUN": "1200301"}),
        ])
        data = json.loads(payload)
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 2
        assert data["features"][0]["id"] == 1
        assert data["features"][0]["properties"] == {"CD_MUN": "1100205"}
        assert "geometry" not in data["features"][0]

    def test_codes_stay_strings(self):
        """Leading-zero municipality codes must survive round-trips."""
        payload = build_identifiers_geojson([(1, {"CD_MUN": "0500205"})])
        assert json.loads(payload)["features"][0]["properties"]["CD_MUN"] \
            == "0500205"

    def test_compact_serialization(self):
        payload = build_identifiers_geojson([(1, {"a": 1})])
        assert ": " not in payload and ", " not in payload
        assert "\n" not in payload


class TestSizeHelpers:
    """Budget arithmetic."""

    def test_size_bytes_is_utf8(self):
        assert size_bytes("ç") == 2  # ç is two bytes in UTF-8

    def test_over_budget(self):
        assert over_budget("x" * 11, 10)
        assert not over_budget("x" * 10, 10)
        assert over_budget("x", 0)

    def test_default_budget_is_64k(self):
        assert DEFAULT_INLINE_BUDGET == 65536

    def test_compact_json_has_no_padding(self):
        text = compact_json({"a": [1, 2], "b": "ç"})
        assert text == '{"a":[1,2],"b":"ç"}'


class TestScopeValidation:
    """collect_geojson rejects unknown scopes before touching QGIS."""

    def test_unknown_scope_raises_without_qgis(self):
        with pytest.raises(LayerExportError, match="unknown scope"):
            # Import here so the module's lazy QGIS imports are never hit.
            from core.layer_export import collect_geojson
            collect_geojson(object(), scope="bogus")
