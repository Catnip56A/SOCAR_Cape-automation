"""
ctr_tools/builder_pdms.py

Writes the PDMS proposal (a Word document) from the same rows the AZN and USD
CTRs are built from. Nothing is recalculated here except the AZN conversion of
equipment and consumables (see _unit_prices) — the labour rows already carry
AZN rates.

The template (TEST_Files/CTR creator/PDMS_TEMPLATE_short.docx) holds tags that
are replaced here:

    {{PRP_NUMBER}} {{DATE}} {{CUSTOMER_NAME}} {{CUSTOMER_CITY}} {{ATTENTION_NAME}}
    {{SALUTATION_NAME}} {{JOB_TITLE}} {{SIGNER_NAME}} {{SIGNER_TITLE}} {{LOCATION}}
    {{CURRENCY}} {{GRAND_TOTAL}} {{TABLES_REFERENCE}} {{NO_LABOUR}} {{NO_EQUIPMENT}}
    {{NO_CONSUMABLES}}
    Tables are the sample's own (header rows, merges, fills, fonts as in Word). Their
    data cells carry tags — {{L_DESC}} ... for labour, {{E_DESC}} ... equipment,
    {{C_DESC}} ... consumables, {{S_LABOUR}} ... the summary, {{HOTBED_*}}, {{*_SUM}} —
    and the row with the first tag is copied once per data line.
    {{#IF_EQUIPMENT}} ... {{/IF_EQUIPMENT}}   (same for CONSUMABLES)
        caption and table between the markers are deleted when the section is empty

Fixed texts and defaults live in template_config.json ("pdms").

PROVISIONAL: the USD -> AZN conversion of equipment and consumables (CBAR rate,
consumables mark-up applied) is not confirmed by the colleagues yet — task log
MA202. Everything that depends on it is in _unit_prices().
"""

from __future__ import annotations

import copy
import logging
import re
from datetime import datetime
from pathlib import Path

import docx
from docx.table import Table, _Row
from docx.text.paragraph import Paragraph

from ctr_tools.config import CFG
from ctr_tools.naming import pdms_output_filename

log = logging.getLogger(__name__)

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]

_DEFAULTS = {
    # The real customer and signer are configuration (template_config.json, "pdms"); the code carries none.
    "customer_name": "",
    "customer_city": "",
    "signer_name":   "",
    "signer_title":  "",
    "currency":      "AZN",
    "currency_symbol": "₼",
    "hotbed_label":  "Hotbed – if required",
    "hotbed_rate":   59.0,
    "default_usd_azn": 1.7,
    "markup_rate": 0.10,          # the mark-up a PDMS proposal usually carries; the Markup Rate box switches to it
    "category_currencies": {"manpower": "AZN", "equipment": "USD", "consumables": "USD", "transport": "AZN"},
}


def pdms_defaults() -> dict:
    """The "pdms" block of template_config.json over the built-in defaults."""
    cfg = {**_DEFAULTS, **CFG.get("pdms", {})}
    cfg["category_currencies"] = {**_DEFAULTS["category_currencies"], **cfg.get("category_currencies", {})}
    return cfg


# ── Header helpers (also used by the UI) ─────────────────────────────────────

def attention_from_requester(requester: str) -> str:
    """"Jane Doe <a@x.com>" -> "Jane Doe";
    "Doe, Jane (j@x.com) Specialist" -> "Jane Doe"."""
    name = re.split(r"[<(]", requester or "", maxsplit=1)[0].strip()
    if "," in name:
        last, _, first = name.partition(",")
        name = f"{first.strip()} {last.strip()}".strip()
    return re.sub(r"\s+", " ", name)


def salutation_from_attention(attention: str) -> str:
    """First word of the attention name ("Dear Jane,")."""
    parts = (attention or "").split()
    return parts[0] if parts else ""


def letter_date(text: str) -> str:
    """"2026-09-09" -> "9 September 2026"; anything else is kept as typed."""
    try:
        d = datetime.strptime(text.strip(), "%Y-%m-%d")
    except ValueError:
        return text.strip()
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


