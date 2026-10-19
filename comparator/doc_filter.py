"""
doc_filter.py — boolean expressions over MR/CTR source documents and sheets.

Used by the MR vs CTR Comparator's "Filter documents" box to show only the
rows that came from the chosen documents.

Syntax
------
    MR:<document>            rows whose MR side came from that document
    CTR:<document>           same for the CTR side
    MR:<document>><sheet>    ... and from that sheet of it (">" is just the symbol)

    and / or / not           (case-insensitive; "not" binds tightest, then
                             "and", then "or")
    ( ... )                  grouping, nestable

Names match case-insensitively on *part* of the name. A name that contains
( ) > " or the words and/or/not must be quoted: MR:"Rock and Roll (1).xlsx".
Text without MR:/CTR: matches either side.

    (MR:Jan>CH-01 or MR:Feb) and not CTR:draft

Public API: parse, evaluate, suggest, fragment_start, format_term,
FilterSyntaxError.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class FilterSyntaxError(ValueError):
    """The expression can't be parsed; the message is shown to the user."""


@dataclass(frozen=True)
class Term:
    side: str | None      # "MR", "CTR", or None = either side
    doc: str              # substring of the document name ("" = any)
    sheet: str | None     # substring of the sheet name (None = any)

    def _side_matches(self, part: tuple[str, str] | None) -> bool:
        if part is None:
            return False
        doc, sheet = part
        if self.doc.casefold() not in doc.casefold():
            return False
        if self.sheet is not None and self.sheet.casefold() not in sheet.casefold():
            return False
        return True

    def matches(self, mr: tuple[str, str] | None, ctr: tuple[str, str] | None) -> bool:
        if self.side == "MR":
            return self._side_matches(mr)
        if self.side == "CTR":
            return self._side_matches(ctr)
        return self._side_matches(mr) or self._side_matches(ctr)


# ── Tokenizer ────────────────────────────────────────────────────────────────

_OP_AT   = re.compile(r"(and|or|not)(?=[\s()]|$)", re.I)
_OP_NEXT = re.compile(r"\s+(?:(?:and|or|not)(?=[\s()]|$)|(?:mr|ctr)\s*:)", re.I)
_SIDE    = re.compile(r"(mr|ctr)\s*:", re.I)


def _read_text(s: str, i: int) -> tuple[str, int]:
    """Reads one name starting at s[i]: "quoted", or bare text up to a
    structural character ( ) >, or a following and/or/not word."""
    n = len(s)
    while i < n and s[i].isspace():
        i += 1
    if i < n and s[i] == '"':
        end = s.find('"', i + 1)
        if end < 0:
            raise FilterSyntaxError('Missing closing " quote.')
        return s[i + 1:end], end + 1
    start = i
    while i < n:
        if s[i] in "()>":
            break
        if s[i].isspace() and _OP_NEXT.match(s, i):
            break
        i += 1
    return s[start:i].strip(), i


def _tokenize(s: str) -> list[tuple[str, object]]:
    tokens: list[tuple[str, object]] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch.isspace():
            i += 1
        elif ch in "()":
            tokens.append((ch, None))
            i += 1
        elif m := _OP_AT.match(s, i):
            tokens.append(("op", m.group(1).lower()))
            i = m.end()
        else:
            side = None
            if m := _SIDE.match(s, i):
                side = m.group(1).upper()
                i = m.end()
            doc, i = _read_text(s, i)
            sheet = None
            if i < n and s[i] == ">":
                sheet, i = _read_text(s, i + 1)
            if side is None and not doc and sheet is None:
                raise FilterSyntaxError(
                    f"Unexpected “{s[i:i + 1]}” — put names containing ( ) > in quotes.")
            tokens.append(("term", Term(side, doc, sheet)))
    return tokens


# ── Parser (recursive descent) ───────────────────────────────────────────────
# expr := and_ ("or" and_)* ; and_ := not_ ("and" not_)* ;
# not_ := "not" not_ | "(" expr ")" | term

def parse(text: str):
    """Returns an expression tree, or None for a blank expression."""
    tokens = _tokenize(text)
    if not tokens:
        return None
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else (None, None)

    def eat():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def expr():
        node = and_()
        while peek() == ("op", "or"):
            eat()
            node = ("or", node, and_())
        return node

    def and_():
        node = not_()
        while peek() == ("op", "and"):
            eat()
            node = ("and", node, not_())
        return node

    def not_():
        kind, val = peek()
        if (kind, val) == ("op", "not"):
            eat()
            return ("not", not_())
        if kind == "(":
            eat()
            node = expr()
            if peek()[0] != ")":
                raise FilterSyntaxError("Missing closing bracket “)”.")
            eat()
            return node
        if kind == "term":
            return ("term", eat()[1])
        if kind == ")":
            raise FilterSyntaxError("Unexpected closing bracket “)”.")
        if kind == "op":
            raise FilterSyntaxError(f"“{val}” needs a name or bracket after it.")
        raise FilterSyntaxError("The expression ends too early — a name is missing.")

    tree = expr()
    if pos < len(tokens):
        kind, val = tokens[pos]
        if kind == ")":
            raise FilterSyntaxError("Unexpected closing bracket “)”.")
        raise FilterSyntaxError("Expected and / or between names"
                                + (" (quote names that contain these words)." if kind == "term" else "."))
    return tree


