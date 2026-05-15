"""Validator tests — semantic checks and error code emission."""

import pytest
from lacelang_validator.parser import parse
from lacelang_validator.validator import validate


def _validate(source, **kwargs):
    return validate(parse(source), **kwargs)


def error_codes(source, **kwargs):
    return [d.code for d in _validate(source, **kwargs).errors]


def warning_codes(source, **kwargs):
    return [d.code for d in _validate(source, **kwargs).warnings]


# ── Structural rules ────────────────────────────────────────────

class TestStructuralRules:
    def test_at_least_one_call(self):
        """AT_LEAST_ONE_CALL fires when AST has empty calls (safety net)."""
        from lacelang_validator.validator import validate
        sink = validate({"version": "0.9.2", "calls": []})
        assert "AT_LEAST_ONE_CALL" in [e.code for e in sink.errors]

    def test_empty_script_parse_error(self):
        from lacelang_validator.parser import ParseError, parse
        import pytest
        with pytest.raises(ParseError):
            parse("")

    def test_empty_chain(self):
        """EMPTY_CHAIN fires when AST call has no chain methods (safety net)."""
        from lacelang_validator.validator import validate
        ast = {"version": "0.9.2", "calls": [{"method": "get", "url": "$u", "chain": {}}]}
        sink = validate(ast)
        assert "EMPTY_CHAIN" in [e.code for e in sink.errors]

    def test_valid_chain_no_error(self):
        assert error_codes('get("$u")\n    .expect(status: 200)\n') == []


class TestChainOrder:
    def test_store_before_assert(self):
        src = 'get("$u")\n    .store({ a: this.body.x })\n    .assert({ expect: [this.status eq 200] })\n'
        assert "CHAIN_ORDER" in error_codes(src)

    def test_correct_order(self):
        src = 'get("$u")\n    .expect(status: 200)\n    .store({ a: this.body.x })\n'
        assert "CHAIN_ORDER" not in error_codes(src)


class TestChainDuplicate:
    def test_duplicate_expect(self):
        src = 'get("$u")\n    .expect(status: 200)\n    .expect(body: "ok")\n'
        assert "CHAIN_DUPLICATE" in error_codes(src)

    def test_no_duplicate(self):
        src = 'get("$u")\n    .expect(status: 200)\n    .check(body: "ok")\n'
        assert "CHAIN_DUPLICATE" not in error_codes(src)


class TestEmptyBlocks:
    def test_empty_scope_block(self):
        src = 'get("$u")\n    .expect()\n'
        assert "EMPTY_SCOPE_BLOCK" in error_codes(src)

    def test_empty_assert_block(self):
        src = 'get("$u")\n    .assert({})\n'
        assert "EMPTY_ASSERT_BLOCK" in error_codes(src)

    def test_empty_store_block(self):
        src = 'get("$u")\n    .store({})\n'
        assert "EMPTY_STORE_BLOCK" in error_codes(src)


# ── Variable checks ─────────────────────────────────────────────

class TestVariableChecks:
    def test_unknown_variable_with_registry(self):
        src = 'get("$u")\n    .assert({ expect: [$unknown eq 1] })\n'
        assert "VARIABLE_UNKNOWN" in error_codes(src, variables=["u"])

    def test_known_variable(self):
        src = 'get("$u")\n    .assert({ expect: [$host eq 1] })\n'
        assert "VARIABLE_UNKNOWN" not in error_codes(src, variables=["u", "host"])

    def test_no_registry_no_error(self):
        """Without a variable registry, unknown vars are not flagged."""
        src = 'get("$u")\n    .assert({ expect: [$anything eq 1] })\n'
        assert "VARIABLE_UNKNOWN" not in error_codes(src)

    def test_run_var_reassigned(self):
        src = (
            'get("$u")\n    .expect(status: 200)\n    .store({ $$x: this.status })\n'
            'get("$u")\n    .expect(status: 200)\n    .store({ $$x: this.status })\n'
        )
        assert "RUN_VAR_REASSIGNED" in error_codes(src)

    def test_run_var_single_assignment(self):
        src = 'get("$u")\n    .expect(status: 200)\n    .store({ $$x: this.status })\n'
        assert "RUN_VAR_REASSIGNED" not in error_codes(src)


# ── Expression checks ───────────────────────────────────────────

class TestExpressionChecks:
    def test_unknown_function(self):
        src = 'get("$u")\n    .assert({ expect: [random() gt 5] })\n'
        assert "UNKNOWN_FUNCTION" in error_codes(src)

    def test_known_function_json(self):
        src = 'post("$u", { body: json({ a: 1 }) })\n    .expect(status: 200)\n'
        assert "UNKNOWN_FUNCTION" not in error_codes(src)

    def test_schema_var_unknown(self):
        src = 'get("$u")\n    .expect(body: schema($missing))\n'
        assert "SCHEMA_VAR_UNKNOWN" in error_codes(src, variables=["u"])

    def test_schema_var_known(self):
        src = 'get("$u")\n    .expect(body: schema($s))\n'
        assert "SCHEMA_VAR_UNKNOWN" not in error_codes(src, variables=["u", "s"])

    def test_wait_valid(self):
        src = 'get("$u")\n    .expect(status: 200)\n    .wait(1000)\n'
        assert "EXPRESSION_SYNTAX" not in error_codes(src)


# ── Config limit checks ─────────────────────────────────────────

