"""Load EpidBot-provided geodata into the QGIS map canvas.

Handles the sources produced by :mod:`core.geo_extract`:

- inline GeoJSON text (written to a temp file and opened through OGR);
- URLs to GeoJSON/JSON/GPKG/ZIP (downloaded, then opened through OGR);
- URLs to CSV (downloaded, then opened through the delimited-text provider,
  promoted to a spatial layer when latitude/longitude columns are detected).

QGIS imports are lazy so the pure helpers stay importable in tests.
"""

import csv
import os
import tempfile
import urllib.parse
from contextlib import suppress
from typing import Any, Dict, List, Optional, Tuple

import requests

_LAYERS_DIR = os.path.join(tempfile.gettempdir(), "epidbot-qgis", "layers")

_DOWNLOAD_TIMEOUT = 60  # seconds
_MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024

# Column names accepted as coordinates in CSV sources (lowercase).
_LAT_NAMES = {"lat", "latitude", "y", "nu_latitude", "lat_y"}
_LON_NAMES = {"lon", "long", "longitude", "x", "nu_longitude", "lon_x"}

# Fields preferred for a categorical default style.
_STYLE_PRIORITY = ["municipio", "municipality", "co_municip", "co_uf", "uf",
                   "estado", "state", "regiao", "region", "co_distrito",
                   "nu_ano", "ano", "year"]

# Categorized style: do not try to build more categories than this.
_STYLE_MAX_CATEGORIES = 25


class GeoLoadError(Exception):
    """Raised when a geodata source cannot be turned into a map layer."""


def layers_dir() -> str:
    """Local cache directory for downloaded geodata."""
    os.makedirs(_LAYERS_DIR, exist_ok=True)
    return _LAYERS_DIR


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def sniff_csv_coordinate_columns(path: str) -> Tuple[Optional[str], Optional[str]]:
    """Find latitude/longitude columns in a CSV file's header + first rows.

    :returns: ``(lat_column, lon_column)`` or ``(None, None)``.
    """
    try:
        with open(path, "r", newline="", encoding="utf-8-sig", errors="replace") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                return None, None
            columns = {name.strip(): None for name in reader.fieldnames}
            lat = _match_column(columns, _LAT_NAMES)
            lon = _match_column(columns, _LON_NAMES)
            if lat is None or lon is None:
                return None, None
            # Confirm the columns actually hold numbers in the first rows.
            checked = 0
            for row in reader:
                try:
                    float(str(row.get(lat, "")).replace(",", "."))
                    float(str(row.get(lon, "")).replace(",", "."))
                except (TypeError, ValueError):
                    return None, None
                checked += 1
                if checked >= 5:
                    break
            return lat, lon
    except OSError:
        return None, None


def _match_column(columns: Dict[str, None], candidates: set) -> Optional[str]:
    """First header in ``columns`` matching the candidate names (lowercased)."""
    for name in columns:
        if name.strip().lower() in candidates:
            return name.strip()
    return None


