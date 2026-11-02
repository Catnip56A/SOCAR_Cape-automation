"""
comparator/column_keywords.py — which header texts of an MR / CTR table mean which column, and where the headers are.

The parser (sheet_parser.py) looks for the columns of a table by their header text and then ALWAYS uses the app's
standard names (Description, Quantity, Unit, Rate, Days, Total, Stock Code …). What this module holds is only the list
of header spellings that lead to each standard name, plus the header row numbers, so a file whose author renamed a
header ("Tot" for "Total") can be taught once in Settings instead of needing a code change.

Two copies, like the Stock Code types (ctr_tools/stockcode_types.py):
  * SHIPPED (below) — the lists that ship with the app. In code, so there is nothing to bundle or lose.
  * `column_keywords.json` in the per-user data folder (ctr_tools/paths.py) — the user's own full copy, created from
    the shipped lists on first use. This is what the Settings window edits. Merge shipped adds the words a newer
    version brought; Undo my entries removes the words the user added; Reset replaces everything with the shipped lists.

A word is matched against a header with case, punctuation and "*" ignored:
    "qty"       the header contains the word (or the phrase "unit price"), starting at a word: "Qty *", "Unit Price, AZN",
                "item specification" also matches "Item Specifications in English …"
    "=unit"     the header is exactly that:                                                "Unit" but not "Unit Price"
When several words match one header the most specific one wins (more words, then longer): "Unit Price" is Rate even
though "=unit" would also have matched "Unit". Headers that match nothing keep their own text as the column name.

File format:
    {"version": 1,
     "ctr": {"Quantity": ["=quantity", "=qty"], …},        # concept -> words
     "mr":  {"Stock Code": ["stockcode"], …},
     "rows": {"mr": 9, "ctr_equipment": null, "ctr_consumables": null}}      # Excel row numbers; null = find it
"""

from __future__ import annotations

import copy
import json
import logging
import re
import shutil
from contextlib import contextmanager
from pathlib import Path

from ctr_tools.paths import user_data_dir

log = logging.getLogger(__name__)

_KEYWORDS_PATH = user_data_dir() / "column_keywords.json"

SHIPPED_VERSION = 1
IGNORE = "(ignore)"        # headers that must not be read as a standard column (the generator's internal cost columns)

SHIPPED: dict = {
    "ctr": {
        "Description": ["=description", "=general equipment"],
        "Quantity":    ["=quantity", "=qty"],
        "Unit":        ["=unit"],
        "Rate":        ["rate", "unit price"],
        "Days":        ["=days"],
        "Total":       ["=total", "=tot", "ctr total", "total azn"],
        "Stock Code":  ["equipmentcode", "equipment code", "productcode", "product code", "stock code", "stockcode"],
        IGNORE:        ["total sc"],
    },
    "mr": {
        "Stock Code":    ["stockcode", "stock code"],
        "No":            ["=№"],
        "Qty":           ["qty"],
        "Unit":          ["unit"],
        "Description":   ["item specification", "item spec"],
        "Delivery Date": ["required delivery date", "delivery date"],
        "Return Date":   ["planned return date", "return date"],
        "Rechargeable":  ["rechargeable"],
        "Allocation":    ["allocation"],
    },
}
SHIPPED_ROWS: dict = {"mr": 9, "ctr_equipment": None, "ctr_consumables": None}
SIDES = ("ctr", "mr")
ROW_KEYS = ("mr", "ctr_equipment", "ctr_consumables")
MAX_ROW = 500

_cache: tuple[object, dict] | None = None       # ((path, mtime), cfg)
_override: dict | None = None                   # the "Test on a file" tool tries unsaved words


# ── text matching ──────────────────────────────────────────────────────────────────────────────

def normalise(text) -> str:
    """Lower case, "*" and punctuation removed, words separated by one space: "Unit Price, AZN" -> "unit price azn"."""
    return " ".join(re.findall(r"\w+", str(text).replace("*", " ").lower()))


def _parse_word(word: str) -> tuple[bool, str, str]:
    """(exact, normalised text, raw lower text) of one stored word."""
    w = str(word).strip()
    exact = w.startswith("=")
    w = w[1:].strip() if exact else w
    return exact, normalise(w), w.lower()


def word_matches(word: str, header: str) -> bool:
    exact, norm_w, raw_w = _parse_word(word)
    h_norm, h_raw = normalise(header), str(header).strip().lower()
    if not norm_w:                                   # a symbol-only word such as "№"
        return bool(raw_w) and h_raw == raw_w
    if exact:
        return h_norm == norm_w
    return re.search(rf"(?<!\w){re.escape(norm_w)}", h_norm) is not None     # starts at a word: "specification" ~ "specifications"


