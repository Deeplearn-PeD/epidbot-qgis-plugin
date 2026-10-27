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

    def test_selected_count_is_reported(self):
        layer = dict(LAYER, selected=17)
        text = build_context_text([layer], None, None)
        assert "17 selected" in text

    def test_zero_selection_is_omitted(self):
        layer = dict(LAYER, selected=0)
        text = build_context_text([layer], None, None)
        assert "selected" not in text

    def test_fields_and_samples_are_listed(self):
        layer = dict(LAYER, fields=[
            {"name": "municipio", "type": "string"},
            {"name": "casos", "type": "integer"},
        ], samples={"municipio": ["Rio de Janeiro", "Niterói"]})
        text = build_context_text([layer], None, None)
        assert "fields: municipio (string), casos (integer)" in text
        assert "sample values: municipio in {Rio de Janeiro, Niterói}" in text

    def test_fields_more_is_listed(self):
        layer = dict(LAYER, fields=[{"name": "id", "type": "integer"}],
                     fields_more=7)
        text = build_context_text([layer], None, None)
        assert "... and 7 more" in text

    def test_long_sample_lists_are_truncated(self):
        layer = dict(LAYER, fields=[{"name": "uf", "type": "string"}],
                     samples={"uf": ["RJ", "SP", "MG", "ES", "BA", "PR"]})
        text = build_context_text([layer], None, None)
        assert "RJ, SP, MG, ES, BA, ..." in text
        assert "PR" not in text


class TestLayerDetailText:
    """Edge cases of the per-layer detail line."""

    def test_no_fields_gives_no_detail_line(self):
        from core.map_context import _layer_detail_text
        assert _layer_detail_text(LAYER) == ""

    def test_at_most_four_sample_fields(self):
        from core.map_context import _layer_detail_text
        samples = {"f{}".format(i): ["x"] for i in range(6)}
        layer = dict(LAYER, fields=[
            {"name": "f{}".format(i), "type": "string"} for i in range(6)],
            samples=samples)
        text = _layer_detail_text(layer)
        assert text.count(" in {") == 4

