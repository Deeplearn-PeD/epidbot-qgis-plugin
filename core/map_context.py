"""Map context extraction for EpidBot prompts.

The pure function :func:`build_context_text` turns plain Python data (layer
descriptions, an extent, a CRS) into the ``[QGIS context]`` preamble that is
prepended to chat messages, so it can be unit-tested without QGIS.

:func:`collect_map_context` gathers that data from a live QGIS canvas; it
imports ``qgis`` lazily inside the function body.
"""

from typing import Any, Dict, List, Optional

# Sample values shown per field in the context preamble.
_SAMPLE_VALUES_SHOWN = 5

# Sample-value hints shown for at most this many fields per layer.
_MAX_SAMPLE_FIELDS = 4


def build_context_text(layers: List[Dict[str, Any]],
                       extent: Optional[Dict[str, Any]],
                       project_crs: Optional[str],
                       max_layers: int = 10) -> str:
    """Render the map-context preamble for a chat message.

    :param layers: dicts with keys ``name``, ``geometry_type``, ``crs`` and
        optionally ``feature_count``, ``selected`` (selected feature count),
        ``fields`` (list of ``{name, type}`` dicts), ``fields_more`` (number of
        fields not listed) and ``samples`` (field name -> distinct values).
    :param extent: dict with ``xmin``, ``ymin``, ``xmax``, ``ymax`` and
        ``crs`` (already reprojected to EPSG:4326 by the collector), or None.
    :param project_crs: project CRS authority id, e.g. ``EPSG:4674``.
    :param max_layers: cap on the number of layers listed.
    :returns: multi-line ``[QGIS context] ...`` string, without a trailing
        newline, or an empty string when there is nothing to report.

    Only layers currently visible on the canvas are reported by the collector,
    so no explicit visibility flag is needed here.
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
            selected = layer.get("selected")
            if selected:
                desc += ", {:,} selected".format(selected).replace(",", " ")
            lines.append("  - " + desc)
            detail = _layer_detail_text(layer)
            if detail:
                lines.append(detail)
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


def _layer_detail_text(layer: Dict[str, Any]) -> str:
    """Second (indented) line for a layer: schema and sample values."""
    fields = layer.get("fields") or []
    if not fields:
        return ""
    parts = ["{} ({})".format(f.get("name", "?"), f.get("type", "?"))
             for f in fields]
    more = layer.get("fields_more")
    if more:
        parts.append("... and {} more".format(more))
    text = "    fields: " + ", ".join(parts)
    samples = layer.get("samples") or {}
    sample_bits = []
    for name, values in list(samples.items())[:_MAX_SAMPLE_FIELDS]:
        shown = values[:_SAMPLE_VALUES_SHOWN]
        suffix = ", ..." if len(values) > _SAMPLE_VALUES_SHOWN else ""
        sample_bits.append("{} in {{{}}}".format(
            name, ", ".join(shown) + suffix))
    if sample_bits:
        text += "\n    sample values: " + "; ".join(sample_bits)
    return text

