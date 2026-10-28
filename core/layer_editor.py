"""Apply EpidBot attribute edit operations to QGIS vector layers.

An edit operation (parsed by :func:`core.geo_extract.extract_edit_ops`)
looks like::

    {"op": "add_field",
     "layer": "dengue_cases",
     "field": {"name": "pop_2024", "type": "integer"},
     "key": "CD_MUN",
     "confidence": "fetched",
     "values": {"1100205": 12345, "1200301": 678}}

Safety model (see the 0.3.0 plan): validation and join planning here are
pure; the actual write happens in the UI layer after explicit user
confirmation, inside a QGIS edit session so standard undo applies.
"""

from typing import Any, Dict, List

from .layer_export import pick_join_key

OP_ADD_FIELD = "add_field"
OP_UPDATE_FIELD = "update_field"
KNOWN_OPS = (OP_ADD_FIELD, OP_UPDATE_FIELD)

# Requested EpidBot type -> accepted spellings.
_TYPE_ALIASES = {
    "integer": ("integer", "int", "long", "int64"),
    "number": ("number", "float", "double", "real"),
    "string": ("string", "text", "str", "varchar"),
    "boolean": ("boolean", "bool"),
}

# Guard against pathological payloads.
_MAX_VALUES = 500_000


class EditOpError(Exception):
    """Raised when an edit operation is invalid or cannot be applied."""


# ---------------------------------------------------------------------------
# Pure validation and join planning
# ---------------------------------------------------------------------------

def validate_edit_op(op: Dict[str, Any]) -> Dict[str, Any]:
    """Structural validation; returns a normalized copy of ``op``.

    :raises EditOpError: with a user-presentable message.
    """
    if not isinstance(op, dict):
        raise EditOpError("edit operation must be an object")
    kind = op.get("op")
    if kind not in KNOWN_OPS:
        raise EditOpError(
            "unsupported operation {!r} (expected one of {})".format(
                kind, ", ".join(KNOWN_OPS)))
    field = op.get("field")
    if not isinstance(field, dict) or not str(field.get("name", "")).strip():
        raise EditOpError("edit operation is missing a field name")
    type_name = _canonical_type(field.get("type", "string"))
    key = str(op.get("key") or "").strip()
    if not key:
        raise EditOpError(
            "edit operation is missing the join key ('fid' or a field name)")
    values = op.get("values")
    if not isinstance(values, dict) or not values:
        raise EditOpError("edit operation has no values")
    if len(values) > _MAX_VALUES:
        raise EditOpError("too many values ({:,})".format(len(values)))
    return {"op": kind, "layer": str(op.get("layer") or ""),
            "field": {"name": str(field["name"]).strip(),
                      "type": type_name},
            "key": key, "values": dict(values),
            "confidence": str(op.get("confidence") or "")}


def _canonical_type(type_name: Any) -> str:
    lowered = str(type_name or "string").strip().lower()
    for canonical, aliases in _TYPE_ALIASES.items():
        if lowered in aliases:
            return canonical
    return "string"


def coerce_value(value: Any, type_name: str) -> Any:
    """Convert a raw value to the field's type (raises ``ValueError``)."""
    canonical = _canonical_type(type_name)
    if canonical == "integer":
        return int(str(value).strip())
    if canonical == "number":
        return float(str(value).replace(",", ".").strip())
    if canonical == "boolean":
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes", "sim", "t"):
                return True
            if lowered in ("false", "0", "no", "nao", "não", "f"):
                return False
            raise ValueError("not a boolean: {!r}".format(value))
        return bool(value)
    return str(value)


def plan_edit(values: Dict[str, Any], layer_keys: Dict[str, Any],
              type_name: str) -> Dict[str, Any]:
    """Match requested values to features; pure, no QGIS.

    :param values: requested ``key -> raw value`` map.
    :param layer_keys: ``str(key) -> fid`` map built from the real layer.
    :param type_name: canonical field type for coercion.
    :returns: ``{"matched": {fid: value}, "unmatched": [keys],
        "invalid": [{key, value, error}]}``.
    """
    matched: Dict[Any, Any] = {}
    unmatched: List[str] = []
    invalid: List[Dict[str, Any]] = []
    for key, raw in values.items():
        fid = layer_keys.get(str(key).strip())
        if fid is None:
            unmatched.append(str(key))
            continue
        try:
            matched[fid] = coerce_value(raw, type_name)
        except (TypeError, ValueError) as exc:
            invalid.append({"key": str(key), "value": raw,
                            "error": str(exc)})
    return {"matched": matched, "unmatched": unmatched, "invalid": invalid}


