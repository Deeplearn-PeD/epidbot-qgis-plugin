"""Export QGIS layers as compact GeoJSON attachments for EpidBot prompts.

Scopes (see :data:`SCOPE_CHOICES`):

- ``selection`` — only the currently selected features;
- ``extent`` — only features intersecting the (EPSG:4326) canvas extent;
- ``all`` — every feature of the layer;
- ``identifiers`` — fid + join-key fields only, no geometry (cheap payload
  that lets EpidBot return attribute edits keyed to real features).

QGIS imports are lazy; the pure helpers (join-key detection, compact
serialization, identifier payloads) are unit-testable without a runtime.
"""

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCOPE_SELECTION = "selection"
SCOPE_EXTENT = "extent"
SCOPE_ALL = "all"
SCOPE_IDENTIFIERS = "identifiers"

SCOPE_CHOICES = (SCOPE_SELECTION, SCOPE_EXTENT, SCOPE_ALL, SCOPE_IDENTIFIERS)

SCOPE_LABELS = {
    SCOPE_SELECTION: "selected features",
    SCOPE_EXTENT: "features in extent",
    SCOPE_ALL: "all features",
    SCOPE_IDENTIFIERS: "identifiers",
}

# Default inline attachment budget (bytes).
DEFAULT_INLINE_BUDGET = 64 * 1024

# Never attempt to serialize more features than this inline; the interactive
# reduction flow takes over instead of freezing the UI.
_HARD_FEATURE_LIMIT = 50_000

# Coordinate precision for exported geometries (6 dp is about 11 cm).
_GEOMETRY_PRECISION = 6

# Candidate join-key field names for attribute write-back, in priority order
# (matched case-insensitively against layer fields).
_JOIN_KEY_CANDIDATES = [
    "cd_mun", "cd_munic", "cd_municip", "co_municip", "codmun",
    "municipality_code", "codarea", "co_uf", "cd_uf", "uf", "estado",
    "state", "region", "regiao", "codigo", "code", "gid", "id",
]


class LayerExportError(Exception):
    """Raised when a layer cannot be exported within the given constraints."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def compact_json(obj: Any) -> str:
    """Serialize with minimal separators (smaller attachments)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def pick_join_key(field_names: Iterable[str]) -> Optional[str]:
    """Best semantic join-key field for attribute write-back, or None.

    Matching is case-insensitive against :data:`_JOIN_KEY_CANDIDATES`
    (priority order); the original field-name spelling is returned.
    """
    lowered = {str(name).lower(): str(name) for name in field_names}
    for candidate in _JOIN_KEY_CANDIDATES:
        if candidate in lowered:
            return lowered[candidate]
    return None


def build_identifiers_geojson(rows: Iterable[Tuple[Any, Dict[str, Any]]]) -> str:
    """Build a geometry-less FeatureCollection of identifiers.

    :param rows: iterable of ``(fid, properties_dict)`` pairs. The fid is
        stored as the GeoJSON feature ``id`` so EpidBot can echo edits back
        keyed by ``fid``.
    """
    features = []
    for fid, properties in rows:
        features.append({"type": "Feature", "id": fid,
                         "properties": dict(properties)})
    collection = {"type": "FeatureCollection", "features": features}
    return compact_json(collection)


def size_bytes(text: str) -> int:
    """UTF-8 byte length of a serialized payload."""
    return len(text.encode("utf-8"))


def over_budget(text: str, budget_bytes: int) -> bool:
    """True when ``text`` exceeds ``budget_bytes``."""
    return size_bytes(text) > max(int(budget_bytes), 0)


# ---------------------------------------------------------------------------
# QGIS-dependent export
# ---------------------------------------------------------------------------

