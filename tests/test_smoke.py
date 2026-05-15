"""Smoke tests — basic parse/validate/roundtrip checks."""

import json

from lacelang_validator.cli import strip_ast_metadata
from lacelang_validator.parser import parse
from lacelang_validator.validator import validate


def test_minimal_get_parses():
    ast = strip_ast_metadata(parse('get("$u").expect(status: 200)\n'))
    assert ast["version"] == "0.9.2"
    [call] = ast["calls"]
    assert call["method"] == "get"
    assert call["url"] == "$u"
    assert call["chain"]["expect"]["status"]["value"]["value"] == 200


def test_chain_order_violation():
    ast = parse('get("$u").store({ a: this.body.x }).assert({ expect: [this.status eq 200] })\n')
    sink = validate(ast, variables=["u"])
    codes = [e.code for e in sink.errors]
    assert "CHAIN_ORDER" in codes


def test_unknown_function():
    ast = parse('get("$x").assert({ expect: [random() gt 5] })\n')
    sink = validate(ast, variables=["x"])
    assert "UNKNOWN_FUNCTION" in [e.code for e in sink.errors]


def test_emit_roundtrip_matches_schema_shape():
    ast = strip_ast_metadata(parse(
        'post("$url", { body: json({ a: "$x" }) }).expect(status: 200)\n'
    ))
    txt = json.dumps(ast)
    assert "objectLit" in txt
    assert "__order" not in txt
