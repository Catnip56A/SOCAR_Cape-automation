"""
ctr_tools/po.py

Purchase orders for the CTR Tracker (the "Purchase orders" section of the CTR Tracker tab).

  read_po(path)            reads a SAP purchase-order PDF (the text layer; no OCR) into a POData — one POItem per PO
                           item. One PO item normally belongs to one CTR, i.e. one tracker row.
  short_company(name)      the short name the tracker's Company column uses (AIOC, BPCS …), from the editable list
                           CFG["ctr_tracker_po_companies"]; None when the company is not in the list.
  suggest_rows(item, rows) ranks tracker rows as candidates for a PO item from its DESCRIPTION only: a CTR number
                           written in it (exact), then the rows with the most alike description (RapidFuzz).
                           Suggestions only — the user always picks the row, by description or by CTR number.
  read_ctr_pricebook(path) the "Pricebook #" of a generated CTR file, to compare with the PO's.

The labels the reader looks for are config (CFG["ctr_tracker_po"]); the tracker columns the writer fills are in the
same section (tracker_fast.write_pos_fast does the writing). Nothing here touches the tracker file.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from rapidfuzz import fuzz, process, utils

from ctr_tools.config import CFG

log = logging.getLogger(__name__)

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_DATE_RE = re.compile(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})")
_MONEY_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:,\d{3})*\.\d{2}(?![\d])|(?<![\d.,])\d+\.\d{2}(?![\d])")


class POError(ValueError):
    """The file is not a purchase order the reader understands (the message says what is missing)."""


@dataclass
class POItem:
    number:           int = 1
    description:      str = ""
    pricebook:        str = ""
    master_agreement: str = ""
    account:          str = ""
    delivery:         date | None = None
    amount:           float | None = None


@dataclass
class POData:
    path:        str = ""
    po_number:   str = ""
    order_date:  date | None = None
    currency:    str = ""
    company:     str = ""
    total:       float | None = None
    items:       list[POItem] = field(default_factory=list)
    notes:       list[str] = field(default_factory=list)    # things worth a warning (a total that does not add up …)


def _parse_date(text: str) -> date | None:
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    month = _MONTHS.get(m.group(2).upper())
    if month is None:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        return None


def _money(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:    # pragma: no cover - declared dependency
        raise POError("Reading PO files needs the 'pypdf' package, which is not installed.") from exc
    try:
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:
        raise POError(f"{path.name} could not be read as a PDF ({exc}).") from exc


def parse_po_text(text: str, path: str = "") -> POData:
    """The fields of a purchase order from its text (split out of read_po so tests can feed it text)."""
    cfg = CFG["ctr_tracker_po"]
    po = POData(path=str(path))
    lines = [ln.rstrip() for ln in text.splitlines()]

    def after(label: str) -> str:
        for ln in lines:
            i = ln.find(label)
            if i >= 0:
                return ln[i + len(label):].strip()
        return ""

    po.po_number = after(cfg["label_po_number"]).split(" ")[0]
    po.order_date = _parse_date(after(cfg["label_order_date"]))
    po.currency = after(cfg["label_currency"]).split(" ")[0].upper()

    # The company name may wrap onto a second line ("... OPERATING" / "CO."): everything up to the Address line.
    for i, ln in enumerate(lines):
        if ln.startswith(cfg["label_company"]):
            parts = [ln[len(cfg["label_company"]):].strip()]
            for nxt in lines[i + 1:i + 4]:
                if nxt.strip().startswith("Address:") or not nxt.strip():
                    break
                parts.append(nxt.strip())
            po.company = " ".join(p for p in parts if p)
            break

    missing = [name for name, v in (("PO number", po.po_number), ("currency", po.currency), ("company", po.company)) if not v]
    if missing:
        raise POError(f"{Path(path).name or 'The file'} does not look like a purchase order (no {', '.join(missing)} found).")

    # The items block: from the "Item Description ..." header to the total line.
    start = next((i for i, ln in enumerate(lines) if ln.strip().startswith("Item") and "Description" in ln), None)
    end = next((i for i, ln in enumerate(lines) if cfg["label_total"] in ln), None)
    if start is None or end is None or end <= start:
        raise POError(f"{Path(path).name or 'The file'}: the item list of the purchase order was not found.")
    block = lines[start + 1:end]

    total_line = lines[end]
    amounts = _MONEY_RE.findall(total_line)
    po.total = _money(amounts[-1]) if amounts else None

    desc_label = re.escape(cfg["label_item_description"])
    item_start = re.compile(rf"^\s*(\d+)\s+{desc_label}\s*(.*)$")
    chunks: list[tuple[int, list[str]]] = []
    for ln in block:
        m = item_start.match(ln)
        if m:
            chunks.append((int(m.group(1)), [m.group(2).strip()]))
        elif chunks:
            chunks[-1][1].append(ln.strip())
    if not chunks:
        raise POError(f"{Path(path).name or 'The file'}: no item was found in the purchase order.")

    stop_labels = [cfg["label_pricebook"], "Master Agreement #", cfg["label_account"], cfg["label_delivery"]]
    for number, chunk in chunks:
        item = POItem(number=number)
        desc_parts: list[str] = []
        reading_desc = True
        field_lines: list[str] = []
        for ln in chunk:
            if reading_desc and any(ln.startswith(s) for s in stop_labels):
                reading_desc = False
            if reading_desc:
                if ln:
                    desc_parts.append(ln)
            else:
                field_lines.append(ln)
        # the description line may carry quantity/prices at its end when the PO has them; keep the words only
        item.description = re.sub(r"\s+", " ", " ".join(desc_parts)).strip()
        joined = "\n".join(field_lines)

        def field_value(label: str, stops: list[str]) -> str:
            m = re.search(re.escape(label) + r"\s*(.*?)(?=" + "|".join(re.escape(s) for s in stops) + r"|\Z)",
                          joined, re.S)
            return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

        item.pricebook = field_value(cfg["label_pricebook"], ["Master Agreement #", cfg["label_account"]])
        item.master_agreement = field_value("Master Agreement #", [cfg["label_account"], cfg["label_delivery"]])
        item.account = field_value(cfg["label_account"], [cfg["label_delivery"]])
        delivery_text = field_value(cfg["label_delivery"], ["\n\n"])
        item.delivery = _parse_date(delivery_text)
        # the item's amount: the last money figure of its text (after the delivery date, where the PDF prints it)
        tail = joined[joined.find(cfg["label_delivery"]):] if cfg["label_delivery"] in joined else joined
        figures = _MONEY_RE.findall(tail) or _MONEY_RE.findall(" ".join(chunk))
        item.amount = _money(figures[-1]) if figures else None
        po.items.append(item)

    added = sum(i.amount or 0 for i in po.items)
    if po.total is not None and abs(added - po.total) > 0.005:
        po.notes.append(f"the items add up to {added:,.2f} but the PO total is {po.total:,.2f}")
    for item in po.items:
        if item.amount is None:
            po.notes.append(f"item {item.number} has no amount")
        if item.delivery is None:
            po.notes.append(f"item {item.number} has no delivery date")
    return po


def read_po(path: str | Path) -> POData:
    """Read one purchase-order PDF. Raises POError (with a readable message) when it is not one."""
    path = Path(path)
    if not path.is_file():
        raise POError(f"{path.name} was not found.")
    return parse_po_text(_pdf_text(path), str(path))


# ── company short name ──────────────────────────────────────────────────────────────────────────────────────

_SUFFIX_RE = re.compile(r"[\s,.]+(LIMITED|LTD|LLC)\.?$", re.I)


def _company_key(name: str) -> str:
    key = re.sub(r"\s+", " ", (name or "").strip()).upper().rstrip(".,")
    prev = None
    while prev != key:
        prev = key
        key = _SUFFIX_RE.sub("", key).rstrip(".,").strip()
    return key


def short_company(name: str, mapping: dict | None = None) -> str | None:
    """The tracker's short company name for a company as printed on a PO, or None when it is not in the list."""
    mapping = CFG.get("ctr_tracker_po_companies", {}) if mapping is None else mapping
    wanted = _company_key(name)
    if not wanted:
        return None
    for full, short in mapping.items():
        if _company_key(full) == wanted:
            return short
    return None