# ── Numbers ───────────────────────────────────────────────────────────────────

def _num(v) -> float | None:
    """A number, or None for a non-rechargeable / blank value."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _money(v: float, symbol: str) -> str:
    return f"{v:,.2f} {symbol}"


def _qty(v: float) -> str:
    return f"{v:g}"


def _cents(v: float) -> float:
    """A line total rounded to 2 decimals — the one place amounts are rounded."""
    return round(v + 1e-9, 2)


def _to_azn(code: str, rates: dict) -> float:
    """AZN per 1 unit of `code`; AZN itself is 1. A currency without a rate is refused."""
    code = (code or "AZN").upper()
    if code == "AZN":
        return 1.0
    rate = _num(rates.get(code))
    if not rate or rate <= 0:
        raise ValueError(f"No CBAR rate for {code}: enter it (or reload it) in the CBAR rates and generate again.")
    return rate


def _currency_of(row: dict, category: str, defaults: dict) -> str:
    """The currency a line's price is in: its own, else its category's."""
    return str(row.get("currency") or defaults[category]).upper()


def _unit_prices(equip_rows, consump_rows, rates: dict, markup_rate: float, defaults: dict):
    """
    PROVISIONAL (MA202): AZN prices of equipment and consumables. A price is in the line's own
    currency (else its category's); it is converted to AZN first, and the consumables mark-up
    is added to the converted price. Line totals are rounded to 2 decimals.
    Non-rechargeable lines (no number) are not billed and are left out.
    Returns (equipment, consumables) as lists of dicts with the columns the tables show.
    """
    equipment = []
    for r in equip_rows:
        rate = _num(r.get("rate_per_day"))
        if rate is None:
            continue
        qty, days = float(r["quantity"]), float(r["days"])
        cur = _currency_of(r, "equipment", defaults)
        fx = _to_azn(cur, rates)
        unit = rate * fx
        equipment.append({"description": r["description"], "qty": qty, "days": days,
                          "uom": r.get("unit", ""), "unit": unit, "total": _cents(qty * days * unit),
                          "native": rate, "cur": cur, "fx": fx, "code": str(r.get("stock_code") or "")})
    consumables = []
    for r in consump_rows:
        cost = _num(r.get("local_expect_cost"))
        if cost is None:
            continue
        qty = float(r["quantity"])
        cur = _currency_of(r, "consumables", defaults)
        fx = _to_azn(cur, rates)
        unit = cost * fx * (1 + markup_rate)
        consumables.append({"description": r["long_description"], "uom": r.get("unit_code", ""),
                            "qty": qty, "unit": unit, "total": _cents(qty * unit),
                            "native": cost, "cur": cur, "fx": fx, "markup": markup_rate,
                            "code": str(r.get("product") or "")})
    return equipment, consumables


def _transport_lines(third_party_rows, rates: dict, defaults: dict) -> list[dict]:
    """Transport / hired-service lines for the end of the consumables table: only the description
    and what it costs — quantity × rate × duration, the rate converted to AZN first, then × (1 +
    the line's own mark-up) — the AZN CTR's formula. A quantity of 0 or less is left out, as the
    AZN CTR hides it; a missing figure is refused."""
    out = []
    for i, tp in enumerate(third_party_rows or []):
        what = tp.get("description") or tp.get("transport_type") or f"line {i + 1}"
        qty, rate, duration = _num(tp.get("quantity")), _num(tp.get("rate_azn")), _num(tp.get("duration"))
        if None in (qty, rate, duration):
            raise ValueError(f"Transport line {what!r} has no quantity, rate or duration.")
        if qty <= 0:
            continue
        cur = _currency_of(tp, "transport", defaults)
        fx = _to_azn(cur, rates)
        rate_azn = rate * fx
        markup = _num(tp.get("markup")) or 0.0
        out.append({"description": str(what),
                    "total": _cents(qty * rate_azn * duration * (1 + markup)),
                    "qty": qty, "duration": duration, "uom": str(tp.get("uom") or ""), "native": rate,
                    "cur": cur, "fx": fx, "markup": markup})
    return out


def _labour_description(row: dict) -> str:
    """"PA Level Foreman-National" + "Day Shift" -> "PA Level Foreman-Day shift"
    (the nationality has its own column)."""
    desc = row["description"].strip()
    nat = (row.get("nationality") or "").strip()
    if nat and desc.lower().endswith("-" + nat.lower()):
        desc = desc[: -(len(nat) + 1)]
    shift = (row.get("comment") or "").replace("Shift", "shift").strip()
    return f"{desc}-{shift}" if shift else desc


# ── Word helpers ──────────────────────────────────────────────────────────────

_TAG = re.compile(r"\{\{[^{}]+\}\}")


def _replace_in_paragraph(p: Paragraph, values: dict) -> None:
    """Replace tags in p. A tag normally sits in one run (the template is built
    so); if Word split one, the runs are merged into the first so it still works."""
    if "{{" not in p.text:
        return
    for run in p.runs:
        if "{{" in run.text:
            run.text = _fill(run.text, values)
    if "{{" in p.text and p.runs and any(t in values for t in _TAG.findall(p.text)):
        merged = _fill(p.text, values)
        p.runs[0].text = merged
        for run in p.runs[1:]:
            run.text = ""


def _fill(text: str, values: dict) -> str:
    return _TAG.sub(lambda m: values.get(m.group(0), m.group(0)), text)


def _all_paragraphs(doc):
    yield from doc.paragraphs
    for section in doc.sections:
        for part in (section.header, section.first_page_header, section.even_page_header,
                     section.footer, section.first_page_footer, section.even_page_footer):
            # Only the headers / footers the template really has: reading a missing one would
            # make python-docx add an empty part to the file.
            if not part.is_linked_to_previous:
                yield from part.paragraphs


def _find_paragraph(doc, text: str) -> Paragraph | None:
    for p in doc.paragraphs:
        if p.text.strip() == text:
            return p
    return None


def _remove_block(doc, name: str) -> None:
    """Delete the paragraphs from {{#IF_name}} to {{/IF_name}}, markers included."""
    start, end = _find_paragraph(doc, "{{#IF_%s}}" % name), _find_paragraph(doc, "{{/IF_%s}}" % name)
    if start is None or end is None:
        raise ValueError(f"PDMS template has no {{{{#IF_{name}}}}} ... {{{{/IF_{name}}}}} block.")
    el = start._p
    while True:
        nxt = el.getnext()
        el.getparent().remove(el)
        if el is end._p or nxt is None:
            break
        el = nxt


def _drop_markers(doc, name: str) -> None:
    for tag in ("{{#IF_%s}}" % name, "{{/IF_%s}}" % name):
        p = _find_paragraph(doc, tag)
        if p is not None:
            p._p.getparent().remove(p._p)


def _find_table(doc, tag: str) -> Table:
    for t in doc.tables:
        if any(tag in c.text for r in t.rows for c in r.cells):
            return t
    raise ValueError(f"PDMS template has no table with {tag}.")


def _row_with(table: Table, tag: str):
    for r in table.rows:
        if any(tag in c.text for c in r.cells):
            return r
    raise ValueError(f"PDMS template: no table row with {tag}.")


def _fill_row(table: Table, tr, values: dict) -> None:
    for cell in _Row(tr, table).cells:
        for p in cell.paragraphs:
            _replace_in_paragraph(p, values)


def _fill_rows(table: Table, tag: str, rows: list[dict]) -> None:
    """The row holding `tag` is the sample row of the table (its fills, fonts and
    alignment come with it): one copy per entry of `rows`, the sample itself removed."""
    sample = _row_with(table, tag)._tr
    for values in rows:
        tr = copy.deepcopy(sample)
        sample.addprevious(tr)
        _fill_row(table, tr, values)
    sample.getparent().remove(sample)


def _drop_row(table: Table, tag: str) -> None:
    tr = _row_with(table, tag)._tr
    tr.getparent().remove(tr)


# ── Entry point ───────────────────────────────────────────────────────────────

def compute_pdms(
    support_rows: list[dict],
    other_rows: list[dict],
    equip_rows: list[dict],
    consump_rows: list[dict],
    header: dict,
    rates: dict,
    markup_rate: float,
    third_party_rows: list[dict] | None = None,
    hotbed: dict | None = None,
    currencies: dict | None = None,
) -> dict:
    """The figures of a PDMS proposal, as the Word document and the Excel overview both show them
    (see build_pdms for the arguments): labour, equipment, consumables, transport, the Hotbed line,
    the four totals and the rates used. Every line also keeps its own price, currency and CBAR rate."""
    cfg = pdms_defaults()
    defaults = {**cfg["category_currencies"], **(currencies or {})}
    labour = []
    for r in list(support_rows) + list(other_rows):
        hours = _num(r.get("hours_per_shift"))
        if not hours or hours <= 0:
            raise ValueError(f"Manpower line {r.get('description')!r} has no shift length "
                             "(the hours at the end of its Encoding).")
        # The Generator stores hours and an hourly rate; the proposal shows days and a day rate.
        qty_hours, men = float(r["quantity"]), float(r["num_employees"])
        cur = _currency_of(r, "manpower", defaults)
        fx = _to_azn(cur, rates)
        hourly = float(r["rate_azn"]) * fx
        labour.append({"description": _labour_description(r), "nationality": r.get("nationality", ""),
                       "days": qty_hours / hours, "men": men, "rate": hourly * hours,
                       "total": _cents(men * qty_hours * hourly),
                       "native": float(r["rate_azn"]) * hours, "cur": cur, "fx": fx,
                       "encoding": str(r.get("encoding") or "")})
    equipment, consumables = _unit_prices(equip_rows, consump_rows, rates, markup_rate, defaults)
    transport = _transport_lines(third_party_rows, rates, defaults)
    hotbed_total = 0.0
    if hotbed and hotbed.get("rate"):      # the rate is shown in manats, like the other day rates
        hcur = (hotbed.get("currency") or defaults["manpower"]).upper()
        hfx = _to_azn(hcur, rates)
        hotbed = {**hotbed, "native": hotbed["rate"], "cur": hcur, "fx": hfx, "rate": hotbed["rate"] * hfx}
    if hotbed and hotbed.get("rate") and hotbed.get("days"):
        hotbed_total = _cents(hotbed["rate"] * hotbed["days"])

    labour_total = sum(r["total"] for r in labour) + hotbed_total
    equip_total = sum(r["total"] for r in equipment)
    cons_total = sum(r["total"] for r in consumables) + sum(r["total"] for r in transport)
    grand_total = labour_total + equip_total + cons_total

    location = "Onshore" if "onshore" in (header.get("project_type") or "").lower() else "Offshore"


    return {"labour": labour, "equipment": equipment, "consumables": consumables, "transport": transport,
            "hotbed": hotbed, "hotbed_total": hotbed_total, "labour_total": labour_total,
            "equip_total": equip_total, "cons_total": cons_total, "grand_total": grand_total,
            "location": location, "markup_rate": markup_rate, "defaults": defaults, "rates": dict(rates)}



def build_pdms(
    support_rows: list[dict],
    other_rows: list[dict],
    equip_rows: list[dict],
    consump_rows: list[dict],
    template_path: str | Path,
    output_dir: str | Path,
    header: dict,
    rates: dict,
    markup_rate: float,
    third_party_rows: list[dict] | None = None,
    hotbed: dict | None = None,
    currencies: dict | None = None,
) -> Path:
    """
    Write the PDMS proposal into a copy of the template and return its path.

    Row dicts are the ones CTRWorker already gets (see build_azn / build_usd).
    header keys: prp_number, date, customer_name, customer_city, attention_name,
        salutation_name, scope (job title), project_type (Offshore/Onshore),
        signer_name, signer_title. Missing optional values fall back to the
        "pdms" block of template_config.json.
    rates: {currency code: AZN per 1 unit} (CBAR; provisional, MA202). A price is in its line's
        own "currency" key, else in its category's default (`currencies`, over the "pdms"
        block's: manpower AZN, equipment USD, consumables USD, transport AZN); it is converted to
        AZN first, then the mark-up is added, and line totals are rounded to 2 decimals.
    hotbed: {"rate", "days"[, "currency"]} or None (= left out). With both a rate and days it is
        calculated (rate × days, counted in the labour total); with one or both None the line is
        inserted with just what is given and no total.
    markup_rate: consumables mark-up as a fraction (the USD CTR's).
    third_party_rows: the transport lines (as for build_azn); they are listed at the end of the
        consumables table with their cost only, and counted in that table's total.
    """
    template_path, output_dir = Path(template_path), Path(output_dir)
    if not template_path.is_file():
        raise ValueError(f"PDMS Template not found: {template_path}")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Cannot create output folder {output_dir}: {e}") from e

    cfg = pdms_defaults()
    sym, currency = cfg["currency_symbol"], cfg["currency"]
    defaults = {**cfg["category_currencies"], **(currencies or {})}
    doc = docx.Document(str(template_path))

    d = compute_pdms(support_rows, other_rows, equip_rows, consump_rows, header, rates, markup_rate,
                     third_party_rows, hotbed, currencies)
    labour, equipment, consumables, transport = d["labour"], d["equipment"], d["consumables"], d["transport"]
    hotbed, hotbed_total = d["hotbed"], d["hotbed_total"]
    labour_total, equip_total, cons_total, grand_total = (
        d["labour_total"], d["equip_total"], d["cons_total"], d["grand_total"])
    location = d["location"]

    # ── table numbering and the reference sentence ──
    has_cons = bool(consumables or transport)       # transport sits in the consumables table
    present = [("LABOUR", True), ("EQUIPMENT", bool(equipment)), ("CONSUMABLES", has_cons)]
    numbers, n = {}, 0
    for name, used in present:
        if used:
            n += 1
            numbers[name] = n
    refs = [f"Table № {i}" for i in numbers.values()]
    tables_ref = refs[0] if len(refs) == 1 else ", ".join(refs[:-1]) + " and " + refs[-1]

    attention = header.get("attention_name", "")
    values = {
        "{{PRP_NUMBER}}":      header["prp_number"],
        "{{DATE}}":            letter_date(header.get("date", "")),
        "{{CUSTOMER_NAME}}":   header.get("customer_name") or cfg["customer_name"],
        "{{CUSTOMER_CITY}}":   header.get("customer_city") or cfg["customer_city"],
        "{{ATTENTION_NAME}}":  attention,
        "{{SALUTATION_NAME}}": header.get("salutation_name") or salutation_from_attention(attention),
        "{{JOB_TITLE}}":       header.get("scope", ""),
        "{{SIGNER_NAME}}":     header.get("signer_name") or cfg["signer_name"],
        "{{SIGNER_TITLE}}":    header.get("signer_title") or cfg["signer_title"],
        "{{LOCATION}}":        location,
        "{{CURRENCY}}":        currency,
        "{{GRAND_TOTAL}}":     f"{grand_total:,.2f}",
        "{{TABLES_REFERENCE}}": tables_ref,
        "{{NO_LABOUR}}":       str(numbers.get("LABOUR", "")),
        "{{NO_EQUIPMENT}}":    str(numbers.get("EQUIPMENT", "")),
        "{{NO_CONSUMABLES}}":  str(numbers.get("CONSUMABLES", "")),
    }

    # ── optional blocks (before the tables, so empty ones leave nothing) ──
    for name, rows in (("EQUIPMENT", equipment), ("CONSUMABLES", has_cons)):
        if rows:
            _drop_markers(doc, name)
        else:
            _remove_block(doc, name)

    # ── tables: the template holds the sample's own tables; rows are copies of its sample rows ──
    labour_t = _find_table(doc, "{{L_DESC}}")
    _fill_rows(labour_t, "{{L_DESC}}", [
        {"{{L_DESC}}": r["description"], "{{L_NAT}}": r["nationality"], "{{L_LOC}}": location,
         "{{L_DAYS}}": _qty(r["days"]), "{{L_MEN}}": _qty(r["men"]),
         "{{L_RATE}}": _money(r["rate"], sym), "{{L_TOTAL}}": _money(r["total"], sym)}
        for r in labour])
    if hotbed:      # always inserted; calculated only when both the rate and the days are given
        both = bool(hotbed.get("rate") and hotbed.get("days"))
        _fill_row(labour_t, _row_with(labour_t, "{{HOTBED_LABEL}}")._tr, {
            "{{HOTBED_LABEL}}": cfg["hotbed_label"],
            "{{HOTBED_DAYS}}": _qty(hotbed["days"]) if hotbed.get("days") else "",
            "{{HOTBED_RATE}}": _money(hotbed["rate"], sym) if hotbed.get("rate") else "",
            "{{HOTBED_TOTAL}}": _money(hotbed_total, sym) if both else ""})
    else:
        _drop_row(labour_t, "{{HOTBED_LABEL}}")
    _fill_row(labour_t, _row_with(labour_t, "{{L_SUM}}")._tr, {"{{L_SUM}}": _money(labour_total, sym)})

    if equipment:
        t = _find_table(doc, "{{E_DESC}}")
        _fill_rows(t, "{{E_DESC}}", [
            {"{{E_DESC}}": r["description"], "{{E_QTY}}": _qty(r["qty"]),
             "{{E_DURATION}}": f"{_qty(r['days'])} Days", "{{E_UOM}}": r["uom"],
             "{{E_UNIT}}": _money(r["unit"], sym), "{{E_TOTAL}}": _money(r["total"], sym)}
            for r in equipment])
        _fill_row(t, _row_with(t, "{{E_SUM}}")._tr, {"{{E_SUM}}": _money(equip_total, sym)})

    if has_cons:
        t = _find_table(doc, "{{C_DESC}}")
        _fill_rows(t, "{{C_DESC}}", [
            {"{{C_DESC}}": r["description"], "{{C_UOM}}": r["uom"], "{{C_QTY}}": _qty(r["qty"]),
             "{{C_UNIT}}": _money(r["unit"], sym), "{{C_TOTAL}}": _money(r["total"], sym)}
            for r in consumables
        ] + [   # transport: the same row look, description and cost only
            {"{{C_DESC}}": r["description"], "{{C_UOM}}": "", "{{C_QTY}}": "",
             "{{C_UNIT}}": "", "{{C_TOTAL}}": _money(r["total"], sym)}
            for r in transport])
        _fill_row(t, _row_with(t, "{{C_SUM}}")._tr, {"{{C_SUM}}": _money(cons_total, sym)})

    summary_t = _find_table(doc, "{{S_GRAND}}")
    if not equipment:
        _drop_row(summary_t, "{{S_MATERIAL}}")
    if not has_cons:
        _drop_row(summary_t, "{{S_CONSUMABLES}}")
    for r in summary_t.rows:
        _fill_row(summary_t, r._tr, {
            "{{S_LABOUR}}": _money(labour_total, sym), "{{S_MATERIAL}}": _money(equip_total, sym),
            "{{S_CONSUMABLES}}": _money(cons_total, sym), "{{S_GRAND}}": _money(grand_total, sym)})

    # ── text tags (body, headers, footers) ──
    for par in _all_paragraphs(doc):
        _replace_in_paragraph(par, values)

    leftover = sorted({t for par in _all_paragraphs(doc) for t in _TAG.findall(par.text)})
    if leftover:
        raise ValueError("PDMS template has tags this version cannot fill: " + ", ".join(leftover))

    out_path = output_dir / pdms_output_filename(header["prp_number"], header.get("scope", ""))
    doc.save(str(out_path))
    log.info("PDMS proposal written: %s (labour=%d equipment=%d consumables=%d transport=%d total=%.2f)",
             out_path, len(labour), len(equipment), len(consumables), len(transport), grand_total)
    return out_path
