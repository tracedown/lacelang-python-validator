"""Lexer tests — tokenization of Lace source text."""

import pytest
from lacelang_validator.lexer import tokenize, LexError


def tokens(src):
    return [(t.type, t.value) for t in tokenize(src) if t.type != "EOF"]


class TestBasicTokens:
    def test_string(self):
        assert tokens('"hello"') == [("STRING", "hello")]

    def test_int(self):
        assert tokens("42") == [("INT", "42")]

    def test_float(self):
        assert tokens("3.14") == [("FLOAT", "3.14")]

    def test_bool(self):
        assert tokens("true false") == [("BOOL", "true"), ("BOOL", "false")]

    def test_ident(self):
        assert tokens("myVar") == [("IDENT", "myVar")]

    def test_keyword(self):
        assert tokens("get") == [("KEYWORD", "get")]

    def test_run_var(self):
        toks = tokens("$$token")
        assert toks[0][0] == "RUN_VAR"

    def test_script_var(self):
        toks = tokens("$host")
        assert toks[0][0] == "SCRIPT_VAR"


class TestEscapeSequences:
    def test_newline(self):
        assert tokens(r'"line\n"') == [("STRING", "line\n")]

    def test_tab(self):
        assert tokens(r'"col\t"') == [("STRING", "col\t")]

    def test_backslash(self):
        assert tokens(r'"path\\"') == [("STRING", "path\\")]

    def test_quote(self):
        assert tokens(r'"say\"hi\""') == [("STRING", 'say"hi"')]

    def test_dollar(self):
        """\\$ produces literal $ in the string value."""
        assert tokens(r'"price\$100"') == [("STRING", "price$100")]

    def test_carriage_return(self):
        assert tokens(r'"cr\r"') == [("STRING", "cr\r")]

    def test_invalid_escape(self):
        with pytest.raises(LexError, match="invalid escape"):
            tokenize(r'"\z"')


class TestPunctuation:
    def test_all_punctuation(self):
        result = tokens("(){}[],:.")
        types = [t for t, _ in result]
        assert types == ["LPAREN", "RPAREN", "LBRACE", "RBRACE",
                         "LBRACK", "RBRACK", "COMMA", "COLON", "DOT"]

    def test_arithmetic(self):
        result = tokens("+ - * / %")
        types = [t for t, _ in result]
        assert types == ["PLUS", "MINUS", "STAR", "SLASH", "PERCENT"]


class TestComments:
    def test_line_comment_skipped(self):
        result = tokens('// this is a comment\nget')
        assert result == [("KEYWORD", "get")]

    def test_inline_comment(self):
        result = tokens('get // comment\n"url"')
        assert result == [("KEYWORD", "get"), ("STRING", "url")]


class TestEdgeCases:
    def test_empty_string(self):
        assert tokens('""') == [("STRING", "")]

    def test_unterminated_string(self):
        with pytest.raises(LexError, match="unterminated"):
            tokenize('"hello')

    def test_whitespace_ignored(self):
        assert tokens("   get   ") == [("KEYWORD", "get")]

    def test_unknown_char(self):
        with pytest.raises(LexError, match="unexpected character"):
            tokenize("#")
