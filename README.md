# lacelang-validator (python)

Canonical Python validator for [Lace](https://github.com/tracedown/lacelang) —
the reference implementation with **100% spec conformance**.

This is the validator that the Lace specification is developed and tested
against. All error codes, AST schemas, and validation rules are verified
against this implementation before each spec release.

This package **only** parses and validates `.lace` source text. It has no
HTTP client and does not execute probes. The runtime and its network
surface live in the separate
[`lacelang-executor`](https://github.com/tracedown/lacelang-python-executor) package, per
`lace-spec.md` §16.

## Why split

Integrators that want syntax / semantic checking in a CI job, IDE
extension, or block editor shouldn't have to pull in an HTTP stack, TLS,
cookie state, or an extension dispatcher. Installing `lacelang-validator`
alone gives a clean audit surface with zero network exposure.

## Install

```bash
pip install lacelang-validator
```

Or from source:

```bash
pip install git+https://github.com/tracedown/lacelang-python-validator.git
```

## Usage

```bash
# Parse → emit AST, or { errors: [...] } on syntax failure
lacelang-validate parse script.lace

# Validate → emit { errors: [...], warnings: [...] }
lacelang-validate validate script.lace \
    --vars-list vars.json \
    --context context.json

# With an extension's field/function registrations active
lacelang-validate validate script.lace \
    --enable-extension laceNotifications

# Pretty-print any subcommand
lacelang-validate parse script.lace --pretty
```

Exit code is always `0` on successful processing. Parse and validation
errors are reported in the JSON body. Non-zero exit codes are reserved
for tool failures (unreadable source file, malformed arguments).

## Library usage

```python
from lacelang_validator.parser import parse
from lacelang_validator.validator import validate

ast = parse(open("script.lace").read())
diagnostics = validate(
    ast,
    variables=["url"],
    context={"maxRedirects": 10, "maxTimeoutMs": 300_000},
)
for err in diagnostics.errors:
    print(err.code, err.call_index, err.field)
```

## License

Apache License 2.0
