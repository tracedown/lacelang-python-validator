"""Render AST expressions back to source-form Lace text.

Used by the executor to populate the ``expression`` field on assert-type
assertion records (spec §9.2, ``assertions[].expression``). The output
should round-trip: it parses back to
an equivalent AST (modulo whitespace). Operator precedence is preserved by
always parenthesising binary sub-expressions that could be ambiguous.
"""

from __future__ import annotations

import json
from typing import Any

_BINARY_PRIORITY = {
    "or": 1, "and": 2,
    "eq": 3, "neq": 3,
    "lt": 4, "lte": 4, "gt": 4, "gte": 4,
    "+": 5, "-": 5,
    "*": 6, "/": 6, "%": 6,
}


def fmt(expr: Any) -> str:
    if not isinstance(expr, dict):
        return json.dumps(expr)
    k = expr.get("kind")
    if k == "literal":
        vt = expr.get("valueType")
        v = expr.get("value")
        if vt == "string":
            return json.dumps(v)
        if vt == "null":
            return "null"
        if vt == "bool":
            return "true" if v else "false"
        return str(v)
    if k == "scriptVar":
        return f"${expr['name']}" + _fmt_var_path(expr.get("path"))
    if k == "runVar":
        return f"$${expr['name']}" + _fmt_var_path(expr.get("path"))
    if k == "thisRef":
        return "this" + "".join(f".{p}" for p in expr.get("path", []))
    if k == "prevRef":
        out = "prev"
        for seg in expr.get("path", []):
            out += f".{seg['name']}" if seg["type"] == "field" else f"[{seg['index']}]"
        return out
    if k == "unary":
        op = expr["op"]
        # Unary ops are `not` (keyword) and `-` (arithmetic negation).
        if op == "not":
            return f"not {fmt(expr['operand'])}"
        return f"{op}{fmt(expr['operand'])}"
    if k == "binary":
        op = expr["op"]
        return f"{_paren(expr['left'], op)} {op} {_paren(expr['right'], op)}"
    if k == "funcCall":
        args = ", ".join(fmt(a) for a in expr.get("args", []))
        return f"{expr['name']}({args})"
    if k == "objectLit":
        entries = ", ".join(f"{e['key']}: {fmt(e['value'])}" for e in expr.get("entries", []))
        return "{" + entries + "}"
    if k == "arrayLit":
        return "[" + ", ".join(fmt(i) for i in expr.get("items", [])) + "]"
    return "<unknown>"


def _fmt_var_path(path: Any) -> str:
    if not path:
        return ""
    out = ""
    for seg in path:
        if seg.get("type") == "field":
            out += f".{seg['name']}"
        else:
            out += f"[{seg['index']}]"
    return out


def _paren(sub: Any, outer_op: str) -> str:
    if isinstance(sub, dict) and sub.get("kind") == "binary":
        inner = sub["op"]
        if _BINARY_PRIORITY.get(inner, 99) < _BINARY_PRIORITY.get(outer_op, 99):
            return f"({fmt(sub)})"
    return fmt(sub)
