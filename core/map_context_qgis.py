"""Collect live map context (layers, extent, CRS) from a QGIS canvas.

Kept separate from the pure formatting logic in ``build_context_text`` so
the latter is testable without a QGIS runtime. All QGIS imports are lazy.
"""

from contextlib import suppress
from typing import Any, Dict, List, Optional, Tuple

# Do not report feature counts above this (counting huge layers is slow).
_FEATURE_COUNT_LIMIT = 100_000

# Number of features scanned to collect sample values for the context block.
_FIELD_SAMPLE_SCAN_LIMIT = 300

# Distinct values kept per field; fields exceeding this are treated as
# high-cardinality (e.g. ids) and dropped from the sample list.
_FIELD_SAMPLE_CAP = 8

# Maximum number of fields named in the context preamble.
_MAX_FIELDS_LISTED = 15

# Do not reproject/report extents larger than the whole world in degrees.
_VALID_EXTENT_MAX_DEGREES = 361.0


def _geometry_type_name(qgis_layer) -> str:
    """Human-readable geometry type for a QgsVectorLayer."""
    from qgis.core import Qgis

    names = {
        Qgis.GeometryType.Point: "Point",
        Qgis.GeometryType.Line: "LineString",
        Qgis.GeometryType.Polygon: "Polygon",
        Qgis.GeometryType.Null: "No geometry",
        Qgis.GeometryType.Unknown: "Unknown geometry",
    }
    return names.get(qgis_layer.geometryType(), "Unknown geometry")


def _describe_vector_layer(qgis_layer) -> Dict[str, Any]:
    """Summarize one QgsVectorLayer for the context preamble."""
    entry: Dict[str, Any] = {
        "name": qgis_layer.name(),
        "geometry_type": _geometry_type_name(qgis_layer),
        "crs": qgis_layer.crs().authid() if qgis_layer.crs().isValid() else None,
        "feature_count": None,
        "fields": [],
        "samples": {},
        "selected": None,
    }
    with suppress(TypeError, ValueError, RuntimeError):
        count = int(qgis_layer.featureCount())
        if 0 < count <= _FEATURE_COUNT_LIMIT:
            entry["feature_count"] = count
    with suppress(AttributeError, TypeError, ValueError, RuntimeError):
        entry["selected"] = int(qgis_layer.selectedFeatureCount())
    # Never let schema introspection kill the prompt.
    with suppress(Exception):
        fields = list(qgis_layer.fields())
        entry["fields"] = [{"name": f.name(), "type": f.typeName()}
                           for f in fields[:_MAX_FIELDS_LISTED]]
        if len(fields) > _MAX_FIELDS_LISTED:
            entry["fields_more"] = len(fields) - _MAX_FIELDS_LISTED
        if entry["feature_count"]:
            entry["samples"] = _sample_values(qgis_layer, fields)
    return entry


def _sample_values(qgis_layer, fields) -> Dict[str, List[str]]:
    """Collect a few distinct values per field to hint at its content.

    Fields whose distinct count exceeds :data:`_FIELD_SAMPLE_CAP` within the
    scanned features (ids, names, ...) are omitted: only plausible categorical
    fields are reported.
    """
    scan = _FIELD_SAMPLE_SCAN_LIMIT
    distinct: Dict[str, List[str]] = {
        f.name(): [] for f in fields[:_MAX_FIELDS_LISTED]}
    with suppress(Exception):
        for feature in qgis_layer.getFeatures():
            if scan <= 0:
                break
            scan -= 1
            attrs = feature.attributes()
            for idx, field in enumerate(fields[:_MAX_FIELDS_LISTED]):
                values = distinct.get(field.name())
                if values is None:  # already marked high-cardinality
                    continue
                raw = attrs[idx] if idx < len(attrs) else None
                if raw is None:
                    continue
                text = str(raw).strip()
                if not text or text in values:
                    continue
                values.append(text)
                if len(values) > _FIELD_SAMPLE_CAP:
                    distinct[field.name()] = None  # high-cardinality: drop
    return {name: values for name, values in distinct.items() if values}


