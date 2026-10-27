"""Collect live map context (layers, extent, CRS) from a QGIS canvas.

Kept separate from the pure formatting logic in ``build_context_text`` so
the latter is testable without a QGIS runtime. All QGIS imports are lazy.
"""

from typing import Any, Dict, List, Optional, Tuple

# Do not report feature counts above this (counting huge layers is slow).
_FEATURE_COUNT_LIMIT = 100_000

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
    }
    try:
        count = int(qgis_layer.featureCount())
        if 0 < count <= _FEATURE_COUNT_LIMIT:
            entry["feature_count"] = count
    except (TypeError, ValueError, RuntimeError):
        pass
    return entry


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
        try:
            if layer.type() == QgsMapLayerType.VectorLayer:
                layers.append(_describe_vector_layer(layer))
            elif layer.type() == QgsMapLayerType.RasterLayer:
                layers.append({"name": layer.name(),
                               "geometry_type": "raster",
                               "crs": None, "feature_count": None})
        except Exception:
            continue  # Never let one broken layer kill the prompt.
    extent = _canvas_extent_4326(canvas)
    project_crs = None
    try:
        from qgis.core import QgsProject

        project_crs = QgsProject.instance().crs().authid()
    except Exception:
        pass
    return layers, extent, project_crs
