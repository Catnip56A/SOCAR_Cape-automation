"""Cost statistics for the MR vs CTR "Statistics" tab (task MA131).

Pure pandas, no Qt, and no import of app.py: the caller classifies each row's
rechargeability from the *documents* (MR's Rechargeable column, CTR's Rate
column) and hands the result in. SAGE is used only for the expected unit cost
(`local_expect_cost`). How rechargeability is decided is deliberately left to
the caller — the department may want a different source later.

Input rows (one per MR or CTR line), columns:
    key      Stock Code, any case/spacing
    doc      source file name
    sheet    source sheet name
    side     "MR" or "CTR"
    qty      number, or blank / text when the document has none
    status   "RECHARGE", "NONRECHARG", or None/blank when the document
             does not say
    section  CTR rows only: the Pricing section the row came from
             ("Plant & Equipment" / "Consumables & Materials")

Buckets: Rechargeable, Non-Rechargeable, Unknown. A row is Unknown when its
document gives no rechargeability. A row whose Stock Code is not in SAGE has no
cost at all; it is counted separately (`no_cost`) and never in a percentage.
Percentages are shares of the costed total, so when Unknown cost exists the
Rechargeable % and Non-Rechargeable % do not add up to 100.
"""
from __future__ import annotations

import pandas as pd

RECHARGEABLE = "Rechargeable"
NON_RECHARGEABLE = "Non-Rechargeable"
UNKNOWN = "Unknown"
BUCKETS = (RECHARGEABLE, NON_RECHARGEABLE, UNKNOWN)

EQUIPMENT = "Equipment"
CONSUMABLE = "Consumable"
UNCATEGORISED = "Uncategorised"

TOTAL_LABEL = "Total"

# Why a row was left out of the totals.
NO_QTY = "No quantity"
NO_SAGE = "Not in SAGE"

SOURCE_SAGE = "SAGE"
SOURCE_MANUAL = "Manual"

ROW_COLUMNS = ["key", "doc", "sheet", "side", "category", "bucket", "qty",
               "unit_cost", "source", "cost", "problem"]


def norm_key(v) -> str:
    s = str(v).strip().upper()
    return "" if s in ("NAN", "NONE") else s


def sage_cost_map(sage_df: pd.DataFrame | None) -> dict[str, float]:
    """{STOCK CODE: local_expect_cost}. A product can have many SAGE rows
    (different warehouses and orders); the first one wins, the same rule the
    CTR Generator's lookup uses, so both tabs cost an item identically."""
    if sage_df is None or sage_df.empty:
        return {}
    keys = sage_df["product"].map(norm_key)
    costs = pd.to_numeric(sage_df["local_expect_cost"], errors="coerce")
    out: dict[str, float] = {}
    for k, c in zip(keys.tolist(), costs.tolist()):
        if k and k not in out and pd.notna(c):
            out[k] = float(c)
    return out


def section_category(section) -> str:
    """CTR Pricing section name -> Equipment / Consumable / Uncategorised."""
    s = str(section).strip().lower()
    if s.startswith("plant"):
        return EQUIPMENT
    if s.startswith("consumable"):
        return CONSUMABLE
    return UNCATEGORISED


def ctr_category_map(items: pd.DataFrame) -> dict[str, str]:
    """{STOCK CODE: category} from the CTR rows alone. A code that appears in
    both an equipment and a consumables section is ambiguous and stays
    Uncategorised rather than being guessed."""
    out: dict[str, str] = {}
    ctr = items[items["side"] == "CTR"]
    if ctr.empty or "section" not in ctr.columns:
        return out
    seen: dict[str, set[str]] = {}
    for k, sec in zip(ctr["key"].map(norm_key).tolist(), ctr["section"].tolist()):
        cat = section_category(sec)
        if k and cat != UNCATEGORISED:
            seen.setdefault(k, set()).add(cat)
    for k, cats in seen.items():
        out[k] = next(iter(cats)) if len(cats) == 1 else UNCATEGORISED
    return out


def _bucket(status) -> str:
    s = "" if status is None else str(status).strip().upper()
    if s == "RECHARGE":
        return RECHARGEABLE
    if s == "NONRECHARG":
        return NON_RECHARGEABLE
    return UNKNOWN


