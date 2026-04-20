"""Semantic validator for Lace ASTs.

Emits canonical error codes from `specs/error-codes.json` via DiagnosticSink.
Strict by default: the parser is permissive and the validator rejects anything
that violates spec §12. Context (maxRedirects, maxTimeoutMs) gates
system-limit checks; when absent, reasonable spec defaults are used.
"""

import re
from typing import Any

from lacelang_validator.errors import DiagnosticSink

CHAIN_ORDER: tuple[str, ...] = ("expect", "check", "assert", "store", "wait")
CORE_FUNCS: frozenset[str] = frozenset({"json", "form", "schema"})
OP_VALUES: frozenset[str] = frozenset({"lt", "lte", "eq", "neq", "gte", "gt"})
TIMEOUT_ACTIONS: frozenset[str] = frozenset({"fail", "warn", "retry"})

MAX_BODY_RE = re.compile(r"^\d+(k|kb|m|mb|g|gb)?$", re.IGNORECASE)
COOKIE_JAR_FIXED = {"inherit", "fresh", "selective_clear"}
COOKIE_JAR_NAMED_RE = re.compile(r"^named:([A-Za-z0-9_\-]+)$")
COOKIE_JAR_NAMED_SELECTIVE_RE = re.compile(r"^([A-Za-z0-9_\-]+):selective_clear$")


def validate(
    ast: dict[str, Any],
    variables: list[str] | None = None,
    context: dict[str, Any] | None = None,
    prev_results_available: bool = False,
    active_extensions: list[str] | None = None,
) -> DiagnosticSink:
    sink = DiagnosticSink()
    ctx = context or {}
    vars_set = set(variables or [])
    extensions_active = bool(active_extensions)

    calls = ast.get("calls", [])
    if len(calls) == 0:
        sink.error("AT_LEAST_ONE_CALL")
        return sink
    if len(calls) > 10:
        sink.warning("HIGH_CALL_COUNT")

    # Run-var tracking for RUN_VAR_REASSIGNED.
    run_var_assigned: dict[str, int] = {}

    for i, call in enumerate(calls):
        _validate_call(
            call, i, sink, vars_set, ctx, prev_results_available,
            run_var_assigned, extensions_active,
        )

    # Wait has no language-level ceiling (spec §4.8). Platform-level chain
    # length limits are outside the language validator's scope (spec §12).

    return sink


def _validate_call(
    call: dict[str, Any],
    idx: int,
    sink: DiagnosticSink,
    vars_set: set[str],
    ctx: dict[str, Any],
    prev_available: bool,
    run_var_assigned: dict[str, int],
    extensions_active: bool,
) -> None:
    cfg = call.get("config") or {}
    _validate_call_config(cfg, idx, sink, vars_set, ctx, prev_available, extensions_active)

    chain = call.get("chain") or {}
    order = chain.get("__order", [])
    dupes = chain.get("__duplicates", [])

    if dupes:
        sink.error("CHAIN_DUPLICATE", call_index=idx, detail=",".join(dupes))

    # Order check on the de-duplicated observed sequence.
    seen: list[str] = []
    for m in order:
        if m not in seen:
            seen.append(m)
    expected_order = [m for m in CHAIN_ORDER if m in seen]
    if seen != expected_order:
        sink.error("CHAIN_ORDER", call_index=idx)

    if not order:
        sink.error("EMPTY_CHAIN", call_index=idx)
        return

    for method in ("expect", "check"):
        if method in chain:
            _validate_scope_block(chain[method], call, idx, method, sink, vars_set, prev_available)

    if "assert" in chain:
        _validate_assert_block(chain["assert"], call, idx, sink, vars_set, prev_available)

    if "store" in chain:
        _validate_store_block(chain["store"], call, idx, sink, vars_set, prev_available, run_var_assigned)

    if "wait" in chain:
        w = chain["wait"]
        if not isinstance(w, int) or w < 0:
            sink.error("EXPRESSION_SYNTAX", call_index=idx, chain_method="wait")


# ── call config ─────────────────────────────────────────────────────