def terms(tree) -> list[Term]:
    """Every name (Term) used in the expression."""
    if tree is None:
        return []
    if tree[0] == "term":
        return [tree[1]]
    return [t for sub in tree[1:] for t in terms(sub)]


def explain_unmatched(term: Term, pairs) -> str | None:
    """None if `term` matches some known (side, document, sheet); otherwise a
    short hint — including the usual cause, a missing ">" before the sheet."""
    pairs = list(pairs)
    for side, doc, sheet in pairs:
        if term.side in (None, side) and term.matches(
                (doc, sheet) if side == "MR" else None,
                (doc, sheet) if side == "CTR" else None):
            return None
    label = (f"{term.side}:" if term.side else "") + term.doc + (f">{term.sheet}" if term.sheet is not None else "")
    if term.sheet is None:
        for side, doc, sheet in sorted(set(pairs)):
            if term.side in (None, side) and sheet and term.doc.casefold().endswith(
                    " " + sheet.casefold()):
                head = term.doc[: -len(sheet)].rstrip()
                if head.casefold() in doc.casefold():
                    return (f"Nothing matches “{label}” — to pick a sheet put “>” before it: "
                            f"{format_term(side, doc, sheet)}")
    return f"Nothing matches “{label}” (names are checked against the compared files and sheets)."


def evaluate(tree, mr: tuple[str, str] | None, ctr: tuple[str, str] | None) -> bool:
    """Is the expression true for a row whose MR side came from
    (document, sheet) `mr` and CTR side from `ctr` (None = no such side)?"""
    kind = tree[0]
    if kind == "term":
        return tree[1].matches(mr, ctr)
    if kind == "not":
        return not evaluate(tree[1], mr, ctr)
    if kind == "and":
        return evaluate(tree[1], mr, ctr) and evaluate(tree[2], mr, ctr)
    return evaluate(tree[1], mr, ctr) or evaluate(tree[2], mr, ctr)


# ── Suggestions (for the autocomplete popup) ─────────────────────────────────

_BOUNDARY = re.compile(r"(?<![^\s(])(?:and|or|not)\s+|\(\s*", re.I)


def fragment_start(prefix: str) -> int:
    """Index in `prefix` (the text left of the cursor) where the name being
    typed begins — after the last "(" or and/or/not."""
    end = 0
    for m in _BOUNDARY.finditer(prefix):
        if prefix.count('"', 0, m.start()) % 2:
            continue                      # inside a "quoted name"
        end = m.end()
    while end < len(prefix) and prefix[end].isspace():
        end += 1
    return end


def _needs_quotes(name: str) -> bool:
    return bool(re.search(r'[()>"]', name) or re.search(r"\b(and|or|not)\b", name, re.I))


def _quote(name: str) -> str:
    return f'"{name}"' if _needs_quotes(name) else name


def format_term(side: str, doc: str, sheet: str | None = None) -> str:
    out = f"{side}:{_quote(doc)}"
    return out + (f">{_quote(sheet)}" if sheet else "")


SHEET_LABEL_PREFIX = "      ↳  "     # marks a sheet entry in suggest()'s labels


def suggest(fragment: str, pairs, limit: int = 40) -> list[tuple[str, str]]:
    """Suggestions for the name being typed. `pairs` is an iterable of
    (side, document, sheet). Returns [(label, text to insert)], documents
    before their sheets. Matching is by part of the name, case-insensitive."""
    frag = fragment.replace('"', "")
    side_f = None
    if ":" in frag:
        head, frag = frag.split(":", 1)
        side_f = head.strip().upper()
        if side_f not in ("MR", "CTR"):
            return []
    doc_f, sheet_f = (frag.split(">", 1) + [None])[:2] if ">" in frag else (frag, None)
    doc_f = doc_f.strip().casefold()
    sheet_f = sheet_f.strip().casefold() if sheet_f is not None else None
    # Typing just "M" / "CT" should offer MR:/CTR: entries rather than nothing.
    side_prefix = frag.strip().upper() if side_f is None and ">" not in frag else ""

    out: list[tuple[str, str]] = []
    seen: set = set()
    # `pairs` sorted by (side, document, sheet): each document's entry is
    # followed directly by its own sheets.
    for side, doc, sheet in sorted(set(pairs)):
        if side_f and side != side_f:
            continue
        by_side = bool(side_prefix) and side.startswith(side_prefix)
        if not by_side and doc_f not in doc.casefold():
            continue
        if sheet_f is None and (side, doc) not in seen:
            seen.add((side, doc))
            out.append((f"{side}:{doc}", format_term(side, doc)))
        if sheet and (sheet_f is None or sheet_f in sheet.casefold()):
            # Sheet name first and indented under its file: long file names
            # get cut off at the right edge, which used to hide the sheet.
            out.append((f"{SHEET_LABEL_PREFIX}{sheet}   — {side}:{doc}", format_term(side, doc, sheet)))
    return out[:limit]
