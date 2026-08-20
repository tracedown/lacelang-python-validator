"""Formatter tests — AST expressions rendered back to Lace source (fmt)."""

from lacelang_validator.ast_fmt import fmt
from lacelang_validator.cli import strip_ast_metadata
from lacelang_validator.parser import parse


def _condition(expr_src):
    src = 'get("$u")\n    .assert({ expect: [%s] })\n' % expr_src
    ast = strip_ast_metadata(parse(src))
    return ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]


def _lit(value, value_type):
    return {"kind": "literal", "valueType": value_type, "value": value}


class TestObjectKeyQuoting:
    def test_bare_identifier_keys_stay_bare(self):
        expr = {
            "kind": "objectLit",
            "entries": [
                {"key": "ok", "value": _lit(True, "bool")},
                {"key": "_x1", "value": _lit(2, "int")},
            ],
        }
        assert fmt(expr) == "{ok: true, _x1: 2}"

    def test_numeric_key_is_quoted(self):
        expr = {
            "kind": "objectLit",
            "entries": [{"key": "404", "value": _lit(2, "int")}],
        }
        assert fmt(expr) == '{"404": 2}'

    def test_hyphenated_key_is_quoted(self):
        expr = {
            "kind": "objectLit",
            "entries": [{"key": "content-type", "value": _lit(1, "int")}],
        }
        assert fmt(expr) == '{"content-type": 1}'

    def test_printed_object_lit_reparses_to_equal_ast(self):
        expr_src = 'count([{"content-type": 1, "404": 2, ok: true}]) eq 1'
        cond = _condition(expr_src)
        printed = fmt(cond)
        assert printed == expr_src
        assert _condition(printed) == cond