def match(cfg: dict, side: str, header) -> tuple[str | None, str | None]:
    """(concept, the word that matched) for a header text, or (None, None). The most specific word wins."""
    best, best_key = (None, None), (0, 0)
    for concept, words in cfg.get(side, {}).items():
        for w in words:
            if word_matches(w, header):
                _exact, norm_w, raw_w = _parse_word(w)
                key = (len((norm_w or raw_w).split()), len(norm_w or raw_w))
                if key > best_key:
                    best, best_key = (concept, w), key
    return best


def is_shipped_word(side: str, concept: str, word: str) -> bool:
    return str(word).strip().lower() in {w.lower() for w in SHIPPED.get(side, {}).get(concept, [])}


# Shipped spellings that are variants of the standard name, not the name itself: a parse note says when one was used.
_VARIANTS = {"=tot", "ctr total", "total azn"}


def is_variant(side: str, concept: str, word: str) -> bool:
    """True when `word` is the user's own or one of the shipped variant spellings (worth a note when it matched)."""
    return (not is_shipped_word(side, concept, word)) or str(word).strip().lower() in _VARIANTS


def concept_words(cfg: dict, side: str, concepts: list[str]) -> list[str]:
    return [w for c in concepts for w in cfg.get(side, {}).get(c, [])]


# ── the data ────────────────────────────────────────────────────────────────────────────────────

def shipped() -> dict:
    return {"version": SHIPPED_VERSION, "ctr": copy.deepcopy(SHIPPED["ctr"]), "mr": copy.deepcopy(SHIPPED["mr"]),
            "rows": dict(SHIPPED_ROWS)}


def _clean_row(v):
    if v in (None, "", "auto"):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"a header row must be a whole number, not {v!r}")
    if not 1 <= n <= MAX_ROW:
        raise ValueError(f"a header row must be between 1 and {MAX_ROW}, not {n}")
    return n


def check_structure(data) -> dict:
    """The file's content as a clean config; raises ValueError saying what is wrong (an import is refused, never half-read)."""
    if not isinstance(data, dict):
        raise ValueError("the file is not a column-keywords file (a JSON object was expected)")
    cfg = {"version": int(data.get("version", SHIPPED_VERSION)), "ctr": {}, "mr": {}, "rows": dict(SHIPPED_ROWS)}
    for side in SIDES:
        block = data.get(side)
        if not isinstance(block, dict):
            raise ValueError(f"the “{side}” section is missing")
        for concept, words in block.items():
            if not isinstance(words, list) or any(not isinstance(w, str) for w in words):
                raise ValueError(f"{side} › {concept}: the words must be a list of texts")
            clean, seen = [], set()
            for w in words:
                w = w.strip()
                if w and w.lower() not in seen:
                    seen.add(w.lower())
                    clean.append(w)
            cfg[side][str(concept)] = clean
    rows = data.get("rows", {})
    if not isinstance(rows, dict):
        raise ValueError("“rows” must be an object")
    for k in ROW_KEYS:
        if k in rows:
            cfg["rows"][k] = _clean_row(rows[k])
    return cfg