def _canvas_extent_4326(canvas) -> Optional[Dict[str, Any]]:
    """Canvas extent reprojected to EPSG:4326 (or None when invalid)."""
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                           QgsProject)

    extent = canvas.extent()
    if extent.isEmpty() or not extent.isFinite():
        return None
    source_crs = canvas.mapSettings().destinationCrs()
    target_crs = QgsCoordinateReferenceSystem("EPSG:4326")
    if source_crs.authid() != "EPSG:4326":
        transform = QgsCoordinateTransform(source_crs, target_crs,
                                           QgsProject.instance())
        try:
            extent = transform.transformBoundingBox(extent)
        except Exception:
            return None
    # Sanity check: a geographic extent bigger than the planet means the
    # reprojection blew up (e.g. polar projections) - skip it.
    if (extent.width() > _VALID_EXTENT_MAX_DEGREES
            or extent.height() > _VALID_EXTENT_MAX_DEGREES):
        return None
    return {
        "xmin": extent.xMinimum(), "ymin": extent.yMinimum(),
        "xmax": extent.xMaximum(), "ymax": extent.yMaximum(),
        "crs": "EPSG:4326",
    }


def collect_map_context(iface, max_layers: int = 10,
                        ) -> Tuple[List[Dict[str, Any]],
                                   Optional[Dict[str, Any]], Optional[str]]:
    """Gather (layers, extent_4326, project_crs) from the QGIS interface.

    Vector layers are described in detail; raster layers are listed
    generically. Results feed :func:`map_context.build_context_text`.

    :param iface: the ``QgisInterface`` object handed to the plugin.
    """
    from qgis.core import QgsMapLayerType

    canvas = iface.mapCanvas()
    layers: List[Dict[str, Any]] = []
    for layer in canvas.layers() or []:
        # Never let one broken layer kill the prompt.
        with suppress(Exception):
            if layer.type() == QgsMapLayerType.VectorLayer:
                layers.append(_describe_vector_layer(layer))
            elif layer.type() == QgsMapLayerType.RasterLayer:
                layers.append({"name": layer.name(),
                               "geometry_type": "raster",
                               "crs": None, "feature_count": None})
    extent = _canvas_extent_4326(canvas)
    project_crs = None
    with suppress(Exception):
        from qgis.core import QgsProject

        project_crs = QgsProject.instance().crs().authid()
    return layers, extent, project_crs


# ---------------------------------------------------------------------------
# Selection summaries (used by the "ask about selection" spatial prompt)
# ---------------------------------------------------------------------------

# Render at most this many selected features in detail.
_SELECTION_FEATURE_LIMIT = 20

# Attribute values shown per feature.
_SELECTION_FIELD_LIMIT = 8

# Guard against absurd attribute strings.
_SELECTION_VALUE_MAX_CHARS = 60


def collect_selection_summary(iface, max_features: int = _SELECTION_FEATURE_LIMIT,
                              ) -> Optional[str]:
    """Build a compact text summary of the features selected on the canvas.

    :returns: a multi-line string (without trailing newline) or ``None`` when
        nothing is selected.
    """
    canvas = iface.mapCanvas()
    lines: List[str] = []
    try:
        canvas_layers = canvas.layers() or []
    except Exception:
        canvas_layers = []
    for layer in canvas_layers:
        try:
            selected = int(layer.selectedFeatureCount())
        except (AttributeError, TypeError, ValueError, RuntimeError):
            continue
        if selected <= 0:
            continue
        lines.append('- "{}": {:,} selected'.format(
            layer.name(), selected).replace(",", " "))
        detail = _selected_features_detail(layer, selected, max_features)
        if detail:
            lines.append(detail)
    if not lines:
        return None
    return "\n".join(lines)


def _selected_features_detail(layer, selected: int,
                              max_features: int) -> Optional[str]:
    """Render up to ``max_features`` selected features as key=value lines."""
    try:
        fields = list(layer.fields())
        feature_ids = list(layer.selectedFeatureIds())[:max_features]
        if not feature_ids:
            return None
        iterator = layer.getFeatures(feature_ids)
        rows = []
        for feature in iterator:
            attrs = feature.attributes()
            pairs = []
            for idx, field in enumerate(fields[:_SELECTION_FIELD_LIMIT]):
                raw = attrs[idx] if idx < len(attrs) else None
                if raw is None:
                    continue
                text = str(raw).strip()
                if len(text) > _SELECTION_VALUE_MAX_CHARS:
                    text = text[:_SELECTION_VALUE_MAX_CHARS] + "..."
                if text:
                    pairs.append("{}={}".format(field.name(), text))
            if pairs:
                rows.append("  - " + ", ".join(pairs))
        hidden = selected - len(rows)
        if hidden > 0:
            rows.append("  - ... and {:,} more".format(hidden).replace(
                ",", " "))
        return "\n".join(rows) if rows else None
    except Exception:
        return None  # Selection detail is best-effort.

