"""Tests for :mod:`core.geo_extract` (geodata source extraction)."""

import json

from core.geo_extract import extract_geo_sources, _looks_like_geojson

FEATURE_COLLECTION = {
    "type": "FeatureCollection",
    "name": "dengue_cases",
    "features": [
        {"type": "Feature", "geometry": {"type": "Point",
                                         "coordinates": [-43.2, -22.9]},
         "properties": {"municipio": "Rio de Janeiro"}},
    ],
}
TAGGED_BLOCK = "```geojson\n{}\n```".format(json.dumps(FEATURE_COLLECTION))
UNTAGGED_BLOCK = "```\n{}\n```".format(json.dumps(FEATURE_COLLECTION))
JSON_BLOCK = "```json\n{}\n```".format(json.dumps(FEATURE_COLLECTION))
JSON_NON_GEO_BLOCK = "```json\n{\"foo\": [1, 2, 3]}\n```"


class TestExtractGeoSources:
    """GeoJSON fences and geodata URLs in answers."""

    def test_no_sources_in_plain_text(self):
        assert extract_geo_sources("Just an answer with no data.") == []

    def test_tagged_geojson_block_is_found(self):
        sources = extract_geo_sources(TAGGED_BLOCK)
        assert len(sources) == 1
        assert sources[0]["kind"] == "geojson"
        assert sources[0]["label"] == "dengue_cases"
        assert '"FeatureCollection"' in sources[0]["content"]

    def test_json_block_only_counts_when_geojson(self):
        assert len(extract_geo_sources(JSON_BLOCK)) == 1
        assert extract_geo_sources(JSON_NON_GEO_BLOCK) == []

    def test_untagged_geojson_is_ignored(self):
        # Sniffing arbitrary code fences would be too risky.
        assert extract_geo_sources(UNTAGGED_BLOCK) == []

    def test_geojson_urls_are_found(self):
        text = ("Download the data from https://example.com/cases.geojson "
                "or https://example.com/sinan.zip.")
        sources = extract_geo_sources(text)
        kinds = [s["kind"] for s in sources]
        assert kinds == ["url", "url"]
        assert sources[0]["label"] == "cases.geojson"
        assert sources[1]["format"] == "vector"

    def test_csv_url_is_marked_as_table(self):
        text = "Data: https://example.com/dengue.csv?q=1"
        sources = extract_geo_sources(text)
        assert len(sources) == 1
        assert sources[0]["format"] == "table"
        assert sources[0]["url"] == "https://example.com/dengue.csv?q=1"

    def test_plain_urls_are_ignored(self):
        text = "See https://example.com/about for details."
        assert extract_geo_sources(text) == []

    def test_duplicates_are_removed(self):
        text = TAGGED_BLOCK + "\n" + TAGGED_BLOCK
        assert len(extract_geo_sources(text)) == 1

    def test_empty_and_none_inputs(self):
        assert extract_geo_sources("") == []
        assert extract_geo_sources(None) == []


class TestLooksLikeGeojson:
    """Fence-content sniffing (only used for untagged ``json`` blocks)."""

    def test_feature_collection(self):
        assert _looks_like_geojson(json.dumps(FEATURE_COLLECTION))

    def test_bare_geometry(self):
        assert _looks_like_geojson('{"type": "Point", "coordinates": [1, 2]}')

    def test_plain_json_is_rejected(self):
        assert not _looks_like_geojson('{"hello": "world"}')

    def test_broken_json_is_rejected(self):
        assert not _looks_like_geojson("{not json")