def cost_rows(items: pd.DataFrame, cost_map: dict[str, float],
              use_qty: bool = True,
              manual: dict[str, float] | None = None) -> pd.DataFrame:
    """One output row per input row: category, bucket, unit cost (+ where it
    came from), cost and, when the row cannot be costed, `problem`
    (NO_QTY / NO_SAGE). See recost() for how cost and problem are decided.
    `manual` = unit costs typed in by the user for codes SAGE lacks."""
    if items.empty:
        return pd.DataFrame(columns=ROW_COLUMNS)

    keys = items["key"].map(norm_key)
    qty = pd.to_numeric(items["qty"], errors="coerce")
    unit_cost = keys.map(cost_map)                       # NaN when not in SAGE
    cat_map = ctr_category_map(items.assign(key=keys))
    category = keys.map(cat_map).fillna(UNCATEGORISED)

    rows = pd.DataFrame({
        "key": keys,
        "doc": items["doc"],
        "sheet": items["sheet"] if "sheet" in items.columns else "",
        "side": items["side"],
        "category": category,
        "bucket": items["status"].map(_bucket),
        "qty": qty,
        "unit_cost": unit_cost,
        "source": unit_cost.map(lambda v: SOURCE_SAGE if pd.notna(v) else ""),
    })
    return apply_manual(rows, manual or {}, use_qty)


def apply_manual(rows: pd.DataFrame, manual: dict[str, float],
                 use_qty: bool = True) -> pd.DataFrame:
    """Fill the unit cost of rows SAGE does not know from `manual`
    ({STOCK CODE: unit cost}), then recost. SAGE always wins over a manual
    value, so once SAGE gains the code the typed-in number stops applying."""
    rows = rows.copy()
    if len(rows):
        # Reset to what SAGE said before applying (idempotent on repeat calls).
        manual_mask = rows["source"] == SOURCE_MANUAL
        rows.loc[manual_mask, "unit_cost"] = float("nan")
        rows.loc[manual_mask, "source"] = ""
        if manual:
            fill = (rows["source"] == "") & rows["key"].isin(manual.keys())
            rows.loc[fill, "unit_cost"] = rows.loc[fill, "key"].map(manual)
            rows.loc[fill, "source"] = SOURCE_MANUAL
    return recost(rows, use_qty)


def recost(rows: pd.DataFrame, use_qty: bool = True) -> pd.DataFrame:
    """(Re)compute `cost` and `problem` from unit_cost and qty.

    use_qty=True : cost = unit cost x quantity; a row without a numeric
                   quantity is not costed (problem = NO_QTY).
    use_qty=False: cost = unit cost alone; quantity is not needed.
    A row with no unit cost is never costed (problem = NO_SAGE)."""
    rows = rows.copy()
    problem = pd.Series("", index=rows.index, dtype=object)
    problem[rows["unit_cost"].isna()] = NO_SAGE
    if use_qty:
        problem[(problem == "") & rows["qty"].isna()] = NO_QTY
    cost = rows["unit_cost"] * rows["qty"] if use_qty else rows["unit_cost"].astype(float)
    rows["cost"] = cost.where(problem == "")
    rows["problem"] = problem
    return rows[ROW_COLUMNS]


MISSING_QTY_COLUMNS = ["side", "doc", "sheet", "key"]


def missing_qty(items: pd.DataFrame) -> pd.DataFrame:
    """The input rows that have no numeric quantity — they cannot be costed
    when cost is multiplied by quantity, so the MR vs CTR tab lists them in
    Needs Review. Independent of SAGE and of the quantity toggle."""
    if items.empty:
        return pd.DataFrame(columns=MISSING_QTY_COLUMNS)
    bad = pd.to_numeric(items["qty"], errors="coerce").isna()
    out = items.loc[bad, ["side", "doc", "sheet", "key"]].copy()
    out["key"] = out["key"].map(norm_key)
    return out.sort_values(["side", "doc", "sheet", "key"]).reset_index(drop=True)


def filter_rows(rows: pd.DataFrame, category: str | None = None,
                bucket: str | None = None) -> pd.DataFrame:
    """Category / bucket filter used by the Statistics detail lists."""
    out = rows
    if category:
        out = out[out["category"] == category]
    if bucket:
        out = out[out["bucket"] == bucket]
    return out


