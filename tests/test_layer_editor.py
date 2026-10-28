"""Tests for :mod:`core.layer_editor` (pure validation/planning logic)."""

import pytest

from core.layer_editor import (EditOpError, choose_join_key, coerce_value,
                               plan_edit, validate_edit_op)

VALID_OP = {
    "op": "add_field",
    "layer": "dengue_cases",
    "field": {"name": "pop_2024", "type": "integer"},
    "key": "CD_MUN",
    "values": {"1100205": 12345, "1200301": 678},
    "confidence": "fetched",
}


class TestValidateEditOp:
    def test_valid_op_passes(self):
        normalized = validate_edit_op(VALID_OP)
        assert normalized["op"] == "add_field"
        assert normalized["field"] == {"name": "pop_2024", "type": "integer"}
        assert normalized["key"] == "CD_MUN"
        assert normalized["confidence"] == "fetched"

    def test_unknown_op_rejected(self):
        with pytest.raises(EditOpError, match="unsupported operation"):
            validate_edit_op({"op": "delete_layer"})

    def test_missing_field_name_rejected(self):
        bad = dict(VALID_OP, field={"type": "integer"})
        with pytest.raises(EditOpError, match="field name"):
            validate_edit_op(bad)

    def test_missing_key_rejected(self):
        bad = dict(VALID_OP, key="")
        with pytest.raises(EditOpError, match="join key"):
            validate_edit_op(bad)

    def test_empty_values_rejected(self):
        bad = dict(VALID_OP, values={})
        with pytest.raises(EditOpError, match="no values"):
            validate_edit_op(bad)

    def test_unknown_type_defaults_to_string(self):
        op = dict(VALID_OP, field={"name": "x", "type": "weird"})
        assert validate_edit_op(op)["field"]["type"] == "string"

    def test_type_aliases_are_canonicalized(self):
        op = dict(VALID_OP, field={"name": "x", "type": "double"})
        assert validate_edit_op(op)["field"]["type"] == "number"

    def test_non_dict_rejected(self):
        with pytest.raises(EditOpError):
            validate_edit_op(["not", "a", "dict"])


class TestCoerceValue:
    def test_integer_from_string(self):
        assert coerce_value(" 42 ", "integer") == 42

    def test_integer_rejects_float_text(self):
        with pytest.raises(ValueError):
            coerce_value("4.2", "integer")

    def test_number_accepts_comma_decimal(self):
        assert coerce_value("3,14", "number") == pytest.approx(3.14)

    def test_boolean_parses_pt_en_variants(self):
        assert coerce_value("True", "boolean") is True
        assert coerce_value("sim", "boolean") is True
        assert coerce_value("não", "boolean") is False
        with pytest.raises(ValueError):
            coerce_value("maybe", "boolean")

    def test_string_passes_through(self):
        assert coerce_value(12, "string") == "12"

    def test_leading_zero_codes_stay_strings(self):
        assert coerce_value("0500205", "string") == "0500205"


class TestPlanEdit:
    KEYS = {"1100205": 10, "1200301": 20, "3106200": 30}

    def test_matching_and_unmatched(self):
        plan = plan_edit({"1100205": 5, "9999999": 1}, self.KEYS, "integer")
        assert plan["matched"] == {10: 5}
        assert plan["unmatched"] == ["9999999"]
        assert plan["invalid"] == []

    def test_invalid_values_are_reported_not_fatal(self):
        plan = plan_edit({"1100205": "abc", "1200301": 7}, self.KEYS,
                         "integer")
        assert plan["matched"] == {20: 7}
        assert plan["invalid"] == [{"key": "1100205", "value": "abc",
                                    "error": "invalid literal for int() "
                                             "with base 10: 'abc'"}]

    def test_keys_are_normalized(self):
        plan = plan_edit({" 1100205 ": 5}, self.KEYS, "integer")
        assert plan["matched"] == {10: 5}

    def test_all_unmatched(self):
        plan = plan_edit({"1": 2}, {}, "integer")
        assert plan["matched"] == {}
        assert plan["unmatched"] == ["1"]


class TestChooseJoinKey:
    def test_semantic_key_preferred(self):
        assert choose_join_key(["fid", "uf", "CD_MUN"]) == "CD_MUN"

    def test_fid_fallback(self):
        assert choose_join_key(["nome", "casos"]) == "fid"
