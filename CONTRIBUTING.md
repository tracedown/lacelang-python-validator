# Contributing to lacelang-validator

The reference Python validator for [Lace](https://github.com/tracedown/lacelang)
is an open-source project under the Apache License 2.0. Contributions are
welcome — bug fixes, new features, test coverage, and documentation.

## Relationship to the spec

This validator implements the parsing and semantic validation rules
defined in the [lacelang](https://github.com/tracedown/lacelang)
specification. The spec is the source of truth. If the validator's
behaviour diverges from the spec, that is a bug in the validator.

Changes that require spec modifications (new syntax, new error codes,
changed validation rules) must be proposed upstream in the spec repo
first.

## Conventions

- **Naming**: camelCase for wire-format fields (matching the spec),
  snake_case for internal Python identifiers (PEP 8).
- **Error codes**: must match `specs/error-codes.json` in the spec
  repo. Do not invent new codes without a corresponding spec change.
- **No purely stylistic changes**: refactoring whitespace, rewording
  comments, or reformatting code without a functional reason will not
  be accepted.
- **Tests required**: every behaviour change must include tests.

## How to contribute

1. **Open an issue** if you want feedback before implementing. This is
   optional — you can also go straight to a PR.
2. **Open a PR** with the proposed changes. Include:
   - The code change
   - Tests in `tests/`
   - Updated README if the public API changes
3. Run the test suite before submitting:
   ```bash
   pytest
   ```
4. Discussion happens in PR comments. A maintainer must approve before
   merge.

## Version

This package tracks the spec version via `__ast_version__` in
`__init__.py`. The package version (`__version__`) is independent and
follows its own release cadence. Only bump `__ast_version__` when
updating to conform to a new spec version.

## Review process

All contributions require approval from a project maintainer before
merge. Discussion happens in PR comments.

## License

By contributing, you agree that your contributions will be licensed
under the Apache License 2.0, the same license as the project.