def _validate_call_config(
    cfg: dict[str, Any],
    idx: int,
    sink: DiagnosticSink,
    vars_set: set[str],
    ctx: dict[str, Any],
    prev_available: bool,
    extensions_active: bool,
) -> None:
    # cookieJar / clearCookies
    jar = cfg.get("cookieJar")
    if jar is not None:
        _validate_cookie_jar(jar, cfg, idx, sink)
    else:
        if cfg.get("clearCookies"):
            sink.error("CLEAR_COOKIES_WRONG_JAR", call_index=idx)

    # redirects
    red = cfg.get("redirects") or {}
    if "max" in red:
        limit = ctx.get("maxRedirects")
        if isinstance(limit, int) and red["max"] > limit:
            sink.error("REDIRECTS_MAX_LIMIT", call_index=idx, field="redirects.max")

    # timeout
    to = cfg.get("timeout") or {}
    if "action" in to and to["action"] not in TIMEOUT_ACTIONS:
        sink.error("TIMEOUT_ACTION_INVALID", call_index=idx, field="timeout.action")
    if "retries" in to and to.get("action") != "retry":
        sink.error("TIMEOUT_RETRIES_REQUIRES_RETRY", call_index=idx)
    if "ms" in to:
        limit = ctx.get("maxTimeoutMs")
        if isinstance(limit, int) and to["ms"] > limit:
            sink.error("TIMEOUT_MS_LIMIT", call_index=idx, field="timeout.ms")

    # Walk expressions in config for variable/function/this checks.
    # `this` is forbidden in call config (not a chain context).
    ctx_info = _ExprCtx(call_index=idx, chain_method=None, allow_this=False,
                       allow_extension_funcs=False)
    _walk_any(cfg.get("headers"), sink, vars_set, ctx_info, prev_available)
    _walk_body(cfg.get("body"), sink, vars_set, ctx_info, prev_available)
    _walk_any(cfg.get("cookies"), sink, vars_set, ctx_info, prev_available)
    # extensions passthrough — still check variable refs, and flag inactive.
    ctx_ext = _ExprCtx(call_index=idx, chain_method=None, allow_this=False,
                      allow_extension_funcs=True)
    for name in (cfg.get("extensions") or {}):
        if not extensions_active:
            sink.warning("EXT_FIELD_INACTIVE", call_index=idx, field=name)
    _walk_any(cfg.get("extensions"), sink, vars_set, ctx_ext, prev_available)
    for sub in ("redirects", "security", "timeout"):
        ext = (cfg.get(sub) or {}).get("extensions")
        if ext:
            if not extensions_active:
                for name in ext:
                    sink.warning("EXT_FIELD_INACTIVE", call_index=idx,
                                 field=f"{sub}.{name}")
            _walk_any(ext, sink, vars_set, ctx_ext, prev_available)


def _validate_cookie_jar(jar: str, cfg: dict[str, Any], idx: int, sink: DiagnosticSink) -> None:
    if jar in COOKIE_JAR_FIXED:
        if cfg.get("clearCookies") and jar != "selective_clear":
            sink.error("CLEAR_COOKIES_WRONG_JAR", call_index=idx)
        return
    if jar.startswith("named:"):
        if jar == "named:":
            sink.error("COOKIE_JAR_NAMED_EMPTY", call_index=idx)
            return
        if not COOKIE_JAR_NAMED_RE.match(jar):
            sink.error("COOKIE_JAR_FORMAT", call_index=idx, field="cookieJar")
            return
        if cfg.get("clearCookies"):
            sink.error("CLEAR_COOKIES_WRONG_JAR", call_index=idx)
        return
    m = COOKIE_JAR_NAMED_SELECTIVE_RE.match(jar)
    if m:
        return
    sink.error("COOKIE_JAR_FORMAT", call_index=idx, field="cookieJar")


# ── scope / assert / store ──────────────────────────────────────────

