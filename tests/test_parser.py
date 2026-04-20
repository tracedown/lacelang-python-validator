"""Parser tests — AST structure from Lace source text."""

import pytest
from lacelang_validator.parser import parse, ParseError
from lacelang_validator.cli import strip_ast_metadata


def _parse(src):
    return strip_ast_metadata(parse(src))


class TestCallParsing:
    def test_get(self):
        ast = _parse('get("https://example.com")\n    .expect(status: 200)\n')
        assert ast["version"] == "0.9.0"
        assert len(ast["calls"]) == 1
        assert ast["calls"][0]["method"] == "get"
        assert ast["calls"][0]["url"] == "https://example.com"

    def test_all_methods(self):
        for m in ("get", "post", "put", "patch", "delete"):
            ast = _parse(f'{m}("$u")\n    .expect(status: 200)\n')
            assert ast["calls"][0]["method"] == m

    def test_multiple_calls(self):
        ast = _parse(
            'get("$a")\n    .expect(status: 200)\n'
            'post("$b")\n    .expect(status: 201)\n'
        )
        assert len(ast["calls"]) == 2
        assert ast["calls"][0]["method"] == "get"
        assert ast["calls"][1]["method"] == "post"


class TestCallConfig:
    def test_headers(self):
        ast = _parse(
            'get("$u", {\n'
            '    headers: { "X-Token": "abc" }\n'
            '})\n    .expect(status: 200)\n'
        )
        headers = ast["calls"][0]["config"]["headers"]
        assert "X-Token" in headers

    def test_body_json(self):
        ast = _parse(
            'post("$u", {\n'
            '    body: json({ key: "val" })\n'
            '})\n    .expect(status: 200)\n'
        )
        body = ast["calls"][0]["config"]["body"]
        assert body["type"] == "json"

    def test_body_form(self):
        ast = _parse(
            'post("$u", {\n'
            '    body: form({ key: "val" })\n'
            '})\n    .expect(status: 200)\n'
        )
        assert ast["calls"][0]["config"]["body"]["type"] == "form"

    def test_body_raw_string(self):
        ast = _parse(
            'post("$u", {\n'
            '    body: "raw data"\n'
            '})\n    .expect(status: 200)\n'
        )
        assert ast["calls"][0]["config"]["body"]["type"] == "raw"

    def test_timeout(self):
        ast = _parse(
            'get("$u", {\n'
            '    timeout: { ms: 5000, action: "fail" }\n'
            '})\n    .expect(status: 200)\n'
        )
        t = ast["calls"][0]["config"]["timeout"]
        assert t["ms"] == 5000
        assert t["action"] == "fail"

    def test_redirects(self):
        ast = _parse(
            'get("$u", {\n'
            '    redirects: { follow: true, max: 3 }\n'
            '})\n    .expect(status: 200)\n'
        )
        r = ast["calls"][0]["config"]["redirects"]
        assert r["follow"] is True
        assert r["max"] == 3

    def test_security(self):
        ast = _parse(
            'get("$u", {\n'
            '    security: { rejectInvalidCerts: false }\n'
            '})\n    .expect(status: 200)\n'
        )
        s = ast["calls"][0]["config"]["security"]
        assert s["rejectInvalidCerts"] is False

    def test_cookie_jar(self):
        ast = _parse(
            'get("$u", {\n'
            '    cookieJar: "fresh"\n'
            '})\n    .expect(status: 200)\n'
        )
        assert ast["calls"][0]["config"]["cookieJar"] == "fresh"


class TestChainMethods:
    def test_expect(self):
        ast = _parse('get("$u")\n    .expect(status: 200)\n')
        expect = ast["calls"][0]["chain"]["expect"]
        assert "status" in expect

    def test_check(self):
        ast = _parse('get("$u")\n    .check(status: 200)\n')
        assert "check" in ast["calls"][0]["chain"]

    def test_store_run_var(self):
        ast = _parse('get("$u")\n    .expect(status: 200)\n    .store({ $$x: this.status })\n')
        store = ast["calls"][0]["chain"]["store"]
        assert "$$x" in store

    def test_store_script_var(self):
        ast = _parse('get("$u")\n    .expect(status: 200)\n    .store({ $x: this.status })\n')
        store = ast["calls"][0]["chain"]["store"]
        assert "$x" in store

    def test_store_plain_key(self):
        ast = _parse('get("$u")\n    .expect(status: 200)\n    .store({ mykey: this.status })\n')
        store = ast["calls"][0]["chain"]["store"]
        assert "mykey" in store

    def test_assert_expect(self):
        ast = _parse('get("$u")\n    .assert({ expect: [this.status eq 200] })\n')
        block = ast["calls"][0]["chain"]["assert"]
        assert len(block["expect"]) == 1

    def test_assert_check(self):
        ast = _parse('get("$u")\n    .assert({ check: [this.status eq 200] })\n')
        assert len(ast["calls"][0]["chain"]["assert"]["check"]) == 1

    def test_wait(self):
        ast = _parse('get("$u")\n    .expect(status: 200)\n    .wait(1000)\n')
        assert ast["calls"][0]["chain"]["wait"] == 1000


