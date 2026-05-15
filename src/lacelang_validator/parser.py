"""Recursive descent parser for Lace — emits AST dicts matching `ast.json`.

The parser is permissive: it accepts any syntactically well-formed script,
including calls to unknown identifiers and extension-shaped fields. The
validator (spec §12) is responsible for rejecting semantic errors.

Grammar reference: `lacelang.g4`.
"""

import re
from typing import Any

from lacelang_validator.lexer import Token, tokenize

# A string literal whose content is *exactly* one of:
#   $$ident        → run_var
#   $ident         → script_var
# collapses to the corresponding expression node at parse time (spec §3.5:
# full-string interpolation is equivalent to the value reference). Mixed
# strings with surrounding text remain literals; interpolation is re-scanned
# by the executor at evaluation time.
_PURE_RUN_RE = re.compile(r"^\$\$([a-zA-Z_][a-zA-Z0-9_]*)$")
_PURE_VAR_RE = re.compile(r"^\$([a-zA-Z_][a-zA-Z0-9_]*)$")


def _string_to_expr(s: str) -> dict[str, Any]:
    m = _PURE_RUN_RE.match(s)
    if m:
        return {"kind": "runVar", "name": m.group(1)}
    m = _PURE_VAR_RE.match(s)
    if m:
        return {"kind": "scriptVar", "name": m.group(1)}
    return {"kind": "literal", "valueType": "string", "value": s}

AST_VERSION = "0.9.2"

SCOPE_NAMES = {
    "status", "body", "headers", "bodySize", "totalDelayMs",
    "dns", "connect", "tls", "ttfb", "transfer", "size",
    "redirects",
}

CALL_FIELD_KEYWORDS = {
    "headers", "body", "cookies", "cookieJar", "clearCookies",
    "redirects", "security", "timeout",
}