def _validate_scope_block(
    block: dict[str, Any],
    call: dict[str, Any],
    idx: int,
    method: str,
    sink: DiagnosticSink,
    vars_set: set[str],
    prev_available: bool,
) -> None:
    real_keys = [k for k in block if not k.startswith("__")]
    if not real_keys:
        sink.error("EMPTY_SCOPE_BLOCK", call_index=idx, chain_method=method)
        return

    ctx_info = _ExprCtx(call_index=idx, chain_method=method,
                       allow_this=True, allow_extension_funcs=False)
    ctx_opts = _ExprCtx(call_index=idx, chain_method=method,
                       allow_this=True, allow_extension_funcs=True)

    for field in real_keys:
        sv = block[field]
        if "op" in sv and sv["op"] not in OP_VALUES:
            sink.error("OP_VALUE_INVALID", call_index=idx, chain_method=method, field=field)
        if field == "bodySize":
            val = sv.get("value")
            if isinstance(val, dict) and val.get("kind") == "literal" \
                    and val.get("valueType") == "string":
                if not MAX_BODY_RE.match(str(val["value"])):
                    sink.error("MAX_BODY_FORMAT", call_index=idx, chain_method=method, field=field)
        _walk_any(sv.get("value"), sink, vars_set, ctx_info, prev_available)
        _walk_any(sv.get("options"), sink, vars_set, ctx_opts, prev_available)


def _validate_assert_block(
    block: dict[str, Any],
    call: dict[str, Any],
    idx: int,
    sink: DiagnosticSink,
    vars_set: set[str],
    prev_available: bool,
) -> None:
    # Single emission point for EMPTY_ASSERT_BLOCK: whether the block has no
    # clause keys at all, or the clauses are present but their arrays are
    # empty, we report exactly one diagnostic per empty assert call. Combining
    # both conditions prevents the two historical code paths from
    # double-firing for the same call.
    clauses = [c for c in ("expect", "check") if c in block]
    ctx_info = _ExprCtx(call_index=idx, chain_method="assert",
                       allow_this=True, allow_extension_funcs=False)
    ctx_opts = _ExprCtx(call_index=idx, chain_method="assert",
                       allow_this=True, allow_extension_funcs=True)
    total = 0
    for c in clauses:
        items = block[c] or []
        total += len(items)
        for it in items:
            _walk_any(it.get("condition"), sink, vars_set, ctx_info, prev_available)
            _walk_any(it.get("options"), sink, vars_set, ctx_opts, prev_available)
    if not clauses or total == 0:
        sink.error("EMPTY_ASSERT_BLOCK", call_index=idx, chain_method="assert")
        return


def _validate_store_block(
    block: dict[str, Any],
    call: dict[str, Any],
    idx: int,
    sink: DiagnosticSink,
    vars_set: set[str],
    prev_available: bool,
    run_var_assigned: dict[str, int],
) -> None:
    keys = [k for k in block if not k.startswith("__")]
    if not keys:
        sink.error("EMPTY_STORE_BLOCK", call_index=idx, chain_method="store")
        return
    ctx_info = _ExprCtx(call_index=idx, chain_method="store",
                       allow_this=True, allow_extension_funcs=False)
    for key in keys:
        entry = block[key]
        # RUN_VAR write-once enforcement.
        if entry.get("scope") == "run":
            bare = key[2:] if key.startswith("$$") else key
            if bare in run_var_assigned:
                sink.error("RUN_VAR_REASSIGNED", call_index=idx, chain_method="store")
            else:
                run_var_assigned[bare] = idx
        # Spec §4.6: stored values may be any JSON-serialisable shape,
        # so no syntactic scalar check. Walk the expression tree for var /
        # func / this-scope diagnostics.
        _walk_any(entry.get("value"), sink, vars_set, ctx_info, prev_available)


# ── expression walking ─────────────────────────────────────────────

class _ExprCtx:
    __slots__ = ("call_index", "chain_method", "allow_this", "allow_extension_funcs")

    def __init__(self, call_index: int, chain_method: str | None,
                 allow_this: bool, allow_extension_funcs: bool):
        self.call_index = call_index
        self.chain_method = chain_method
        self.allow_this = allow_this
        self.allow_extension_funcs = allow_extension_funcs


def _walk_body(
    body: Any,
    sink: DiagnosticSink,
    vars_set: set[str],
    ctx: _ExprCtx,
    prev_available: bool,
) -> None:
    if not isinstance(body, dict):
        return
    if body.get("type") in ("json", "form"):
        _walk_any(body.get("value"), sink, vars_set, ctx, prev_available)