def summarize(rows: pd.DataFrame, by: list[str] | None = None) -> pd.DataFrame:
    """Totals per group (default: per `side` and `doc`) plus one TOTAL_LABEL row
    per side. Columns: the group columns, then Rechargeable / Non-Rechargeable /
    Unknown cost, Total cost, a `%` column for each bucket (None when the group
    has no cost), `Non/Rech` (Non-Rechargeable cost ÷ Rechargeable cost — money,
    so it follows the quantity switch; None when there is no rechargeable cost),
    `items` (rows costed), `no_cost` (rows left out) and `no_qty` (the subset of
    those lacking a quantity)."""
    by = by or ["side", "doc"]
    cols = by + list(BUCKETS) + ["Total cost"] + [f"{b} %" for b in BUCKETS] + \
        ["Non/Rech", "items", "no_cost", "no_qty"]
    if rows.empty:
        return pd.DataFrame(columns=cols)

    def one(g: pd.DataFrame) -> dict:
        costed = g[g["problem"] == ""]
        per = costed.groupby("bucket")["cost"].sum()
        vals = {b: float(per.get(b, 0.0)) for b in BUCKETS}
        total = sum(vals.values())
        out = dict(vals)
        out["Total cost"] = total
        for b in BUCKETS:
            out[f"{b} %"] = (vals[b] / total * 100.0) if total else None
        out["Non/Rech"] = (vals[NON_RECHARGEABLE] / vals[RECHARGEABLE]) if vals[RECHARGEABLE] else None
        out["items"] = int(len(costed))
        out["no_cost"] = int((g["problem"] != "").sum())
        out["no_qty"] = int((g["problem"] == NO_QTY).sum())
        return out

    recs = []
    for keys_, g in rows.groupby(by, sort=True):
        keys_ = keys_ if isinstance(keys_, tuple) else (keys_,)
        recs.append({**dict(zip(by, keys_)), **one(g)})
    # TOTAL row per side: every document of that side that is in `rows`
    # (the caller passes only the documents the filter keeps).
    if "side" in by:
        rest = [c for c in by if c != "side"]
        for side, g in rows.groupby("side", sort=True):
            tot = {c: TOTAL_LABEL for c in rest}
            recs.append({"side": side, **tot, **one(g)})
    return pd.DataFrame(recs)[cols]


# ── saving / reporting ───────────────────────────────────────────────────────

BASE_COLUMNS = ["key", "doc", "sheet", "side", "category", "bucket", "qty",
                "unit_cost", "source"]


def rows_to_records(rows: pd.DataFrame) -> list[dict]:
    """Costed rows -> JSON-friendly records (NaN becomes None). Only the base
    columns are kept; cost and problem are recomputed on load."""
    base = rows[BASE_COLUMNS].astype(object)
    return base.where(base.notna(), None).to_dict(orient="records")


def rows_from_records(records: list[dict], use_qty: bool = True) -> pd.DataFrame:
    df = pd.DataFrame(records, columns=BASE_COLUMNS)
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    df["unit_cost"] = pd.to_numeric(df["unit_cost"], errors="coerce")
    df["source"] = df["source"].fillna("")
    return recost(df, use_qty)


def report_frames(rows: pd.DataFrame, use_qty: bool = True,
                  manual: dict[str, float] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(summary, items) for the Excel report — every row, no document or type
    filter applied. `summary` has one line per document and a total per side."""
    rows = apply_manual(rows, manual or {}, use_qty)
    basis = "unit cost × quantity" if use_qty else "unit cost only"
    s = summarize(rows)
    summary = pd.DataFrame({
        "Side": s["side"],
        "Document": s["doc"].where(s["doc"] != TOTAL_LABEL, "ALL DOCUMENTS"),
        "Rechargeable cost": s[RECHARGEABLE],
        "Rechargeable %": s[f"{RECHARGEABLE} %"],
        "Non-Rechargeable cost": s[NON_RECHARGEABLE],
        "Non-Rechargeable %": s[f"{NON_RECHARGEABLE} %"],
        "Unknown cost": s[UNKNOWN],
        "Unknown %": s[f"{UNKNOWN} %"],
        "Total cost": s["Total cost"],
        "Non-Rechargeable ÷ Rechargeable (cost)": s["Non/Rech"],
        "Items counted": s["items"],
        "Items not counted": s["no_cost"],
        "…of which no quantity": s["no_qty"],
        "Cost basis": basis,
    })
    items = pd.DataFrame({
        "Side": rows["side"], "Document": rows["doc"], "Sheet": rows["sheet"],
        "Stock Code": rows["key"], "Type": rows["category"],
        "Rechargeability": rows["bucket"], "Qty": rows["qty"],
        "Unit cost (SAGE expected)": rows["unit_cost"], "Unit cost source": rows["source"],
        "Cost": rows["cost"], "Not counted because": rows["problem"],
    }).sort_values(["Side", "Document", "Cost"], ascending=[True, True, False],
                   na_position="last", kind="mergesort").reset_index(drop=True)
    return summary, items
