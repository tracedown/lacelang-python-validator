"""Shared fixtures for the validator test suite."""

from lacelang_validator.parser import parse
from lacelang_validator.validator import validate


def parse_and_validate(source: str, variables=None, context=None,
                       prev_results_available=False, active_extensions=None):
    """Parse source, then validate and return the sink."""
    ast = parse(source)
    return validate(ast, variables=variables, context=context,
                    prev_results_available=prev_results_available,
                    active_extensions=active_extensions)


def error_codes(source: str, **kwargs):
    """Return the list of error codes from validating source."""
    sink = parse_and_validate(source, **kwargs)
    return [d.code for d in sink.errors]


def warning_codes(source: str, **kwargs):
    """Return the list of warning codes from validating source."""
    sink = parse_and_validate(source, **kwargs)
    return [d.code for d in sink.warnings]
