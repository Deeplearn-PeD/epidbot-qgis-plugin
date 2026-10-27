"""Extract loadable geodata references from EpidBot answers.

Two source kinds are recognized (the documented plugin/server convention):

- fenced code blocks tagged ``geojson`` (a ``json`` fence also counts when its
  content unambiguously parses as GeoJSON), and
- plain URLs pointing at ``.geojson``, ``.json``, ``.csv``, ``.gpkg`` or
  ``.zip`` resources.

Pure Python, no Qt, so it is unit-testable.
"""

import json
import os
import re
import urllib.parse
from typing import Any, Dict, List

_FENCED_RE = re.compile(r"```([A-Za-z0-9_+-]*)[ \t]*\n(.*?)(?:```|\Z)",
                        re.DOTALL)

# Candidate URLs; punctuation that likely ends a sentence is trimmed later.
_RAW_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

_GEOJSON_TYPES = {"featurecollection", "feature", "point", "multipoint",
                  "linestring", "multilinestring", "polygon", "multipolygon",
                  "geometrycollection"}

_URL_KINDS = {"geojson": "vector", "json": "vector", "gpkg": "vector",
              "zip": "vector", "csv": "table"}

# Characters that may trail a URL when it is embedded in prose.
_URL_TRAILING_PUNCT = ".,;:!?)'\""


def _looks_like_geojson(content: str) -> bool:
    """True when ``content`` parses as a JSON object with a GeoJSON type."""
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    type_name = str(data.get("type", "")).lower()
    return type_name in _GEOJSON_TYPES


def _geojson_label(data: Dict[str, Any], index: int) -> str:
    """Best-effort human label for a GeoJSON block."""
    name = data.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "GeoJSON {}".format(index)


def extract_geo_sources(text: str) -> List[Dict[str, Any]]:
    """List geodata sources advertised in a message.

    :param text: raw markdown answer.
    :returns: list of source dicts. Each has ``kind`` (``geojson`` or
        ``url``), a ``label`` and either ``content`` (GeoJSON text) or
        ``url`` plus ``format`` (``vector``/``table``).
    """
    sources: List[Dict[str, Any]] = []
    seen: set = set()

    for index, match in enumerate(_FENCED_RE.finditer(text or ""), start=1):
        lang = (match.group(1) or "").strip().lower()
        content = match.group(2).strip()
        if not content:
            continue
        if lang == "geojson":
            pass  # tagged: trust the author
        elif lang == "json":
            if not _looks_like_geojson(content):
                continue
        else:
            continue  # untagged fences are never sniffed
        key = ("geojson", content[:256])
        if key in seen:
            continue
        seen.add(key)
        try:
            data = json.loads(content)
            label = (_geojson_label(data, index)
                     if isinstance(data, dict) else "GeoJSON {}".format(index))
        except ValueError:
            label = "GeoJSON {}".format(index)
        sources.append({"kind": "geojson", "content": content,
                        "label": label})

    for match in _RAW_URL_RE.finditer(text or ""):
        url = match.group(0).rstrip(_URL_TRAILING_PUNCT)
        ext = os.path.splitext(urllib.parse.urlsplit(url).path)[1]
        ext = ext.lower().lstrip(".")
        if ext not in _URL_KINDS:
            continue
        key = ("url", url)
        if key in seen:
            continue
        seen.add(key)
        name = os.path.basename(urllib.parse.urlsplit(url).path)
        sources.append({"kind": "url", "url": url, "format": _URL_KINDS[ext],
                        "label": name or url})

    return sources
