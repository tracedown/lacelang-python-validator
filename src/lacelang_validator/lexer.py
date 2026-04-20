"""Tokenizer matching lacelang.g4 lexer rules.

Longest-match with lookahead order:
    RUN_VAR > SCRIPT_VAR > keyword > IDENT > numbers > punctuation
"""

from dataclasses import dataclass
from typing import Literal

TokenType = Literal[
    # literal / identifier classes
    "STRING", "INT", "FLOAT", "BOOL", "IDENT",
    "RUN_VAR", "SCRIPT_VAR",
    # keywords — single kind tag; the lexeme carries the specific word
    "KEYWORD",
    # punctuation
    "LPAREN", "RPAREN", "LBRACE", "RBRACE", "LBRACK", "RBRACK",
    "COMMA", "COLON", "DOT", "SEMI",
    # arithmetic operators (logical/comparison use keyword tokens)
    "PLUS", "MINUS", "STAR", "SLASH", "PERCENT",
    # end
    "EOF",
]

KEYWORDS: frozenset[str] = frozenset({
    "get", "post", "put", "patch", "delete",
    "expect", "check", "assert", "store", "wait",
    "headers", "body", "cookies", "cookieJar", "clearCookies",
    "redirects", "security", "timeout",
    "follow", "max",
    "rejectInvalidCerts",
    "ms", "action", "retries",
    "status", "bodySize", "totalDelayMs", "dns", "connect", "tls",
    "ttfb", "transfer", "size",
    "value", "op", "match", "mode", "options", "condition",
    "json", "form", "schema",
    "this", "prev", "null",
    # comparison operator keywords
    "eq", "neq", "lt", "lte", "gt", "gte",
    # logical connective keywords
    "and", "or", "not",
})

BOOLS: frozenset[str] = frozenset({"true", "false"})


@dataclass
class Token:
    type: TokenType
    value: str
    line: int
    col: int

    def __repr__(self) -> str:
        return f"Token({self.type}, {self.value!r}, line={self.line})"


class LexError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(f"{message} at line {line}, col {col}")
        self.message = message
        self.line = line
        self.col = col


class Lexer:
    def __init__(self, source: str):
        self.src = source
        self.pos = 0
        self.line = 1
        self.col = 1

    def _peek(self, offset: int = 0) -> str:
        p = self.pos + offset
        return self.src[p] if p < len(self.src) else ""

    def _advance(self, n: int = 1) -> str:
        chunk = self.src[self.pos : self.pos + n]
        for ch in chunk:
            if ch == "\n":
                self.line += 1
                self.col = 1
            else:
                self.col += 1
        self.pos += n
        return chunk

    def _skip_trivia(self) -> None:
        while self.pos < len(self.src):
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
            elif ch == "/" and self._peek(1) == "/":
                while self.pos < len(self.src) and self._peek() != "\n":
                    self._advance()
            else:
                break

    def _read_string(self) -> Token:
        start_line, start_col = self.line, self.col
        self._advance()  # opening quote
        chars: list[str] = []
        while self.pos < len(self.src):
            ch = self._peek()
            if ch == '"':
                self._advance()
                return Token("STRING", "".join(chars), start_line, start_col)
            if ch == "\\":
                nxt = self._peek(1)
                if nxt in ('\\', '"', "n", "r", "t", "$"):
                    self._advance(2)
                    chars.append({"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t", "$": "$"}[nxt])
                    continue
                raise LexError(f"invalid escape \\{nxt}", self.line, self.col)
            if ch == "\n":
                raise LexError("unterminated string literal", start_line, start_col)
            chars.append(ch)
            self._advance()
        raise LexError("unterminated string literal", start_line, start_col)

    def _read_number(self) -> Token:
        start_line, start_col = self.line, self.col
        start_pos = self.pos
        while self.pos < len(self.src) and self._peek().isdigit():
            self._advance()
        if self._peek() == "." and self._peek(1).isdigit():
            self._advance()
            while self.pos < len(self.src) and self._peek().isdigit():
                self._advance()
            return Token("FLOAT", self.src[start_pos : self.pos], start_line, start_col)
        return Token("INT", self.src[start_pos : self.pos], start_line, start_col)

    def _read_ident_like(self) -> Token:
        start_line, start_col = self.line, self.col
        start_pos = self.pos
        self._advance()  # first [a-zA-Z_]
        while self.pos < len(self.src):
            ch = self._peek()
            if ch.isalnum() or ch == "_":
                self._advance()
            else:
                break
        lex = self.src[start_pos : self.pos]
        if lex in BOOLS:
            return Token("BOOL", lex, start_line, start_col)
        if lex in KEYWORDS:
            return Token("KEYWORD", lex, start_line, start_col)
        return Token("IDENT", lex, start_line, start_col)

    def _read_dollar(self) -> Token:
        """Lex $$var / $var. Leading '$' already confirmed."""
        start_line, start_col = self.line, self.col
        start_pos = self.pos

        if self._peek(1) == "$":
            self._advance(2)  # $$
            if not (self._peek().isalpha() or self._peek() == "_"):
                raise LexError("expected identifier after $$", self.line, self.col)
            while self.pos < len(self.src) and (self._peek().isalnum() or self._peek() == "_"):
                self._advance()
            return Token("RUN_VAR", self.src[start_pos : self.pos], start_line, start_col)

        self._advance()  # $
        if not (self._peek().isalpha() or self._peek() == "_"):
            raise LexError("expected identifier after $", self.line, self.col)
        while self.pos < len(self.src) and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        return Token("SCRIPT_VAR", self.src[start_pos : self.pos], start_line, start_col)

    def _read_punct(self) -> Token:
        start_line, start_col = self.line, self.col
        ch = self._peek()
        two = self.src[self.pos : self.pos + 2]

        # All comparison + logical operators are keyword-only.
        # `eq`/`neq`/`lt`/`lte`/`gt`/`gte`/`and`/`or`/`not` are lexed as
        # KEYWORD tokens and handled in the parser.

        single = {
            "(": "LPAREN", ")": "RPAREN",
            "{": "LBRACE", "}": "RBRACE",
            "[": "LBRACK", "]": "RBRACK",
            ",": "COMMA", ":": "COLON", ".": "DOT", ";": "SEMI",
            "+": "PLUS", "-": "MINUS",
            "*": "STAR", "/": "SLASH", "%": "PERCENT",
        }
        if ch in single:
            self._advance()
            return Token(single[ch], ch, start_line, start_col)  # type: ignore[arg-type]
        raise LexError(f"unexpected character {ch!r}", start_line, start_col)

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while True:
            self._skip_trivia()
            if self.pos >= len(self.src):
                tokens.append(Token("EOF", "", self.line, self.col))
                return tokens
            ch = self._peek()
            if ch == '"':
                tokens.append(self._read_string())
            elif ch == "$":
                tokens.append(self._read_dollar())
            elif ch.isdigit():
                tokens.append(self._read_number())
            elif ch.isalpha() or ch == "_":
                tokens.append(self._read_ident_like())
            else:
                tokens.append(self._read_punct())


def tokenize(source: str) -> list[Token]:
    return Lexer(source).tokenize()
