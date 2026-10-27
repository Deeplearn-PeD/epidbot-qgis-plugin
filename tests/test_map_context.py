"""Tests for :mod:`core.map_context` (pure prompt-context builder)."""

from core.map_context import build_context_text

LAYER = {"name": "dengue_cases", "geometry_type": "Point",
         "crs": "EPSG:4326", "feature_count": 1234}
EXTENT = {"xmin": -43.2, "ymin": -23.0, "xmax": -42.8, "ymax": -22.7,
          "crs": "EPSG:4326"}


class TestBuildContextText:
    """The [QGIS context] preamble injected into prompts."""

    def test_empty_inputs_give_empty_string(self):
        assert build_context_text([], None, None) == ""

    def test_layers_are_listed(self):
        text = build_context_text([LAYER], None, None)
        assert text.startswith("[QGIS context]")
        assert '"dengue_cases" (Point, EPSG:4326)' in text
        assert "1 234 features" in text

    def test_extent_is_included(self):
        text = build_context_text([], EXTENT, None)
        assert "Map extent: -43.2000,-23.0000 to -42.8000,-22.7000" in text
        assert "(EPSG:4326)" in text

    def test_project_crs_is_included(self):
        text = build_context_text([], None, "EPSG:4674")
        assert "Project CRS: EPSG:4674" in text

    def test_layer_cap(self):
        layers = [dict(LAYER, name="l{}".format(i)) for i in range(15)]
        text = build_context_text(layers, None, None, max_layers=10)
        assert "and 5 more layers" in text

    def test_missing_feature_count_is_omitted(self):
        layer = dict(LAYER)
        layer["feature_count"] = None
        text = build_context_text([layer], None, None)
        assert "features" not in text