def choose_join_key(field_names: List[str]) -> str:
    """The key the plugin advertises to EpidBot: semantic key or ``fid``."""
    semantic = pick_join_key(field_names)
    return semantic if semantic else "fid"


# ---------------------------------------------------------------------------
# QGIS-dependent application
# ---------------------------------------------------------------------------

def build_layer_keys(qgis_layer, key: str) -> Dict[str, Any]:
    """``str(key value) -> fid`` map for one layer.

    ``key == "fid"`` maps the stringified QGIS feature id to itself.
    """
    if key == "fid":
        return {str(feature.id()): feature.id()
                for feature in qgis_layer.getFeatures()}
    index = qgis_layer.fields().lookupField(key)
    if index < 0:
        raise EditOpError(
            "join key field '{}' not found on layer '{}'".format(
                key, qgis_layer.name()))
    return {str(feature[index]).strip(): feature.id()
            for feature in qgis_layer.getFeatures()}


def apply_edit_op(qgis_layer, op: Dict[str, Any]) -> Dict[str, Any]:
    """Validate, plan and apply one edit operation inside an edit session.

    The caller is responsible for having confirmed the operation with the
    user (preview dialog). Raises :class:`EditOpError` without touching the
    layer when validation fails; on commit failure the session is rolled
    back before raising.
    """
    from qgis.core import QgsField

    normalized = validate_edit_op(op)
    kind = normalized["op"]
    field_name = normalized["field"]["name"]
    type_name = normalized["field"]["type"]
    key = normalized["key"]

    existing_index = qgis_layer.fields().lookupField(field_name)
    if kind == OP_ADD_FIELD and existing_index >= 0:
        raise EditOpError(
            "field '{}' already exists on '{}' - use update_field".format(
                field_name, qgis_layer.name()))
    if kind == OP_UPDATE_FIELD and existing_index < 0:
        raise EditOpError(
            "field '{}' does not exist on '{}'".format(
                field_name, qgis_layer.name()))

    layer_keys = build_layer_keys(qgis_layer, key)
    plan = plan_edit(normalized["values"], layer_keys, type_name)
    if not plan["matched"]:
        raise EditOpError(
            "no features matched ({} unmatched keys)".format(
                len(plan["unmatched"])))

    if not qgis_layer.startEditing():
        raise EditOpError(
            "could not start an edit session on '{}'".format(
                qgis_layer.name()))
    try:
        if kind == OP_ADD_FIELD:
            if not qgis_layer.addAttribute(
                    QgsField(field_name, _qvariant_type(type_name))):
                raise EditOpError(
                    "could not add field '{}'".format(field_name))
            field_index = qgis_layer.fields().lookupField(field_name)
        else:
            field_index = existing_index
        for fid, value in plan["matched"].items():
            if not qgis_layer.changeAttributeValue(
                    fid, field_index, value):
                raise EditOpError(
                    "could not set {}={} on feature {}".format(
                        field_name, value, fid))
        if not qgis_layer.commitChanges():
            errors = "; ".join(qgis_layer.commitErrors()) or "unknown error"
            raise EditOpError("commit failed: {}".format(errors))
    except Exception:
        if qgis_layer.isEditable():
            qgis_layer.rollBack()
        raise

    return {"layer": qgis_layer.name(), "op": kind, "field": field_name,
            "matched": len(plan["matched"]),
            "unmatched": plan["unmatched"],
            "invalid": plan["invalid"],
            "confidence": normalized["confidence"]}


def _qvariant_type(type_name: str):
    """Map a canonical EpidBot type to a QVariant for QgsField."""
    from qgis.PyQt.QtCore import QVariant

    return {"integer": QVariant.Int,
            "number": QVariant.Double,
            "boolean": QVariant.Bool,
            "string": QVariant.String}[_canonical_type(type_name)]