class TestScopeNames:
    @pytest.mark.parametrize("scope", [
        "status", "body", "headers", "bodySize", "totalDelayMs",
        "dns", "connect", "tls", "ttfb", "transfer", "size", "redirects",
    ])
    def test_scope_accepted(self, scope):
        ast = _parse(f'get("$u")\n    .expect({scope}: 200)\n')
        assert scope in ast["calls"][0]["chain"]["expect"]


class TestExpressions:
    def test_int_literal(self):
        ast = _parse('get("$u")\n    .assert({ expect: [this.status eq 200] })\n')
        cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
        assert cond["right"]["value"] == 200

    def test_string_literal(self):
        ast = _parse('get("$u")\n    .assert({ expect: [this.body eq "ok"] })\n')
        cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
        assert cond["right"]["value"] == "ok"

    def test_bool_literal(self):
        ast = _parse('get("$u")\n    .assert({ expect: [this.body.valid eq true] })\n')
        cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
        assert cond["right"]["value"] is True

    def test_null_literal(self):
        ast = _parse('get("$u")\n    .assert({ expect: [this.body.x eq null] })\n')
        cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
        assert cond["right"]["value"] is None

    def test_script_var(self):
        ast = _parse('get("$u")\n    .assert({ expect: [$x eq 1] })\n')
        cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
        assert cond["left"]["kind"] == "scriptVar"
        assert cond["left"]["name"] == "x"

    def test_run_var(self):
        ast = _parse('get("$u")\n    .assert({ expect: [$$x eq 1] })\n')
        cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
        assert cond["left"]["kind"] == "runVar"

    def test_this_ref(self):
        ast = _parse('get("$u")\n    .assert({ expect: [this.status eq 200] })\n')
        cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
        assert cond["left"]["kind"] == "thisRef"
        assert cond["left"]["path"] == ["status"]

    def test_this_nested(self):
        ast = _parse('get("$u")\n    .assert({ expect: [this.body.data.id eq 1] })\n')
        cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
        assert cond["left"]["path"] == ["body", "data", "id"]

    def test_prev_ref(self):
        ast = _parse('get("$u")\n    .assert({ expect: [prev.outcome eq "success"] })\n')
        cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
        assert cond["left"]["kind"] == "prevRef"

    def test_binary_ops(self):
        for op in ("eq", "neq", "lt", "lte", "gt", "gte"):
            ast = _parse(f'get("$u")\n    .assert({{ expect: [this.status {op} 200] }})\n')
            cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
            assert cond["op"] == op

    def test_arithmetic(self):
        for op_sym in ["+", "-", "*", "/", "%"]:
            ast = _parse(f'get("$u")\n    .assert({{ expect: [this.x {op_sym} 1 eq 0] }})\n')
            cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
            assert cond["left"]["kind"] == "binary"
            assert cond["left"]["op"] == op_sym

    def test_logical_and(self):
        ast = _parse('get("$u")\n    .assert({ expect: [this.a eq 1 and this.b eq 2] })\n')
        cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
        assert cond["op"] == "and"

    def test_logical_or(self):
        ast = _parse('get("$u")\n    .assert({ expect: [this.a eq 1 or this.b eq 2] })\n')
        cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
        assert cond["op"] == "or"

    def test_not(self):
        ast = _parse('get("$u")\n    .assert({ expect: [not this.body.disabled] })\n')
        cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
        assert cond["kind"] == "unary"
        assert cond["op"] == "not"

    def test_unary_minus(self):
        ast = _parse('get("$u")\n    .assert({ expect: [-1 eq this.x] })\n')
        cond = ast["calls"][0]["chain"]["assert"]["expect"][0]["condition"]
        assert cond["left"]["kind"] == "unary"
        assert cond["left"]["op"] == "-"

    def test_array_literal(self):
        ast = _parse('get("$u")\n    .expect(status: [200, 201, 202])\n')
        val = ast["calls"][0]["chain"]["expect"]["status"]["value"]
        assert val["kind"] == "arrayLit"
        assert len(val["items"]) == 3

    def test_object_literal_in_store(self):
        ast = _parse('get("$u")\n    .expect(status: 200)\n    .store({ $$data: this.body })\n')
        store = ast["calls"][0]["chain"]["store"]
        assert "$$data" in store

    def test_func_call_schema(self):
        ast = _parse('get("$u")\n    .expect(body: schema($s))\n')
        val = ast["calls"][0]["chain"]["expect"]["body"]["value"]
        assert val["kind"] == "funcCall"
        assert val["name"] == "schema"


class TestComments:
    def test_comment_before_call(self):
        ast = _parse('// a comment\nget("$u")\n    .expect(status: 200)\n')
        assert len(ast["calls"]) == 1

    def test_comment_between_calls(self):
        ast = _parse(
            'get("$a")\n    .expect(status: 200)\n'
            '// gap\n'
            'get("$b")\n    .expect(status: 200)\n'
        )
        assert len(ast["calls"]) == 2


class TestParseErrors:
    def test_no_method(self):
        with pytest.raises(ParseError):
            parse('"https://example.com"\n')

    def test_unclosed_paren(self):
        with pytest.raises(ParseError):
            parse('get("url"\n')

    def test_invalid_keyword_as_method(self):
        with pytest.raises(ParseError):
            parse('headers("url")\n')