def collect_geojson(qgis_layer, scope: str = SCOPE_ALL,
                    extent: Optional[Dict[str, float]] = None,
                    budget_bytes: int = DEFAULT_INLINE_BUDGET,
                    precision: int = _GEOMETRY_PRECISION,
                    ) -> Dict[str, Any]:
    """Serialize a layer (or a subset of it) as compact GeoJSON.

    :param qgis_layer: a ``QgsVectorLayer``.
    :param scope: one of :data:`SCOPE_CHOICES`.
    :param extent: canvas extent dict (EPSG:4326) for ``SCOPE_EXTENT``.
    :param budget_bytes: advisory inline budget; the result reports
        ``over_budget`` so the caller can offer reductions.
    :param precision: coordinate decimal places (reduction option).
    :returns: dict with ``geojson`` (str), ``feature_count``, ``size_bytes``,
        ``over_budget``, ``label`` and ``scope``.
    :raises LayerExportError: on unsupported scope, missing selection/extent,
        or when the feature count exceeds :data:`_HARD_FEATURE_LIMIT`.
    """
    if scope not in SCOPE_CHOICES:
        raise LayerExportError("unknown scope: {!r}".format(scope))

    if scope == SCOPE_IDENTIFIERS:
        return _collect_identifiers(qgis_layer, budget_bytes)

    features = _gather_features(qgis_layer, scope, extent)
    count = len(features)
    if count > _HARD_FEATURE_LIMIT:
        raise LayerExportError(
            "{:,} features exceed the inline limit of {:,}".format(
                count, _HARD_FEATURE_LIMIT).replace(",", " "))

    geojson_str = _export_features_json(qgis_layer, features, precision)
    result = {
        "geojson": geojson_str,
        "feature_count": count,
        "size_bytes": size_bytes(geojson_str),
        "scope": scope,
        "label": "{} ({})".format(qgis_layer.name(),
                                  SCOPE_LABELS.get(scope, scope)),
    }
    result["over_budget"] = result["size_bytes"] > budget_bytes
    return result


def _gather_features(qgis_layer, scope: str,
                     extent: Optional[Dict[str, float]]) -> List[Any]:
    """Materialize the feature list for a scope (QGIS objects)."""
    from qgis.core import QgsFeatureRequest

    if scope == SCOPE_SELECTION:
        ids = list(qgis_layer.selectedFeatureIds())
        if not ids:
            raise LayerExportError(
                "No features selected on layer '{}'".format(
                    qgis_layer.name()))
        return list(qgis_layer.getFeatures(ids))
    if scope == SCOPE_EXTENT:
        if not extent:
            raise LayerExportError("extent is required for this scope")
        request = QgsFeatureRequest()
        request.setFilterRect(_extent_in_layer_crs(qgis_layer, extent))
        return list(qgis_layer.getFeatures(request))
    return list(qgis_layer.getFeatures())


def _extent_in_layer_crs(qgis_layer, extent: Dict[str, float]):
    """Reproject an EPSG:4326 extent dict into the layer's CRS."""
    from qgis.core import (QgsCoordinateReferenceSystem,
                           QgsCoordinateTransform, QgsProject, QgsRectangle)

    rect = QgsRectangle(extent["xmin"], extent["ymin"],
                        extent["xmax"], extent["ymax"])
    layer_crs = qgis_layer.crs()
    if layer_crs.authid() == "EPSG:4326":
        return rect
    transform = QgsCoordinateTransform(
        QgsCoordinateReferenceSystem("EPSG:4326"), layer_crs,
        QgsProject.instance())
    try:
        return transform.transformBoundingBox(rect)
    except Exception as exc:  # noqa: BLE001 - report as export failure
        raise LayerExportError(
            "could not reproject the extent to the layer CRS: {}".format(exc)
        ) from exc


def _export_features_json(qgis_layer, features: List[Any],
                          precision: int = _GEOMETRY_PRECISION) -> str:
    """Serialize features via QgsJsonExporter (reprojection + precision)."""
    from qgis.core import QgsJsonExporter

    exporter = QgsJsonExporter(qgis_layer, precision)
    exporter.setIncludeAttributes(True)
    exporter.setIncludeGeometry(True)
    exporter.setTransformGeometries(True)
    exporter.setIncludeFeatureIds(True)
    return exporter.exportFeatures(features)


def _collect_identifiers(qgis_layer, budget_bytes: int) -> Dict[str, Any]:
    """fid + join-key fields only (no geometry) for attribute write-back."""
    from qgis.core import QgsFeatureRequest

    field_names = [field.name() for field in qgis_layer.fields()]
    join_key = pick_join_key(field_names)
    if join_key:
        subset = [join_key]
    else:
        subset = field_names[:3]  # nothing semantic: take a few fields
    request = QgsFeatureRequest()
    request.setFlags(QgsFeatureRequest.NoGeometry)
    request.setSubsetOfAttributes(subset, qgis_layer.fields())

    count = 0

    def rows():
        nonlocal count
        for feature in qgis_layer.getFeatures(request):
            count += 1
            yield feature.id(), {
                name: feature[name] for name in subset}

    geojson_str = build_identifiers_geojson(rows())
    result = {
        "geojson": geojson_str,
        "feature_count": count,
        "size_bytes": size_bytes(geojson_str),
        "scope": SCOPE_IDENTIFIERS,
        "join_key": join_key,
        "label": "{} ({})".format(qgis_layer.name(),
                                  SCOPE_LABELS[SCOPE_IDENTIFIERS]),
    }
    result["over_budget"] = result["size_bytes"] > budget_bytes
    return result