class ParseError(Exception):
    def __init__(self, message: str, line: int):
        super().__init__(f"line {line}: {message}")
        self.message = message
        self.line = line


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    # ── token helpers ────────────────────────────────────────────────

    @property
    def tok(self) -> Token:
        return self.tokens[self.pos]

    def _peek(self, offset: int = 0) -> Token:
        return self.tokens[self.pos + offset]

    def _advance(self) -> Token:
        t = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return t

    def _check(self, ttype: str, value: str | None = None) -> bool:
        t = self.tok
        if t.type != ttype:
            return False
        if value is not None and t.value != value:
            return False
        return True

    def _match(self, ttype: str, value: str | None = None) -> Token | None:
        if self._check(ttype, value):
            return self._advance()
        return None

    def _expect(self, ttype: str, value: str | None = None) -> Token:
        if self._check(ttype, value):
            return self._advance()
        want = value if value is not None else ttype
        got = self.tok.value or self.tok.type
        raise ParseError(f"expected {want!r}, got {got!r}", self.tok.line)

    def _expect_kw(self, *kws: str) -> Token:
        if self.tok.type == "KEYWORD" and self.tok.value in kws:
            return self._advance()
        got = self.tok.value or self.tok.type
        raise ParseError(f"expected keyword one of {kws}, got {got!r}", self.tok.line)

    def _is_ident_key(self) -> bool:
        """Matches identKey: IDENT or any keyword (grammar §identKey)."""
        return self.tok.type == "IDENT" or self.tok.type == "KEYWORD"

    # ── script ───────────────────────────────────────────────────────

    def parse_script(self) -> dict[str, Any]:
        calls = [self.parse_call()]
        while not self._check("EOF"):
            calls.append(self.parse_call())
        self._expect("EOF")
        return {"version": AST_VERSION, "calls": calls}

    # ── call ─────────────────────────────────────────────────────────

    def parse_call(self) -> dict[str, Any]:
        method_tok = self._expect_kw("get", "post", "put", "patch", "delete")
        self._expect("LPAREN")
        url_tok = self._expect("STRING")
        call: dict[str, Any] = {"method": method_tok.value, "url": url_tok.value}
        if self._match("COMMA"):
            call["config"] = self.parse_call_config()
        self._expect("RPAREN")
        call["chain"] = self.parse_chain()
        return call

    # ── call config ──────────────────────────────────────────────────

    def parse_call_config(self) -> dict[str, Any]:
        self._expect("LBRACE")
        config: dict[str, Any] = {}
        extensions: dict[str, Any] = {}
        while not self._check("RBRACE"):
            key = self.tok
            if key.type == "KEYWORD" and key.value in CALL_FIELD_KEYWORDS:
                self._advance()
                self._expect("COLON")
                self._parse_call_field(key.value, config)
            elif key.type == "IDENT":
                self._advance()
                self._expect("COLON")
                extensions[key.value] = self.parse_options_value()
            else:
                raise ParseError(f"unexpected call config field {key.value!r}", key.line)
            if not self._match("COMMA"):
                break
        self._expect("RBRACE")
        if extensions:
            config["extensions"] = extensions
        return config

    def _parse_call_field(self, name: str, config: dict[str, Any]) -> None:
        if name == "headers":
            config["headers"] = self._obj_lit_to_map(self.parse_object_lit())
        elif name == "body":
            config["body"] = self.parse_body_value()
        elif name == "cookies":
            config["cookies"] = self._obj_lit_to_map(self.parse_object_lit())
        elif name == "cookieJar":
            tok = self._expect("STRING"); config["cookieJar"] = tok.value
        elif name == "clearCookies":
            self._expect("LBRACK")
            vals = [self._expect("STRING").value]
            while self._match("COMMA"):
                if self._check("RBRACK"):
                    break
                vals.append(self._expect("STRING").value)
            self._expect("RBRACK")
            config["clearCookies"] = vals
        elif name == "redirects":
            config["redirects"] = self._parse_typed_obj({"follow": "BOOL", "max": "INT"})
        elif name == "security":
            config["security"] = self._parse_typed_obj({"rejectInvalidCerts": "BOOL"})
        elif name == "timeout":
            config["timeout"] = self._parse_typed_obj(
                {"ms": "INT", "action": "STRING", "retries": "INT"}
            )

    def _parse_typed_obj(self, fields: dict[str, str]) -> dict[str, Any]:
        """Parse `{ key: typed_literal, … , extensions allowed }`."""
        self._expect("LBRACE")
        obj: dict[str, Any] = {}
        extensions: dict[str, Any] = {}
        while not self._check("RBRACE"):
            key = self.tok
            if key.type == "KEYWORD" and key.value in fields:
                self._advance()
                self._expect("COLON")
                expected = fields[key.value]
                if expected == "BOOL":
                    t = self._expect("BOOL"); obj[key.value] = t.value == "true"
                elif expected == "INT":
                    t = self._expect("INT"); obj[key.value] = int(t.value)
                elif expected == "STRING":
                    t = self._expect("STRING"); obj[key.value] = t.value
            elif key.type == "IDENT":
                self._advance()
                self._expect("COLON")
                extensions[key.value] = self.parse_options_value()
            else:
                raise ParseError(f"unexpected field {key.value!r}", key.line)
            if not self._match("COMMA"):
                break
        self._expect("RBRACE")
        if extensions:
            obj["extensions"] = extensions
        return obj

    def parse_body_value(self) -> dict[str, Any]:
        if self._check("KEYWORD", "json"):
            self._advance(); self._expect("LPAREN")
            val = self.parse_object_lit(); self._expect("RPAREN")
            return {"type": "json", "value": val}
        if self._check("KEYWORD", "form"):
            self._advance(); self._expect("LPAREN")
            val = self.parse_object_lit(); self._expect("RPAREN")
            return {"type": "form", "value": val}
        if self._check("STRING"):
            return {"type": "raw", "value": self._advance().value}
        raise ParseError(f"expected body value, got {self.tok.value!r}", self.tok.line)

    # ── chain ────────────────────────────────────────────────────────

    def parse_chain(self) -> dict[str, Any]:
        """Permissively collect chain methods into a dict preserving source order.

        Duplicate-detection and order-check live in the validator. If the same
        chain method appears twice the later wins (tracked separately for
        CHAIN_DUPLICATE diagnostics).
        """
        chain: dict[str, Any] = {}
        order: list[str] = []
        duplicates: list[str] = []

        if not self._check("DOT"):
            raise ParseError("expected chain method after call arguments", self.tok.line)

        while self._match("DOT"):
            name_tok = self._expect_kw("expect", "check", "assert", "store", "wait")
            name = name_tok.value
            if name in chain:
                duplicates.append(name)
            order.append(name)
            self._expect("LPAREN")
            if name == "expect" or name == "check":
                chain[name] = self.parse_scope_list()
            elif name == "assert":
                chain[name] = self.parse_assert_block()
            elif name == "store":
                chain[name] = self.parse_store_block()
            elif name == "wait":
                t = self._expect("INT")
                chain[name] = int(t.value)
            self._expect("RPAREN")

        # stash ordering + duplicates on chain for validator consumption
        chain["__order"] = order
        if duplicates:
            chain["__duplicates"] = duplicates
        return chain

    # ── scope blocks ─────────────────────────────────────────────────

    def parse_scope_list(self) -> dict[str, Any]:
        block: dict[str, Any] = {}
        duplicates: list[str] = []
        if self._check("RPAREN"):
            block["__order"] = []
            return block
        order: list[str] = []
        while True:
            name = self._parse_scope_name()
            self._expect("COLON")
            val = self.parse_scope_val()
            if name in block:
                duplicates.append(name)
            block[name] = val
            order.append(name)
            if not self._match("COMMA"):
                break
            if self._check("RPAREN"):
                break
        block["__order"] = order
        if duplicates:
            block["__duplicates"] = duplicates
        return block

    def _parse_scope_name(self) -> str:
        if self.tok.type == "KEYWORD" and self.tok.value in SCOPE_NAMES:
            return self._advance().value
        # `headers` is both a scope name and a call-config key — still a keyword.
        raise ParseError(f"expected scope name, got {self.tok.value!r}", self.tok.line)

    def parse_scope_val(self) -> dict[str, Any]:
        # Full form: { value:, op:, options: }
        if self._check("LBRACE"):
            return self._parse_scope_full_form()
        # Array shorthand: [ expr, ... ]
        if self._check("LBRACK"):
            return {"value": self.parse_array_lit()}
        # Scalar shorthand: single expression
        return {"value": self.parse_expr()}

    def _parse_scope_full_form(self) -> dict[str, Any]:
        self._expect("LBRACE")
        out: dict[str, Any] = {}
        while not self._check("RBRACE"):
            k = self.tok
            if k.type == "KEYWORD" and k.value == "value":
                self._advance(); self._expect("COLON")
                if self._check("LBRACK"):
                    out["value"] = self.parse_array_lit()
                else:
                    out["value"] = self.parse_expr()
            elif k.type == "KEYWORD" and k.value == "op":
                self._advance(); self._expect("COLON")
                t = self._expect("STRING"); out["op"] = t.value
            elif k.type == "KEYWORD" and k.value == "match":
                self._advance(); self._expect("COLON")
                t = self._expect("STRING"); out["match"] = t.value
            elif k.type == "KEYWORD" and k.value == "mode":
                self._advance(); self._expect("COLON")
                t = self._expect("STRING"); out["mode"] = t.value
            elif k.type == "KEYWORD" and k.value == "options":
                self._advance(); self._expect("COLON")
                out["options"] = self.parse_options_obj()
            else:
                raise ParseError(f"unexpected scope field {k.value!r}", k.line)
            if not self._match("COMMA"):
                break
        self._expect("RBRACE")
        if "value" not in out:
            raise ParseError("scope full form requires 'value'", self.tok.line)
        return out

    # ── options (extension passthrough) ──────────────────────────────

    def parse_options_obj(self) -> dict[str, Any]:
        self._expect("LBRACE")
        out: dict[str, Any] = {}
        if self._match("RBRACE"):
            return out
        while True:
            key = self._expect("IDENT")
            self._expect("COLON")
            out[key.value] = self.parse_options_value()
            if not self._match("COMMA"):
                break
            if self._check("RBRACE"):
                break
        self._expect("RBRACE")
        return out

    def parse_options_value(self) -> Any:
        """optionsValue: expr | objectLit | array-of-options.

        Returned as the AST Expr-node shape the schema expects; object literals
        are Expr ObjectLitExpr nodes.
        """
        if self._check("LBRACE"):
            return self.parse_object_lit()
        if self._check("LBRACK"):
            items: list[Any] = []
            self._expect("LBRACK")
            if not self._check("RBRACK"):
                while True:
                    items.append(self.parse_options_value())
                    if not self._match("COMMA"):
                        break
                    if self._check("RBRACK"):
                        break
            self._expect("RBRACK")
            return {"kind": "arrayLit", "items": items}
        return self.parse_expr()

    # ── assert / store ───────────────────────────────────────────────

    def parse_assert_block(self) -> dict[str, Any]:
        self._expect("LBRACE")
        out: dict[str, Any] = {}
        while not self._check("RBRACE"):
            k = self.tok
            if k.type == "KEYWORD" and k.value in ("expect", "check"):
                self._advance(); self._expect("COLON")
                self._expect("LBRACK")
                items: list[dict[str, Any]] = []
                if not self._check("RBRACK"):
                    while True:
                        items.append(self.parse_condition_item())
                        if not self._match("COMMA"):
                            break
                        if self._check("RBRACK"):
                            break
                self._expect("RBRACK")
                out[k.value] = items
            else:
                raise ParseError(f"unexpected assert clause {k.value!r}", k.line)
            if not self._match("COMMA"):
                break
        self._expect("RBRACE")
        return out

    def parse_condition_item(self) -> dict[str, Any]:
        if self._check("LBRACE"):
            # full form — but only if a known field appears; peek ahead.
            save = self.pos
            self._advance()
            first = self.tok
            self.pos = save
            if first.type == "KEYWORD" and first.value in ("condition", "options"):
                return self._parse_condition_full_form()
        return {"condition": self.parse_expr()}

    def _parse_condition_full_form(self) -> dict[str, Any]:
        self._expect("LBRACE")
        out: dict[str, Any] = {}
        while not self._check("RBRACE"):
            k = self.tok
            if k.type == "KEYWORD" and k.value == "condition":
                self._advance(); self._expect("COLON")
                out["condition"] = self.parse_expr()
            elif k.type == "KEYWORD" and k.value == "options":
                self._advance(); self._expect("COLON")
                out["options"] = self.parse_options_obj()
            else:
                raise ParseError(f"unexpected condition field {k.value!r}", k.line)
            if not self._match("COMMA"):
                break
        self._expect("RBRACE")
        if "condition" not in out:
            raise ParseError("condition full form requires 'condition'", self.tok.line)
        return out

    def parse_store_block(self) -> dict[str, Any]:
        self._expect("LBRACE")
        out: dict[str, Any] = {}
        if self._match("RBRACE"):
            return out
        while True:
            key, src_key = self._parse_store_key()
            self._expect("COLON")
            val = self.parse_expr()
            scope = "run" if src_key.startswith("$$") else "writeback"
            out[key] = {"scope": scope, "value": val}
            if not self._match("COMMA"):
                break
            if self._check("RBRACE"):
                break
        self._expect("RBRACE")
        return out

    def _parse_store_key(self) -> tuple[str, str]:
        """Returns (normalised_key, raw_source_key).

        Spec §4.6: `$$name` → run-scope; `$name`, bare, and quoted string
        keys → writeback. A quoted string literal of form "$$foo" also
        binds run-scope (handled by caller).
        """
        t = self.tok
        if t.type == "RUN_VAR":
            self._advance()
            return t.value, t.value
        if t.type == "SCRIPT_VAR":
            self._advance()
            return t.value, t.value
        if t.type == "STRING":
            self._advance()
            return t.value, t.value
        if self._is_ident_key():
            self._advance()
            return t.value, t.value
        raise ParseError(f"unexpected store key {t.value!r}", t.line)

    # ── expressions ──────────────────────────────────────────────────
    # Precedence climb: or < and < eq < ord < addsub < muldiv < unary < primary

    def parse_expr(self) -> dict[str, Any]:
        return self._parse_or()

    def _parse_or(self) -> dict[str, Any]:
        left = self._parse_and()
        while self.tok.type == "KEYWORD" and self.tok.value == "or":
            self._advance()
            right = self._parse_and()
            left = {"kind": "binary", "op": "or", "left": left, "right": right}
        return left

    def _parse_and(self) -> dict[str, Any]:
        left = self._parse_eq()
        while self.tok.type == "KEYWORD" and self.tok.value == "and":
            self._advance()
            right = self._parse_eq()
            left = {"kind": "binary", "op": "and", "left": left, "right": right}
        return left

    _EQ_OPS: frozenset[str] = frozenset({"eq", "neq"})
    _ORD_OPS: frozenset[str] = frozenset({"lt", "lte", "gt", "gte"})

    def _parse_eq(self) -> dict[str, Any]:
        left = self._parse_ord()
        # Comparisons do not chain — at most one eq/neq per layer.
        # `a eq b eq c` must be written as `(a eq b) and (b eq c)`.
        if self.tok.type == "KEYWORD" and self.tok.value in self._EQ_OPS:
            op = self._advance().value
            right = self._parse_ord()
            left = {"kind": "binary", "op": op, "left": left, "right": right}
            if self.tok.type == "KEYWORD" and self.tok.value in self._EQ_OPS:
                raise ParseError(
                    f"chained comparison {op!r}: comparisons do not associate; "
                    "use `and`/`or` with parentheses to combine",
                    self.tok.line,
                )
        return left

    def _parse_ord(self) -> dict[str, Any]:
        left = self._parse_addsub()
        if self.tok.type == "KEYWORD" and self.tok.value in self._ORD_OPS:
            op = self._advance().value
            right = self._parse_addsub()
            left = {"kind": "binary", "op": op, "left": left, "right": right}
            if self.tok.type == "KEYWORD" and self.tok.value in self._ORD_OPS:
                raise ParseError(
                    f"chained comparison {op!r}: comparisons do not associate; "
                    "use `and`/`or` with parentheses to combine",
                    self.tok.line,
                )
        return left

    def _parse_addsub(self) -> dict[str, Any]:
        left = self._parse_muldiv()
        while self.tok.type in ("PLUS", "MINUS"):
            op = self._advance().value
            right = self._parse_muldiv()
            left = {"kind": "binary", "op": op, "left": left, "right": right}
        return left

    def _parse_muldiv(self) -> dict[str, Any]:
        left = self._parse_unary()
        while self.tok.type in ("STAR", "SLASH", "PERCENT"):
            op = self._advance().value
            right = self._parse_unary()
            left = {"kind": "binary", "op": op, "left": left, "right": right}
        return left

    def _parse_unary(self) -> dict[str, Any]:
        if self.tok.type == "KEYWORD" and self.tok.value == "not":
            self._advance()
            return {"kind": "unary", "op": "not", "operand": self._parse_unary()}
        if self.tok.type == "MINUS":
            self._advance()
            return {"kind": "unary", "op": "-", "operand": self._parse_unary()}
        return self._parse_primary()

    def _parse_primary(self) -> dict[str, Any]:
        t = self.tok
        if t.type == "LPAREN":
            self._advance()
            e = self.parse_expr()
            self._expect("RPAREN")
            return e
        # Composite literals are first-class expressions (spec §2.1).
        if t.type == "LBRACK":
            return self.parse_array_lit()
        if t.type == "LBRACE":
            return self.parse_object_lit()
        if t.type == "KEYWORD" and t.value == "this":
            return self._parse_this_ref()
        if t.type == "KEYWORD" and t.value == "prev":
            return self._parse_prev_ref()
        if t.type == "RUN_VAR":
            self._advance()
            return self._parse_var_tail({"kind": "runVar", "name": t.value[2:], "path": []})
        if t.type == "SCRIPT_VAR":
            self._advance()
            return self._parse_var_tail({"kind": "scriptVar", "name": t.value[1:], "path": []})
        if t.type == "STRING":
            self._advance()
            return _string_to_expr(t.value)
        if t.type == "INT":
            self._advance()
            return {"kind": "literal", "valueType": "int", "value": int(t.value)}
        if t.type == "FLOAT":
            self._advance()
            return {"kind": "literal", "valueType": "float", "value": float(t.value)}
        if t.type == "BOOL":
            self._advance()
            return {"kind": "literal", "valueType": "bool", "value": t.value == "true"}
        if t.type == "KEYWORD" and t.value == "null":
            self._advance()
            return {"kind": "literal", "valueType": "null", "value": None}
        # function call: IDENT or keyword in {json, form, schema} followed by (
        if (t.type == "IDENT" or (t.type == "KEYWORD" and t.value in ("json", "form", "schema"))) \
                and self._peek(1).type == "LPAREN":
            return self._parse_func_call()
        raise ParseError(f"unexpected token {t.value!r}", t.line)

    def _parse_var_tail(self, node: dict[str, Any]) -> dict[str, Any]:
        """Attach an optional `.field` / `[index]` path to a scriptVar /
        runVar node. Mirrors prevRef path syntax: identKey fields + int
        subscripts. Empty path returns the node unchanged (and omits the
        `path` key for backward compatibility with existing AST shape)."""
        path: list[dict[str, Any]] = []
        while True:
            if self._match("DOT"):
                if not self._is_ident_key():
                    raise ParseError("expected field name after '.'", self.tok.line)
                path.append({"type": "field", "name": self._advance().value})
            elif self._match("LBRACK"):
                idx = self._expect("INT")
                self._expect("RBRACK")
                path.append({"type": "index", "index": int(idx.value)})
            else:
                break
        if path:
            node["path"] = path
        else:
            node.pop("path", None)
        return node

    def _parse_this_ref(self) -> dict[str, Any]:
        self._advance()  # this
        path: list[str] = []
        while self._match("DOT"):
            if not self._is_ident_key():
                raise ParseError("expected field name after '.'", self.tok.line)
            path.append(self._advance().value)
        if not path:
            raise ParseError("'this' requires at least one '.field'", self.tok.line)
        return {"kind": "thisRef", "path": path}

    def _parse_prev_ref(self) -> dict[str, Any]:
        self._advance()  # prev
        path: list[dict[str, Any]] = []
        while True:
            if self._match("DOT"):
                if not self._is_ident_key():
                    raise ParseError("expected field name after '.'", self.tok.line)
                path.append({"type": "field", "name": self._advance().value})
            elif self._match("LBRACK"):
                t = self._expect("INT")
                self._expect("RBRACK")
                path.append({"type": "index", "index": int(t.value)})
            else:
                break
        return {"kind": "prevRef", "path": path}

    def _parse_func_call(self) -> dict[str, Any]:
        name = self._advance().value
        self._expect("LPAREN")
        args: list[Any] = []
        if not self._check("RPAREN"):
            while True:
                if self._check("LBRACE"):
                    args.append(self.parse_object_lit())
                else:
                    args.append(self.parse_expr())
                if not self._match("COMMA"):
                    break
                if self._check("RPAREN"):
                    break
        self._expect("RPAREN")
        return {"kind": "funcCall", "name": name, "args": args}

    # ── object / array literals ──────────────────────────────────────

    def parse_object_lit(self) -> dict[str, Any]:
        self._expect("LBRACE")
        entries: list[dict[str, Any]] = []
        if self._match("RBRACE"):
            return {"kind": "objectLit", "entries": entries}
        while True:
            k = self.tok
            if k.type == "STRING":
                key = self._advance().value
            elif self._is_ident_key():
                key = self._advance().value
            else:
                raise ParseError(f"expected object key, got {k.value!r}", k.line)
            self._expect("COLON")
            entries.append({"key": key, "value": self.parse_expr()})
            if not self._match("COMMA"):
                break
            if self._check("RBRACE"):
                break
        self._expect("RBRACE")
        return {"kind": "objectLit", "entries": entries}

    def parse_array_lit(self) -> dict[str, Any]:
        self._expect("LBRACK")
        items: list[dict[str, Any]] = []
        if self._match("RBRACK"):
            return {"kind": "arrayLit", "items": items}
        while True:
            items.append(self.parse_expr())
            if not self._match("COMMA"):
                break
            if self._check("RBRACK"):
                break
        self._expect("RBRACK")
        return {"kind": "arrayLit", "items": items}

    # ── small helpers ────────────────────────────────────────────────

    @staticmethod
    def _obj_lit_to_map(lit: dict[str, Any]) -> dict[str, Any]:
        """Convert an object_lit (entries list) into a key → Expr map for
        schema fields that expect additionalProperties: Expr (e.g. headers).
        """
        out: dict[str, Any] = {}
        for e in lit["entries"]:
            out[e["key"]] = e["value"]
        return out


def parse(source: str) -> dict[str, Any]:
    """Tokenize and parse. Raises ParseError on syntax issues."""
    tokens = tokenize(source)
    return Parser(tokens).parse_script()