def _walk_any(
    node: Any,
    sink: DiagnosticSink,
    vars_set: set[str],
    ctx: _ExprCtx,
    prev_available: bool,
) -> None:
    if node is None:
        return
    if isinstance(node, list):
        for item in node:
            _walk_any(item, sink, vars_set, ctx, prev_available)
        return
    if isinstance(node, dict):
        kind = node.get("kind")
        if kind is None:
            # container/map — recurse into all values
            for v in node.values():
                _walk_any(v, sink, vars_set, ctx, prev_available)
            return
        _walk_expr(node, sink, vars_set, ctx, prev_available)


def _walk_expr(
    expr: dict[str, Any],
    sink: DiagnosticSink,
    vars_set: set[str],
    ctx: _ExprCtx,
    prev_available: bool,
) -> None:
    kind = expr.get("kind")
    if kind == "binary":
        _walk_expr(expr["left"], sink, vars_set, ctx, prev_available)
        _walk_expr(expr["right"], sink, vars_set, ctx, prev_available)
    elif kind == "unary":
        _walk_expr(expr["operand"], sink, vars_set, ctx, prev_available)
    elif kind == "thisRef":
        if not ctx.allow_this:
            sink.error("THIS_OUT_OF_SCOPE", call_index=ctx.call_index, chain_method=ctx.chain_method)
    elif kind == "prevRef":
        if not prev_available:
            sink.warning("PREV_WITHOUT_RESULTS", call_index=ctx.call_index, chain_method=ctx.chain_method)
    elif kind == "funcCall":
        name = expr.get("name")
        args = expr.get("args", [])
        if name in CORE_FUNCS:
            _check_core_func_args(name, args, sink, ctx, vars_set)
        elif ctx.allow_extension_funcs:
            pass  # extension contexts (call config.extensions, options) accept anything
        else:
            sink.error("UNKNOWN_FUNCTION", call_index=ctx.call_index, chain_method=ctx.chain_method, field=name)
        for a in args:
            _walk_any(a, sink, vars_set, ctx, prev_available)
    elif kind == "scriptVar":
        name = expr.get("name", "")
        # Design intent (keep): VARIABLE_UNKNOWN is only emitted when the
        # caller provides an explicit variables registry (non-empty
        # `vars_set`). When no registry is supplied, the runtime resolves a
        # missing `$var` to null (spec §5.4), so statically flagging the
        # reference would be a false positive in every integration that
        # doesn't ship a var manifest. Spec §12 "validates all references"
        # is read as "validates against the registry, if one is provided" —
        # the validator has no other source of truth for declared vars.
        if vars_set and name not in vars_set:
            sink.error("VARIABLE_UNKNOWN", call_index=ctx.call_index,
                       chain_method=ctx.chain_method, field=name)
    elif kind in ("runVar", "literal"):
        return
    elif kind == "objectLit":
        for e in expr.get("entries", []):
            _walk_any(e.get("value"), sink, vars_set, ctx, prev_available)
    elif kind == "arrayLit":
        for it in expr.get("items", []):
            _walk_any(it, sink, vars_set, ctx, prev_available)


def _check_core_func_args(
    name: str,
    args: list[Any],
    sink: DiagnosticSink,
    ctx: _ExprCtx,
    vars_set: set[str],
) -> None:
    if name in ("json", "form"):
        if len(args) != 1 or not isinstance(args[0], dict) or args[0].get("kind") != "objectLit":
            sink.error("FUNC_ARG_TYPE", call_index=ctx.call_index,
                       chain_method=ctx.chain_method, field=name)
    elif name == "schema":
        if len(args) != 1 or not isinstance(args[0], dict) or args[0].get("kind") != "scriptVar":
            sink.error("FUNC_ARG_TYPE", call_index=ctx.call_index,
                       chain_method=ctx.chain_method, field=name)
        elif vars_set and args[0].get("name") not in vars_set:
            sink.error("SCHEMA_VAR_UNKNOWN", call_index=ctx.call_index,
                       chain_method=ctx.chain_method, field=args[0].get("name"))