def read(path: Path) -> dict | None:
    """The file at `path` as a config, or None when it is missing or not valid."""
    try:
        return check_structure(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        log.warning("Column keywords file %s not usable: %s", path, exc)
        return None


def _stamp():
    try:
        return (str(_KEYWORDS_PATH), _KEYWORDS_PATH.stat().st_mtime_ns)
    except OSError:
        return (str(_KEYWORDS_PATH), None)


def load() -> dict:
    """The user's config. On first use it is created from the shipped lists; an unreadable file falls back to them."""
    if not _KEYWORDS_PATH.exists():
        try:
            _KEYWORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
            _KEYWORDS_PATH.write_text(json.dumps(shipped(), indent=1, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            log.warning("Could not create %s: %s", _KEYWORDS_PATH, exc)
            return shipped()
    return read(_KEYWORDS_PATH) or shipped()


def current() -> dict:
    """The config the parser uses: re-read from disk only when the file changed. Do not modify the result."""
    global _cache
    if _override is not None:
        return _override
    stamp = _stamp()
    if _cache is None or _cache[0] != stamp:
        cfg = load()
        _cache = (_stamp(), cfg)
    return _cache[1]


@contextmanager
def using(cfg: dict):
    """Parse with `cfg` instead of the saved config for a while (the Settings window's "Test on a file")."""
    global _override
    prev, _override = _override, cfg
    try:
        yield
    finally:
        _override = prev


def save(cfg: dict) -> None:
    data = {"version": int(cfg.get("version", SHIPPED_VERSION)),
            "ctr": {c: list(w) for c, w in cfg["ctr"].items()},
            "mr": {c: list(w) for c, w in cfg["mr"].items()},
            "rows": {k: cfg.get("rows", {}).get(k) for k in ROW_KEYS}}
    check_structure(data)                       # never write what cannot be read back
    _KEYWORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _KEYWORDS_PATH.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    global _cache
    _cache = None


def header_row(cfg: dict, key: str) -> int | None:
    """The Excel row number set for "mr" / "ctr_equipment" / "ctr_consumables", or None (= find it)."""
    return cfg.get("rows", {}).get(key)


# ── the buttons of the Settings window ──────────────────────────────────────────────────────────

def _lower_set(words) -> set[str]:
    return {w.strip().lower() for w in words}


def new_shipped_words(cfg: dict) -> int:
    """How many shipped words the user's config does not have (anywhere on that side)."""
    n = 0
    for side in SIDES:
        have = _lower_set(w for ws in cfg.get(side, {}).values() for w in ws)
        n += sum(1 for ws in SHIPPED[side].values() for w in ws if w.lower() not in have)
    return n


def merge_shipped() -> int:
    """Add the shipped words the user's config does not have yet; the user's own words, deletions of other words and
    header rows are kept. Returns how many words were added."""
    mine = read(_KEYWORDS_PATH) or shipped()
    added = 0
    for side in SIDES:
        have = _lower_set(w for ws in mine[side].values() for w in ws)
        for concept, words in SHIPPED[side].items():
            for w in words:
                if w.lower() not in have:
                    mine[side].setdefault(concept, []).append(w)
                    have.add(w.lower())
                    added += 1
    mine["version"] = SHIPPED_VERSION
    save(mine)
    return added


def own_entries(cfg: dict) -> list[tuple[str, str, str]]:
    """(side, concept, word) for every word that is not one of the shipped words of that concept."""
    return [(side, concept, w) for side in SIDES for concept, words in cfg.get(side, {}).items()
            for w in words if not is_shipped_word(side, concept, w)]


def undo_my_entries() -> int:
    """Remove every word the user added (the shipped words stay as they are in the user's config; deleted shipped words
    stay deleted: Merge shipped brings them back). Header rows are settings, not entries, and are kept."""
    mine = read(_KEYWORDS_PATH) or shipped()
    removed = 0
    for side in SIDES:
        for concept in list(mine[side]):
            kept = [w for w in mine[side][concept] if is_shipped_word(side, concept, w)]
            removed += len(mine[side][concept]) - len(kept)
            mine[side][concept] = kept
        for concept in [c for c, w in mine[side].items() if not w and c not in SHIPPED[side]]:
            del mine[side][concept]
    save(mine)
    return removed


def reset_to_shipped() -> None:
    """Replace the user's config (words and header rows) with the shipped one (the caller backs the old file up first)."""
    _KEYWORDS_PATH.unlink(missing_ok=True)
    global _cache
    _cache = None
    load()


def export_to(path: Path, cfg: dict | None = None) -> None:
    cfg = cfg or current()
    Path(path).write_text(json.dumps({**cfg, "rows": {k: cfg.get("rows", {}).get(k) for k in ROW_KEYS}},
                                     indent=1, ensure_ascii=False), encoding="utf-8")


def import_from(path: Path) -> dict:
    """The config in a file, checked; raises ValueError saying what is wrong. Nothing is changed by this call."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"the file is not valid JSON ({exc})") from exc
    except OSError as exc:
        raise ValueError(f"the file cannot be read ({exc})") from exc
    return check_structure(data)


# ── checks before saving ────────────────────────────────────────────────────────────────────────

_GENERIC = {"the", "of", "and", "no", "item", "name", "date", "amount", "cost", "price", "code"}


def problems(cfg: dict) -> list[dict]:
    """What is risky in `cfg`: [{"level": "error" | "warn", "text": …}]. Errors stop a save; warnings ask first."""
    out: list[dict] = []

    def add(level, text):
        out.append({"level": level, "text": text})

    for side in SIDES:
        owner: dict[str, str] = {}
        for concept, words in cfg.get(side, {}).items():
            if not words and concept != IGNORE and concept in SHIPPED[side]:
                add("warn", f"{side.upper()} › {concept}: no words — that column can no longer be recognised.")
            for w in words:
                exact, norm_w, raw_w = _parse_word(w)
                body = norm_w or raw_w
                if not body:
                    add("error", f"{side.upper()} › {concept}: an empty word.")
                    continue
                if not norm_w and exact:
                    pass                                      # a symbol such as “№”, matched exactly: nothing to mis-match
                elif len(body) < (2 if exact else 3):
                    add("warn", f"{side.upper()} › {concept}: “{w}” is very short — it may match unrelated headers.")
                elif body in _GENERIC and not exact:
                    add("warn", f"{side.upper()} › {concept}: “{w}” is very generic — it may match unrelated headers "
                                "(put = in front, “=" + body + "”, to match only a header that is exactly that).")
                key = w.strip().lower()
                if key in owner and owner[key] != concept:
                    add("warn", f"{side.upper()}: “{w}” is in both {owner[key]} and {concept}; the first one found wins.")
                owner.setdefault(key, concept)
    for k in ROW_KEYS:
        try:
            _clean_row(cfg.get("rows", {}).get(k))
        except ValueError as exc:
            add("error", f"Header row ({k}): {exc}.")
    return out
