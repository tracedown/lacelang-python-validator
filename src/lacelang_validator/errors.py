"""Canonical Lace validation error codes and helpers.

The code set must match `specs/error-codes.json` in the lacelang repo. Every
validator and executor emit the same code for the same condition so
conformance vectors are implementation-independent.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Diagnostic:
    code: str
    call_index: int | None = None
    chain_method: str | None = None
    field: str | None = None
    line: int | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code}
        if self.call_index is not None:
            out["callIndex"] = self.call_index
        if self.chain_method is not None:
            out["chainMethod"] = self.chain_method
        if self.field is not None:
            out["field"] = self.field
        if self.line is not None:
            out["line"] = self.line
        if self.detail is not None:
            out["detail"] = self.detail
        return out


@dataclass
class DiagnosticSink:
    errors: list[Diagnostic] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)

    def error(self, code: str, **kw: Any) -> None:
        self.errors.append(Diagnostic(code=code, **kw))

    def warning(self, code: str, **kw: Any) -> None:
        self.warnings.append(Diagnostic(code=code, **kw))

    def to_dict(self) -> dict[str, Any]:
        return {
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
        }
