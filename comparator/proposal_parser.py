"""
comparator/proposal_parser.py

Reads a PDMS proposal (the Word "REQUEST FOR QUOTATION" document) into the same table dicts as
sheet_parser.parse_workbook, so a proposal can stand in for the CTR in Compare.

The items table has a header row with a Description, a UOM / Unit, a Qty and a cost column
(Costs / Price / Rate) and usually a Total column. Totals rows ("Consumables total", "GRAND TOTAL",
the small totals table) are skipped. A proposal has no Stock Code: match it by Description.
Prices are only read for display (Rate / Total); nothing compares them.
"""
from __future__ import annotations

import logging
import re
from io import BytesIO
from pathlib import Path

import pandas as pd

from comparator import column_keywords as CK

log = logging.getLogger(__name__)

SECTION = "Proposal"
_SKIP_FIRST_CELL = re.compile(r"\b(grand\s+)?(sub\s*)?total\b", re.I)


def _concept(header: str) -> str | None:
    """Which column a header names: Description / Unit / Quantity / Rate / Total, or None."""
    h = re.sub(r"[^a-z0-9 ]", " ", str(header).lower())
    h = re.sub(r"\s+", " ", h).strip()
    if not h:
        return None
    if "total" in h or "amount" in h:
        return "Total"
    if h.startswith(("descr", "item")) or "description" in h:
        return "Description"
    if h in ("uom", "unit", "units") or h.startswith(("uom", "unit of")):
        return "Unit"
    if h.replace(" ", "") in ("qty", "qty", "quantity", "qnty") or h.startswith(("q ty", "qty", "quant")):
        return "Quantity"
    if any(w in h for w in ("cost", "price", "rate")):
        return "Rate"
    return None


def _number(text: str):
    """"1,119.06 ₼" -> 1119.06; "" or text that is no number -> None."""
    s = re.sub(r"[^0-9.,\-]", "", str(text))
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:                                  # "1,5" decimal comma, or "1,234" thousands
        head, _, tail = s.rpartition(",")
        s = head.replace(",", "") + ("." if len(tail) != 3 else "") + tail
    try:
        return float(s)
    except ValueError:
        return None


def _cells(row) -> list[str]:
    out, last = [], None
    for c in row.cells:
        if c._tc is last:                           # a merged cell is repeated by python-docx
            continue
        last = c._tc
        out.append(" ".join(c.text.split()))
    return out


def parse_proposal(path, filename: str | None = None) -> list[dict]:
    """The proposal's item tables as [{"source_file", "source_sheet", "table_name", "data", "notes", "kw"}]."""
    import docx

    if isinstance(path, BytesIO):
        name = filename or "proposal.docx"
        doc = docx.Document(path)
    else:
        name = Path(path).name
        doc = docx.Document(str(path))

    results: list[dict] = []
    for ti, table in enumerate(doc.tables, start=1):
        rows = [_cells(r) for r in table.rows]
        header_i, colmap = None, {}
        for i, cells in enumerate(rows):
            m = {}
            for j, h in enumerate(cells):
                c = _concept(h)
                if c and c not in m.values():
                    m[j] = c
            if "Description" in m.values() and "Quantity" in m.values():
                header_i, colmap = i, m
                break
        if header_i is None:
            continue
        by_concept = {c: j for j, c in colmap.items()}

        items, skipped = [], 0
        for cells in rows[header_i + 1:]:
            def get(concept):
                j = by_concept.get(concept)
                return cells[j] if j is not None and j < len(cells) else ""
            desc = get("Description")
            if not desc or _SKIP_FIRST_CELL.search(desc):
                skipped += 1
                continue
            qty = _number(get("Quantity"))
            if qty is None:
                skipped += 1
                continue
            items.append({
                "_Section": SECTION,
                "Description": desc,
                "Quantity": qty,
                "Unit": get("Unit"),
                "Rate": _number(get("Rate")),
                "Total": _number(get("Total")),
                "_SourceSheet": f"Table {ti}",
                "_SourceFile": name,
            })
        if not items:
            continue
        results.append({
            "source_file": name,
            "source_sheet": f"Table {ti}",
            "table_name": f"Proposal table {ti}",
            "data": pd.DataFrame(items),
            "notes": [],
            "kw": CK.fingerprint(),
        })
        log.info("Proposal %r table %d: %d item(s), %d row(s) skipped", name, ti, len(items), skipped)
    return results