# ── suggestions: which tracker row is this PO item for? ─────────────────────────────────────────────────────

_CTR_REF_RE = re.compile(r"\b([A-Z]{2,4})[-\s]?(\d{2})[-\s]?(\d{1,4})(?:R\d+)?\b", re.I)


def ctr_base(ctr: str) -> str:
    """"CTR-22-117R1" / "ctr 22 117 AZN" -> "CTR-22-117" (revision and currency suffixes removed), or "" when the
    text is not a CTR number."""
    m = _CTR_REF_RE.search(ctr or "")
    if not m:
        return ""
    return f"{m.group(1).upper()}-{m.group(2)}-{int(m.group(3)):03d}"


_NUMBER_QUERY_RE = re.compile(r"[A-Za-z]{0,4}[-\s]*\d[\d\s\-]*(?:R\d+)?")
_MIN_SIMILARITY = 60.0


def is_number_query(text: str) -> bool:
    """True for text that is a CTR number or part of one ("334", "26-334", "ctr 26 334"): such a search is exact,
    never fuzzy."""
    return bool(_NUMBER_QUERY_RE.fullmatch((text or "").strip()))


def _similarity(a: str, b: str, **kw) -> float:
    # word set (order and extra words ignored) mostly, a little word order to separate near-ties
    return 0.75 * fuzz.token_set_ratio(a, b, **kw) + 0.25 * fuzz.token_sort_ratio(a, b, **kw)