class TestConfigLimits:
    def test_redirects_max_limit(self):
        src = 'get("$u", { redirects: { max: 999 } })\n    .expect(status: 200)\n'
        assert "REDIRECTS_MAX_LIMIT" in error_codes(
            src, context={"maxRedirects": 10})

    def test_redirects_within_limit(self):
        src = 'get("$u", { redirects: { max: 5 } })\n    .expect(status: 200)\n'
        assert "REDIRECTS_MAX_LIMIT" not in error_codes(
            src, context={"maxRedirects": 10})

    def test_timeout_ms_limit(self):
        src = 'get("$u", { timeout: { ms: 999999 } })\n    .expect(status: 200)\n'
        assert "TIMEOUT_MS_LIMIT" in error_codes(
            src, context={"maxTimeoutMs": 300000})

    def test_timeout_action_invalid(self):
        src = 'get("$u", { timeout: { ms: 5000, action: "explode" } })\n    .expect(status: 200)\n'
        assert "TIMEOUT_ACTION_INVALID" in error_codes(src)

    def test_timeout_retries_requires_retry(self):
        src = 'get("$u", { timeout: { ms: 5000, action: "fail", retries: 3 } })\n    .expect(status: 200)\n'
        assert "TIMEOUT_RETRIES_REQUIRES_RETRY" in error_codes(src)

    def test_timeout_retries_with_retry_ok(self):
        src = 'get("$u", { timeout: { ms: 5000, action: "retry", retries: 3 } })\n    .expect(status: 200)\n'
        assert "TIMEOUT_RETRIES_REQUIRES_RETRY" not in error_codes(src)


# ── Cookie jar checks ───────────────────────────────────────────

class TestCookieJar:
    def test_clear_cookies_wrong_jar(self):
        src = 'get("$u", { cookieJar: "inherit", clearCookies: ["a"] })\n    .expect(status: 200)\n'
        assert "CLEAR_COOKIES_WRONG_JAR" in error_codes(src)

    def test_clear_cookies_selective_ok(self):
        src = 'get("$u", { cookieJar: "selective_clear", clearCookies: ["a"] })\n    .expect(status: 200)\n'
        assert "CLEAR_COOKIES_WRONG_JAR" not in error_codes(src)

    def test_named_empty(self):
        src = 'get("$u", { cookieJar: "named:" })\n    .expect(status: 200)\n'
        assert "COOKIE_JAR_NAMED_EMPTY" in error_codes(src)

    def test_jar_format_invalid(self):
        src = 'get("$u", { cookieJar: "invalid_mode" })\n    .expect(status: 200)\n'
        assert "COOKIE_JAR_FORMAT" in error_codes(src)

    def test_jar_format_named_ok(self):
        src = 'get("$u", { cookieJar: "named:session" })\n    .expect(status: 200)\n'
        assert "COOKIE_JAR_FORMAT" not in error_codes(src)


# ── Scope checks ────────────────────────────────────────────────

class TestScopeChecks:
    def test_op_value_invalid(self):
        src = 'get("$u")\n    .expect(status: { value: 200, op: "nope" })\n'
        assert "OP_VALUE_INVALID" in error_codes(src)

    def test_op_value_valid(self):
        for op in ("lt", "lte", "eq", "neq", "gte", "gt"):
            src = f'get("$u")\n    .expect(status: {{ value: 200, op: "{op}" }})\n'
            assert "OP_VALUE_INVALID" not in error_codes(src)

    def test_body_size_format_invalid(self):
        src = 'get("$u")\n    .expect(bodySize: "invalid")\n'
        assert "MAX_BODY_FORMAT" in error_codes(src)

    def test_body_size_format_valid(self):
        for s in ("500", "10k", "2kb", "1mb", "5GB"):
            src = f'get("$u")\n    .expect(bodySize: "{s}")\n'
            assert "MAX_BODY_FORMAT" not in error_codes(src), f"failed for {s}"


# ── Warnings ────────────────────────────────────────────────────

class TestWarnings:
    def test_prev_without_results(self):
        src = 'get("$u")\n    .assert({ expect: [prev.outcome eq "success"] })\n'
        assert "PREV_WITHOUT_RESULTS" in warning_codes(src)

    def test_prev_with_results(self):
        src = 'get("$u")\n    .assert({ expect: [prev.outcome eq "success"] })\n'
        assert "PREV_WITHOUT_RESULTS" not in warning_codes(
            src, prev_results_available=True)

    def test_high_call_count(self):
        calls = "\n".join(
            f'get("$u")\n    .expect(status: 200)' for _ in range(11))
        assert "HIGH_CALL_COUNT" in warning_codes(calls)

    def test_normal_call_count(self):
        calls = "\n".join(
            f'get("$u")\n    .expect(status: 200)' for _ in range(5))
        assert "HIGH_CALL_COUNT" not in warning_codes(calls)

    def test_ext_field_inactive(self):
        src = 'get("$u", { timeout: { ms: 5000 }, myExtField: 42 })\n    .expect(status: 200)\n'
        assert "EXT_FIELD_INACTIVE" in warning_codes(src)

    def test_ext_field_active(self):
        src = 'get("$u", { timeout: { ms: 5000 }, myExtField: 42 })\n    .expect(status: 200)\n'
        assert "EXT_FIELD_INACTIVE" not in warning_codes(
            src, active_extensions=["someExt"])
