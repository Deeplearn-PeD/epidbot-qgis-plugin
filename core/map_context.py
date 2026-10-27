"""Map context extraction for EpidBot prompts.

The pure function :func:`build_context_text` turns plain Python data (layer
descriptions, an extent, a CRS) into the ``[QGIS context]`` preamble that is
prepended to chat messages, so it can be unit-tested without QGIS.

:func:`collect_map_context` gathers that data from a live QGIS canvas; it
imports ``qgis`` lazily inside the function body.
"""

from typing import Any, Dict, List, Optional


def build_context_text(layers: List[Dict[str, Any]],
                       extent: Optional[Dict[str, Any]],
                       project_crs: Optional[str],
                       max_layers: int = 10) -> str:
    """Render the map-context preamble for a chat message.

    :param layers: dicts with keys ``name``, ``geometry_type``, ``crs`` and
        optionally ``feature_count``.
    :param extent: dict with ``xmin``, ``ymin``, ``xmax``, ``ymax`` and
        ``crs`` (already reprojected to EPSG:4326 by the collector), or None.
    :param project_crs: project CRS authority id, e.g. ``EPSG:4674``.
    :param max_layers: cap on the number of layers listed.
    :returns: multi-line ``[QGIS context] ...`` string, without a trailing
        newline, or an empty string when there is nothing to report.
    """
    sections: List[str] = []
    if layers:
        shown = layers[:max_layers]
        lines = []
        for layer in shown:
            desc = '"{}" ({}, {})'.format(
                layer.get("name", "?"),
                layer.get("geometry_type") or "unknown geometry",
                layer.get("crs") or "unknown CRS")
            count = layer.get("feature_count")
            if count is not None:
                desc += ", {:,} features".format(count).replace(",", " ")
            lines.append("  - " + desc)
        if len(layers) > max_layers:
            lines.append("  - ... and {} more layers".format(
                len(layers) - max_layers))
        sections.append("Active layers:\n" + "\n".join(lines))
    if extent is not None:
        sections.append(
            "Map extent: {xmin:.4f},{ymin:.4f} to {xmax:.4f},{ymax:.4f} "
            "({crs})".format(**extent))
    if project_crs:
        sections.append("Project CRS: {}".format(project_crs))
    if not sections:
        return ""
    return "[QGIS context] " + "\n".join(sections)