def fuzzy_rows(query: str, rows: list[dict], limit: int = 40, cutoff: float = _MIN_SIMILARITY) -> list[tuple[dict, float]]:
    """Tracker rows whose description is like `query` (RapidFuzz; typos, word endings and word order do not matter),
    most alike first, the newest row first among equals. Rows without a description are not considered."""
    if not (query or "").strip():
        return []
    cand = [r for r in rows if r.get("description")]
    hits = process.extract(query, [r["description"] for r in cand], scorer=_similarity,
                           processor=utils.default_process, score_cutoff=cutoff, limit=limit * 3)
    out = [(cand[i], score) for _text, score, i in hits]
    out.sort(key=lambda t: (-round(t[1], 1), -t[0]["row"]))
    return out[:limit]


_TOKEN_RE = re.compile(r"[^\W_]{2,}", re.U)
_WORD_CUTOFF = 78.0


class WordIndex:
    """The words of the tracker descriptions, for a typo-tolerant search by a few typed words."""

    def __init__(self, descriptions: list[str]):
        self.tokens: dict[str, set[int]] = {}
        for pos, text in enumerate(descriptions):
            for tok in _TOKEN_RE.findall((text or "").lower()):
                self.tokens.setdefault(tok, set()).add(pos)
        self._vocab = list(self.tokens)

    def search(self, query: str, limit: int = 40) -> list[tuple[int, float]]:
        """[(position in the description list, score)] best first: every typed word must be like a word of the
        description (RapidFuzz character similarity, so a typo or another ending still counts)."""
        words = _TOKEN_RE.findall((query or "").lower())
        if not words or not self._vocab:
            return []
        score_by_pos: dict[int, float] | None = None
        for w in words:
            best: dict[int, float] = {}
            for tok, sc, _i in process.extract(w, self._vocab, scorer=fuzz.ratio, score_cutoff=_WORD_CUTOFF, limit=60):
                for pos in self.tokens[tok]:
                    if sc > best.get(pos, 0.0):
                        best[pos] = sc
            if score_by_pos is None:
                score_by_pos = best
            else:
                score_by_pos = {pos: score_by_pos[pos] + sc for pos, sc in best.items() if pos in score_by_pos}
            if not score_by_pos:
                return []
        ranked = sorted(score_by_pos.items(), key=lambda t: (-t[1], t[0]))
        return [(pos, sc / len(words)) for pos, sc in ranked[:limit]]


def suggest_rows(item: POItem, rows: list[dict], limit: int | None = None) -> list[dict]:
    """Candidate tracker rows for a PO item, best first, from the item's DESCRIPTION only: a CTR number written in
    the description counts as an exact match and comes first; the rest are the rows with the most alike description
    (RapidFuzz). Each is {"row", "score", "reasons": [...]}. `rows` come from tracker_fast.read_tracker_rows.
    Only a help — the user picks the row (and can pick any row by typing its CTR number)."""
    limit = limit or int(CFG["ctr_tracker_po"].get("suggestions", 8))
    typed = {ctr_base(m.group(0)) for m in _CTR_REF_RE.finditer(item.description or "")}
    typed.discard("")
    out: dict[int, dict] = {}
    for r in rows:
        if r.get("ctr") and ctr_base(r["ctr"]) in typed:
            out[r["row"]] = {"row": r["row"], "score": 1000.0, "reasons": ["the CTR number is in the PO text"]}
    for rec, score in fuzzy_rows(item.description, rows, limit=limit * 2):
        if rec.get("ctr") and rec["row"] not in out:
            out[rec["row"]] = {"row": rec["row"], "score": round(score, 1),
                               "reasons": [f"description {score:.0f} % alike"]}
    ranked = sorted(out.values(), key=lambda s: (-s["score"], -s["row"]))
    return ranked[:limit]


# ── the pricebook number of a CTR file ──────────────────────────────────────────────────────────────────────

def read_ctr_file_info(path: str | Path) -> tuple[str, str]:
    """(pricebook number, CTR base number) of a generated CTR file: the number after "Pricebook #:" and the CTR
    reference cell of its first sheet ("CTR-26-334", "CTR-26-342 USD" -> "CTR-26-342"). Either is "" when not found."""
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        pricebook = ""
        for row in ws.iter_rows(min_row=1, max_row=12, max_col=16):
            cells = list(row)
            for i, c in enumerate(cells):
                if not pricebook and isinstance(c.value, str) and c.value.strip().lower().startswith("pricebook"):
                    for nxt in cells[i + 1:i + 4]:
                        if nxt.value not in (None, ""):
                            v = nxt.value
                            pricebook = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v).strip()
                            break
        ref_cell = CFG.get("ctr_extract", {}).get("cell_ctr_ref", "E3")
        try:
            ctr = ctr_base(str(ws[ref_cell].value or ""))
        except Exception:
            ctr = ""
        return pricebook, ctr
    finally:
        wb.close()


def read_ctr_pricebook(path: str | Path) -> str:
    """The number after "Pricebook #:" on the first sheet of a generated CTR file (blank when there is none)."""
    return read_ctr_file_info(path)[0]


def pricebook_differs(po_pricebook: str, ctr_pricebook: str) -> bool:
    """True when both are known and are not the same number."""
    a, b = re.sub(r"\D", "", po_pricebook or ""), re.sub(r"\D", "", ctr_pricebook or "")
    return bool(a and b and a != b)