def _unique_layer_name(base: str) -> str:
    """Avoid clashing layer names in the project (QGIS dedups silently but
    users see duplicates); keep names short and readable instead."""
    return base[:80] if base else "EpidBot layer"


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _download(url: str) -> str:
    """Download ``url`` into the layers cache; return the local path."""
    os.makedirs(layers_dir(), exist_ok=True)
    filename = os.path.basename(urllib.parse.urlsplit(url).path) or "data"
    target = os.path.join(layers_dir(), filename)
    try:
        with requests.get(url, stream=True, timeout=_DOWNLOAD_TIMEOUT) as resp:
            resp.raise_for_status()
            size = 0
            with open(target, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    size += len(chunk)
                    if size > _MAX_DOWNLOAD_BYTES:
                        raise GeoLoadError("download larger than 64 MB")
                    fh.write(chunk)
    except requests.RequestException as exc:
        raise GeoLoadError("could not download {}: {}".format(url, exc))
    return target


# ---------------------------------------------------------------------------
# QGIS layer creation
# ---------------------------------------------------------------------------

def add_source_to_canvas(iface, source: Dict[str, Any]) -> str:
    """Turn a geodata source into a layer and add it to the project.

    :param iface: ``QgisInterface`` (canvas refresh + message bar).
    :param source: dict from :func:`core.geo_extract.extract_geo_sources`.
    :returns: the layer name added.
    :raises GeoLoadError: when the source cannot be loaded.
    """
    from qgis.core import QgsProject, QgsVectorLayer

    kind = source.get("kind")
    label = source.get("label") or "EpidBot layer"

    if kind == "geojson":
        path = _write_temp_geojson(source.get("content") or "")
        layer = QgsVectorLayer(path, _unique_layer_name(label), "ogr")
    elif kind == "url":
        path = _download(source["url"])
        if source.get("format") == "table":
            layer = _csv_layer(path, label)
        else:
            layer = QgsVectorLayer(path, _unique_layer_name(label), "ogr")
    else:
        raise GeoLoadError("unknown geodata source kind: {!r}".format(kind))

    if not layer.isValid():
        raise GeoLoadError(
            "QGIS could not read the data in '{}'".format(label))
    apply_default_style(layer)
    QgsProject.instance().addMapLayer(layer)
    with suppress(Exception):
        iface.mapCanvas().refresh()
    return layer.name()


def _write_temp_geojson(content: str) -> str:
    """Persist inline GeoJSON to the cache and return the path."""
    os.makedirs(layers_dir(), exist_ok=True)
    # A stable digest-based name avoids unbounded file growth.
    # (Not a security hash: usedforsecurity=False documents that for scanners.)
    import hashlib

    digest = hashlib.sha1(content.encode("utf-8"),
                          usedforsecurity=False).hexdigest()[:12]
    path = os.path.join(layers_dir(), "epidbot-{}.geojson".format(digest))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _csv_layer(path: str, label: str) -> Any:
    """Build a delimited-text layer; spatial when coordinate columns exist."""
    from qgis.core import QgsVectorLayer

    uri = "file:///{}".format(urllib.parse.quote(path.replace(os.sep, "/")))
    lat, lon = sniff_csv_coordinate_columns(path)
    if lat and lon:
        uri += ("?type=csv&delimiter=%2C&xField={}&yField={}"
                "&crs=EPSG:4326&detectTypes=yes").format(
            urllib.parse.quote(lon), urllib.parse.quote(lat))
    else:
        uri += "?type=csv&delimiter=%2C&detectTypes=yes"
    name = _unique_layer_name(label)
    layer = QgsVectorLayer(uri, name, "delimitedtext")
    return layer


# ---------------------------------------------------------------------------
# Default styling
# ---------------------------------------------------------------------------

def apply_default_style(layer) -> None:
    """Give point/line/polygon layers a light categorical style when a
    well-known column (municipality, state, year, ...) is present."""
    # Styling is cosmetic; never block the load.
    with suppress(Exception):
        from qgis.core import (QgsCategorizedSymbolRenderer,
                               QgsMapLayerType, QgsRandomColorRamp,
                               QgsRendererCategory, QgsSymbol)

        if layer.type() != QgsMapLayerType.VectorLayer:
            return
        from qgis.core import Qgis

        if layer.geometryType() in (Qgis.GeometryType.Null,
                                    Qgis.GeometryType.Unknown):
            return  # geometryless table or unknown: no style to apply
        target = None
        for field in layer.fields():
            if field.name().lower() in _STYLE_PRIORITY:
                target = field.name()
                break
        if target is None:
            return
        values: List[Any] = []
        seen = set()
        for feature in layer.getFeatures():
            value = feature[target]
            key = str(value)
            if key not in seen:
                seen.add(key)
                values.append(value)
            if len(values) > _STYLE_MAX_CATEGORIES:
                return  # too varied for a readable categorical style
        if not values:
            return
        ramp = QgsRandomColorRamp()
        ramp.setTotalColorCount(len(values))
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        categories = []
        for index, value in enumerate(values):
            symbol = symbol.clone()
            symbol.setColor(ramp.color(index / max(len(values) - 1, 1)))
            categories.append(QgsRendererCategory(value, symbol, str(value)))
        layer.setRenderer(QgsCategorizedSymbolRenderer(target, categories))
