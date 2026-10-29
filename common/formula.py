"""
formula.py — Excel-style arithmetic for number cells: a text that starts with "=" is a formula.

Only numbers, + - * /, brackets and a unary minus (also × ÷ − and a decimal comma). It is parsed by a
small recursive-descent parser, never evaluated as code. `evaluate("=25/12")` returns the number or
raises FormulaError with a reason that can be shown to the user.
"""

from __future__ import annotations

import math
import re

__all__ = ["FormulaError", "is_formula", "evaluate", "format_number"]

_TOKEN = re.compile(r"\s*(?:(\d+(?:[.,]\d+)?|[.,]\d+)|(.))")
_OPS = {"×": "*", "÷": "/", "−": "-", "–": "-", "x": "*", "X": "*"}


class FormulaError(ValueError):
    """The formula cannot be worked out; the message says why."""


def is_formula(text) -> bool:
    return isinstance(text, str) and text.strip().startswith("=")


def _tokens(src: str) -> list:
    out, pos = [], 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            break
        pos = m.end()
        if m.group(1) is not None:
            out.append(float(m.group(1).replace(",", ".")))
        elif m.group(2) is not None:
            ch = _OPS.get(m.group(2), m.group(2))
            if ch not in "+-*/()":
                raise FormulaError(f"“{m.group(2)}” is not allowed — only numbers, + − × ÷ and brackets")
            out.append(ch)
    if pos < len(src) and src[pos:].strip():
        raise FormulaError("unexpected text at the end")
    return out


class _Parser:
    def __init__(self, tokens: list):
        self.t, self.i = tokens, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def expr(self) -> float:
        v = self.term()
        while self.peek() in ("+", "-"):
            v = v + self.term() if self.take() == "+" else v - self.term()
        return v

    def term(self) -> float:
        v = self.factor()
        while self.peek() in ("*", "/"):
            if self.take() == "*":
                v *= self.factor()
            else:
                d = self.factor()
                if d == 0:
                    raise FormulaError("division by zero")
                v /= d
        return v

    def factor(self) -> float:
        tok = self.take()
        if tok == "-":
            return -self.factor()
        if tok == "+":
            return self.factor()
        if tok == "(":
            v = self.expr()
            if self.take() != ")":
                raise FormulaError("a bracket is not closed")
            return v
        if isinstance(tok, float):
            return tok
        raise FormulaError("a number is missing" if tok is None or tok in ")*/" else f"unexpected “{tok}”")


def evaluate(text: str) -> float:
    """The value of a formula such as "=25/12" (the leading "=" is optional)."""
    src = text.strip()
    if src.startswith("="):
        src = src[1:]
    if not src.strip():
        raise FormulaError("the formula is empty")
    p = _Parser(_tokens(src))
    v = p.expr()
    if p.peek() is not None:
        raise FormulaError("unexpected text after the expression")
    if not math.isfinite(v):
        raise FormulaError("the result is not a finite number")
    return v


def format_number(v: float) -> str:
    """A result as cell text: whole numbers without a trailing .0 problem for the table code
    (which uses float()), otherwise at most 9 decimals — never rounded to the screen's 2."""
    return repr(round(v, 9)) if v != int(v) else str(int(v))
