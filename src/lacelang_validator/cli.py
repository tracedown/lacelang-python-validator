"""CLI for lacelang-validator — parse + validate only. Execution lives in
the separate executor package (see lace-spec.md §16).

Subcommands:
  parse <script>                                    → { "ast": ... } | { "errors": [...] }
  validate <script> [--vars-list P] [--context P]   → { "errors": [...], "warnings": [...] }

Exit codes:
  0 on processed request (parse/validate errors are in the JSON body)
  2 on tool/arg errors
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lacelang_validator import __version__
from lacelang_validator.errors import Diagnostic
from lacelang_validator.parser import ParseError, parse
from lacelang_validator.validator import validate


def strip_ast_metadata(node: Any) -> Any:
    """Remove internal `__order` / `__duplicates` markers before emitting."""
    if isinstance(node, list):
        return [strip_ast_metadata(n) for n in node]
    if isinstance(node, dict):
        return {k: strip_ast_metadata(v) for k, v in node.items() if not k.startswith("__")}
    return node


def read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def emit(obj: Any, pretty: bool = False) -> None:
    if pretty:
        json.dump(obj, sys.stdout, ensure_ascii=False, indent=2, sort_keys=False)
    else:
        json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


def cmd_parse(args: argparse.Namespace) -> int:
    try:
        source = read_text(args.script)
    except OSError as e:
        print(f"error reading script: {e}", file=sys.stderr)
        return 2
    try:
        ast = parse(source)
    except ParseError as e:
        emit({"errors": [Diagnostic(code="PARSE_ERROR", line=e.line).to_dict()]}, args.pretty)
        return 0
    emit({"ast": strip_ast_metadata(ast)}, args.pretty)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        source = read_text(args.script)
    except OSError as e:
        print(f"error reading script: {e}", file=sys.stderr)
        return 2

    variables: list[str] | None = None
    context: dict[str, Any] | None = None
    try:
        if args.vars_list:
            variables = list(read_json(args.vars_list))
        if args.context:
            context = dict(read_json(args.context))
    except (OSError, json.JSONDecodeError) as e:
        print(f"error reading aux input: {e}", file=sys.stderr)
        return 2

    try:
        ast = parse(source)
    except ParseError as e:
        emit({"errors": [Diagnostic(code="PARSE_ERROR", line=e.line).to_dict()], "warnings": []},
             args.pretty)
        return 0

    active_extensions: list[str] | None = list(args.enable_extensions or [])
    if isinstance(context, dict) and isinstance(context.get("extensions"), list):
        for name in context["extensions"]:
            if name not in active_extensions:
                active_extensions.append(name)
    if not active_extensions:
        active_extensions = None
    sink = validate(ast, variables=variables, context=context,
                    prev_results_available=False,
                    active_extensions=active_extensions)
    emit(sink.to_dict(), args.pretty)
    return 0


def add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--pretty", action="store_true",
                        help="Emit indented JSON instead of a single line.")
    parser.add_argument("--enable-extension", dest="enable_extensions",
                        action="append", default=[],
                        metavar="NAME",
                        help="Activate a Lace extension (may be repeated). "
                             "Built-in: laceNotifications.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lacelang-validate",
        description="Lace language validator (parser + semantic checks).",
    )
    p.add_argument("--version", action="version", version=f"lacelang-validator {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    add_common_flags(common)

    sub = p.add_subparsers(dest="command", required=True)

    pp = sub.add_parser("parse", parents=[common],
                        help="Parse a script; emit AST or parse errors.")
    pp.add_argument("script")
    pp.set_defaults(func=cmd_parse)

    pv = sub.add_parser("validate", parents=[common],
                        help="Validate a script; emit errors/warnings.")
    pv.add_argument("script")
    pv.add_argument("--vars-list", dest="vars_list", help="JSON array of declared variable names.")
    pv.add_argument("--context", help="JSON object with validator context.")
    pv.set_defaults(func=cmd_validate)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
