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
A row whose document says Rechargeable (or Non-Rechargeable) while SAGE's own
tag says the opposite is a *disparity* (DISPARITY): it is not costed either
and is listed for the user to look at. SAGE's tag comes from `sage_tag_map`.
The user resolves a disparity per Stock Code by choosing which source is right
(`choices`: {STOCK CODE: "SAGE" | "DOC"}): "SAGE" makes the row use SAGE's
rechargeability, "DOC" keeps the document's; either way it is no longer a
disparity and the row is counted.
Percentages are shares of the costed total, so when Unknown cost exists the
Rechargeable % and Non-Rechargeable % do not add up to 100.

Two views of the same rows. The *cost* view (above) values a row at SAGE's
expected unit cost. The *price* view (`price_view`) values it at what the
documents bill: quantity × rate (× days for equipment and scaffold), where the
rate is looked up the way the CTR Generator does it — equipment from the USD
Pricebook (through the Equipment Names bridge), consumables from SAGE, no
fallback between the two. A Non-Rechargeable row is billed at 0. Consumables can
carry the Generator's markup. Nothing is assumed: a row without a quantity, a
rate or (for equipment) days is left out and says why.
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
DISPARITY = "Rechargeability differs from SAGE"
NO_RATE = "No rate"
NO_DAYS = "No days"
NO_TYPE = "Type unknown"
TYPE_CONFLICT = "Type needs a decision"

SCAFFOLD = "Scaffold"

SOURCE_SAGE = "SAGE"
SOURCE_MANUAL = "Manual"

ROW_COLUMNS = ["key", "doc", "sheet", "side", "category", "bucket", "doc_bucket", "qty",
               "unit_cost", "source", "sage_bucket", "choice", "name", "cost", "problem",
               "description", "kind", "rate", "days", "scaffold", "own_rate",
               "equip_rate", "type_status", "type_note", "list_type", "section_type", "days_note", "days_options"]

CHOICE_SAGE = "SAGE"
CHOICE_DOC = "DOC"


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


def sage_tag_map(sage_df: pd.DataFrame | None) -> dict[str, str]:
    """{STOCK CODE: RECHARGEABLE | NON_RECHARGEABLE} from SAGE's own tag (the
    `non_recharge` flag parse_sage derives from analysis_b). First row per
    product wins — the same rule as sage_cost_map."""
    if sage_df is None or sage_df.empty or "non_recharge" not in sage_df.columns:
        return {}
    out: dict[str, str] = {}
    for k, nr in zip(sage_df["product"].map(norm_key).tolist(), sage_df["non_recharge"].tolist()):
        if k and k not in out:
            out[k] = NON_RECHARGEABLE if bool(nr) else RECHARGEABLE
    return out


def sage_name_map(sage_df: pd.DataFrame | None) -> dict[str, str]:
    """{STOCK CODE: SAGE's long_description} — the item's name. First row per
    product wins, like the cost and tag maps."""
    if sage_df is None or sage_df.empty or "long_description" not in sage_df.columns:
        return {}
    out: dict[str, str] = {}
    for k, n in zip(sage_df["product"].map(norm_key).tolist(), sage_df["long_description"].tolist()):
        if k and k not in out and isinstance(n, str) and n.strip():
            out[k] = n.strip()
    return out


def equip_rate_map(keys, names_df: pd.DataFrame | None,
                   usd_df: pd.DataFrame | None) -> dict[str, float]:
    """{STOCK CODE: equipment rate per day} for the codes the CTR Generator
    would price from the USD Pricebook — the same lookup as its equipment
    matching: the Equipment Names bridge (`names_df`: product, non_recharge,
    legacy_name, canonical_name) turns a request stock code into the pricebook
    description (a bridge code tagged non-recharge is billed at 0 without any
    pricebook lookup); a code not in the bridge is looked up in the pricebook by
    that text directly. The pricebook (`usd_df`: stock_code, supplier_desc,
    unit_price) is searched by description, case-insensitively, and where both a
    normal and a standby (`STBY`) row exist the normal one is used. A code that
    cannot be resolved is simply absent from the result — no rate is assumed."""
    out: dict[str, float] = {}
    pb: dict[str, list[tuple[str, float]]] = {}      # empty pricebook: the bridge can still say "billed at 0"
    if usd_df is not None and not usd_df.empty:
        for desc, code, price in zip(usd_df["supplier_desc"].astype(str).tolist(),
                                     usd_df["stock_code"].astype(str).tolist(),
                                     pd.to_numeric(usd_df["unit_price"], errors="coerce").tolist()):
            if pd.notna(price):
                pb.setdefault(desc.strip().lower(), []).append((code.upper(), float(price)))
    bridge: dict[str, tuple[bool, str]] = {}
    if names_df is not None and not names_df.empty:
        for prod, nr, canon, legacy in zip(
                names_df["product"].astype(str).tolist(), names_df["non_recharge"].tolist(),
                names_df["canonical_name"].tolist(), names_df["legacy_name"].tolist()):
            k = norm_key(prod)
            if k and k not in bridge:
                text = canon if isinstance(canon, str) and canon.strip() else legacy
                bridge[k] = (bool(nr), text.strip() if isinstance(text, str) else "")
    for raw in set(keys):
        k = norm_key(raw)
        if not k:
            continue
        if k in bridge:
            nr, desc = bridge[k]
            if nr:
                out[k] = 0.0
                continue
        else:
            desc = str(raw).strip()
        hits = pb.get(desc.strip().lower())
        if not hits:
            continue
        normal = [h for h in hits if "STBY" not in h[0]]
        out[k] = (normal or hits)[0][1]
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


def _as_bool(series: pd.Series) -> pd.Series:
    return series.map(lambda v: bool(v) if pd.notna(v) else False).astype(bool)


def _opt(items: pd.DataFrame, name: str, default):
    """An optional input column, or `default` for every row when absent."""
    return items[name] if name in items.columns else pd.Series(default, index=items.index)


def cost_rows(items: pd.DataFrame, cost_map: dict[str, float],
              use_qty: bool = True,
              manual: dict[str, float] | None = None,
              sage_tags: dict[str, str] | None = None,
              sage_names: dict[str, str] | None = None,
              equip_rates: dict[str, float] | None = None) -> pd.DataFrame:
    """One output row per input row: category, bucket, unit cost (+ where it
    came from), SAGE's own rechargeability (`sage_bucket`, "" when unknown) and,
    when the row cannot be costed, `problem` (NO_SAGE / DISPARITY / NO_QTY). See
    recost() for how cost and problem are decided.
    `manual` = unit costs typed in by the user for codes SAGE lacks;
    `sage_tags` = sage_tag_map(), to find rows whose document disagrees with SAGE;
    `sage_names` = sage_name_map(), so lists can show the item's name next to its code;
    `equip_rates` = equip_rate_map(), the USD Pricebook rate of equipment codes
    (kept in `equip_rate`; only the price view uses it, for rows whose kind is Equipment).
    Optional input columns: `description` (the line's own text), `rate` (the
    rate the line already carries, e.g. the Generator's rate cell), `days`, and
    `scaffold` (True for a scaffold line)."""
    if items.empty:
        return pd.DataFrame(columns=ROW_COLUMNS)

    keys = items["key"].map(norm_key)
    qty = pd.to_numeric(items["qty"], errors="coerce")
    unit_cost = keys.map(cost_map)                       # NaN when not in SAGE
    cat_map = ctr_category_map(items.assign(key=keys))
    category = keys.map(cat_map).fillna(UNCATEGORISED)
    scaffold = _as_bool(_opt(items, "scaffold", False))
    # What kind of line it is: the CTR section says (Equipment / Consumable); a code no CTR
    # section names stays "" here. The MR vs CTR statistics then decide the kind from the
    # Stock Code types list (see apply_types), the CTR Generator's own tables already know it.
    kind = category.where(category != UNCATEGORISED, "").mask(scaffold, SCAFFOLD)
    rate_in = pd.to_numeric(_opt(items, "rate", float("nan")), errors="coerce")
    equip_rate = keys.map(equip_rates or {})            # the USD Pricebook rate, whatever the kind

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
        "sage_bucket": keys.map(sage_tags or {}).fillna(""),
        "name": keys.map(sage_names or {}).fillna(""),
        "description": _opt(items, "description", "").fillna("").astype(str).str.strip(),
        "kind": kind,
        "rate": rate_in,
        "days": pd.to_numeric(_opt(items, "days", float("nan")), errors="coerce"),
        "scaffold": scaffold,
        "own_rate": "rate" in items.columns,       # the line carries its own rate cell (the Generator's)
        "equip_rate": equip_rate,
        "days_note": _opt(items, "days_note", "").fillna("").astype(str),
        "days_options": _opt(items, "days_options", "").fillna("").astype(str),
    })
    return apply_manual(rows, manual or {}, use_qty)


def apply_types(rows: pd.DataFrame, stock_types: dict | None) -> pd.DataFrame:
    """Decide each row's kind (Equipment / Consumable) from the Stock Code types list
    (`stock_types` = ctr_tools.stockcode_types.current_types(): {"equipment": set, "consumable": set,
    "confirmed": {code: kind}}); None leaves the kinds the rows came with (the CTR Generator's own
    tables know theirs).

    Order: a kind the user confirmed; else the list (Equipment wins for a code in both lists); else
    the CTR section the code is listed under; else unknown. The result is a *proposal* when the
    sources disagree (the list says one thing and the CTR section another, or the code is in both
    lists and no CTR section confirms Equipment) and the user has not confirmed it: `type_status` is "conflict" and the row is not priced
    until they accept or choose. A code nothing knows is "unknown" and is not priced either.
    `category` follows the result, so the Type column and filter use it too."""
    if stock_types is None or rows.empty:
        return rows
    rows = rows.copy()
    eq, cons, conf = stock_types["equipment"], stock_types["consumable"], stock_types.get("confirmed", {})
    key = rows["key"]
    in_eq, in_cons = key.isin(eq), key.isin(cons)
    list_type = pd.Series("", index=rows.index, dtype=object)
    list_type[in_cons] = CONSUMABLE
    list_type[in_eq] = EQUIPMENT                                 # equipment wins over the consumables list
    section = rows["category"].where(rows["category"].isin([EQUIPMENT, CONSUMABLE]), "")
    confirmed = key.map(conf).fillna("")
    proposed = list_type.where(list_type != "", section)
    kind = confirmed.where(confirmed != "", proposed)
    # A code in both lists is proposed as Equipment; when its CTR section also says Equipment that is
    # backed up and nobody needs to be asked. Anything else that disagrees (or nothing backs the
    # proposal up) is a conflict for the user.
    both = in_eq & in_cons & (section != EQUIPMENT)
    clash = (list_type != "") & (section != "") & (list_type != section)
    conflict = (confirmed == "") & (both | clash)
    status = pd.Series("", index=rows.index, dtype=object)
    status[kind == ""] = "unknown"
    status[conflict] = "conflict"
    note = pd.Series("", index=rows.index, dtype=object)
    note[both & (confirmed == "")] = "in both lists — Equipment proposed"
    note[clash & (confirmed == "")] = ("list says " + list_type + " · CTR section says " + section)[clash & (confirmed == "")]
    scaffold = rows["scaffold"]
    rows["kind"] = kind.mask(scaffold, SCAFFOLD)
    rows["type_status"] = status.mask(scaffold, "")
    rows["type_note"] = note.mask(scaffold, "")
    rows["list_type"] = list_type
    rows["section_type"] = section
    rows["category"] = kind.where(kind != "", UNCATEGORISED)
    return rows


def how_to_fix(problem: str, kind: str = "", note: str = "") -> str:
    """One sentence saying what to do about a row that cannot be priced / costed."""
    if problem == NO_TYPE:
        return ("Add this Stock Code to Settings → Stock Code types (Edit…) as Equipment or Consumable, "
                "or choose it with the “Stock Code types” button in the statistics.")
    if problem == TYPE_CONFLICT:
        return (f"The sources disagree ({note}). Press “Stock Code types” in the statistics and accept "
                "the proposal or choose Equipment / Consumable.")
    if problem == NO_RATE:
        if kind == EQUIPMENT:
            return ("The equipment rate comes from the USD Pricebook: add this item to the USD Pricebook "
                    "(its description, or its code through the Combined DB equipment-names sheet), make sure "
                    "the USD Pricebook and Combined DB are set above, and press Reload statistics.")
        if kind == CONSUMABLE:
            return ("The consumable price comes from SAGE: add this Stock Code with a local_expect_cost to "
                    "the SAGE Export and press Reload statistics (or type a unit cost in Document costs).")
        return "Set the Stock Code's type first; the rate source depends on it."
    if problem == NO_DAYS:
        return ("Double-click the (orange) Days cell and type the number of days the equipment is billed for. "
                "An MR line takes its days from the matching CTR row; this one has none to take.")
    if problem == NO_QTY:
        return "Fill in the quantity in the source document, then Compare again."
    if problem == NO_SAGE:
        return "Add the Stock Code to the SAGE Export and press Reload statistics, or type its unit cost."
    if problem == DISPARITY:
        return "Choose which source is right in the Disparities window."
    return ""


def kind_for(key: str, section_category: str, stock_types: dict | None) -> tuple[str, str]:
    """(kind, source) of one Stock Code, by the same order as apply_types: a kind the user confirmed,
    else the Stock Code types list (Equipment wins), else the CTR section, else ("", ""). `source` is
    "confirmed" / "list" / "CTR section" / "". Used for the Type column, filter and tooltips of the
    MR vs CTR tables."""
    section = section_category if section_category in (EQUIPMENT, CONSUMABLE) else ""
    if stock_types is not None:
        k = norm_key(key)
        conf = stock_types.get("confirmed", {}).get(k)
        if conf:
            return conf, "confirmed"
        if k in stock_types["equipment"]:
            return EQUIPMENT, "list"
        if k in stock_types["consumable"]:
            return CONSUMABLE, "list"
    return (section, "CTR section") if section else ("", "")


TYPE_ISSUE_COLUMNS = ["Stock Code", "Description", "List says", "CTR section says", "Proposed", "Status", "Why", "Lines"]


def type_issue_table(rows: pd.DataFrame) -> pd.DataFrame:
    """The Stock Codes whose Equipment / Consumable type needs the user: the sources disagree
    ("conflict": a proposal is shown) or nothing knows the code ("unknown"). One line per code,
    with its description so it is easy to decide. `rows` = apply_manual() output."""
    empty = pd.DataFrame(columns=TYPE_ISSUE_COLUMNS)
    if rows is None or rows.empty or "type_status" not in rows.columns:
        return empty
    bad = rows[rows["type_status"] != ""]
    if bad.empty:
        return empty
    text = bad["description"].where(bad["description"] != "", bad["name"])
    g = bad.assign(_text=text).groupby("key", sort=True).agg(
        d=("_text", lambda v: next((x for x in v if x), "")), lst=("list_type", "first"),
        sec=("section_type", "first"), kind=("kind", "first"), st=("type_status", "first"),
        why=("type_note", "first"), n=("key", "size")).reset_index()
    return pd.DataFrame({
        "Stock Code": g["key"], "Description": g["d"], "List says": g["lst"], "CTR section says": g["sec"],
        "Proposed": g["kind"], "Status": g["st"].map({"conflict": "Sources disagree", "unknown": "Unknown"}),
        "Why": g["why"].where(g["why"] != "", "in neither list and no CTR section lists it"),
        "Lines": g["n"]})


def apply_manual(rows: pd.DataFrame, manual: dict[str, float],
                 use_qty: bool = True,
                 choices: dict[str, str] | None = None,
                 stock_types: dict | None = None,
                 days: dict[str, float] | None = None) -> pd.DataFrame:
    """Fill the unit cost of rows SAGE does not know from `manual`
    ({STOCK CODE: unit cost}) and apply the user's source `choices`
    ({STOCK CODE: "SAGE" | "DOC"}), then recost. SAGE always wins over a manual
    value, so once SAGE gains the code the typed-in number stops applying.
    `rows` must be the rows as costed from the documents (their `bucket` is the
    document's); the original document value is kept in `doc_bucket`.
    `stock_types` decides each row's kind (see apply_types); `days` ({STOCK CODE: days}) are what the user
    typed or picked (see effective_days) — equipment is costed x days."""
    rows = apply_types(_ensure_columns(rows), stock_types)
    rows["days"] = effective_days(rows, days)
    rows["doc_bucket"] = rows["bucket"]
    rows["choice"] = ""
    both = (RECHARGEABLE, NON_RECHARGEABLE)
    if choices and "sage_bucket" in rows.columns:
        for key, ch in choices.items():
            m = (rows["key"] == key) & rows["sage_bucket"].isin(both)
            if ch == CHOICE_SAGE:
                rows.loc[m, "bucket"] = rows.loc[m, "sage_bucket"]
                rows.loc[m, "choice"] = CHOICE_SAGE
            elif ch == CHOICE_DOC:
                rows.loc[m, "choice"] = CHOICE_DOC
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


def _ensure_columns(rows: pd.DataFrame) -> pd.DataFrame:
    """Add the optional columns older / simpler frames lack, with their neutral values."""
    rows = rows.copy()
    if "sage_bucket" not in rows.columns:
        rows["sage_bucket"] = ""
    if "choice" not in rows.columns:
        rows["choice"] = ""
    if "name" not in rows.columns:
        rows["name"] = ""
    if "doc_bucket" not in rows.columns:
        rows["doc_bucket"] = rows["bucket"]
    for col, default in (("description", ""), ("kind", ""),
                         ("rate", float("nan")), ("days", float("nan")), ("scaffold", False),
                         ("own_rate", False), ("equip_rate", float("nan")), ("type_status", ""),
                         ("type_note", ""), ("list_type", ""), ("section_type", ""), ("days_note", ""),
                         ("days_options", "")):
        if col not in rows.columns:
            rows[col] = default
    for col in ("sage_bucket", "choice", "name", "description", "kind", "type_status", "type_note",
                "days_note", "days_options", "list_type", "section_type"):
        rows[col] = rows[col].fillna("")
    rows["scaffold"] = _as_bool(rows["scaffold"])
    rows["own_rate"] = _as_bool(rows["own_rate"])
    return rows


def effective_days(rows: pd.DataFrame, typed: dict[str, float] | None = None) -> pd.Series:
    """The days each row is billed for. Its own (a CTR line's Days, or an MR line's days taken from the
    matching CTR line) unless the user typed something:
      * `typed["CODE@MR"]` / `typed["CODE@CTR"]` — days typed for ONE side of a Stock Code (the lines of that
        side): they win over everything, the line's own days included;
      * `typed["CODE"]` — days for the Stock Code as a whole: they override days that were only taken from a
        CTR and fill in where there are none, but never replace a line's own days.
    NaN = not known yet."""
    typed = typed or {}
    whole = rows["key"].map(typed)
    derived = rows["days_note"].str.startswith("from")
    d = rows["days"].where(rows["days"].notna() & ~(derived & whole.notna()), whole)
    one_side = (rows["key"] + "@" + rows["side"]).map(typed)
    return one_side.where(one_side.notna(), d)


def recost(rows: pd.DataFrame, use_qty: bool = True) -> pd.DataFrame:
    """(Re)compute `cost` and `problem` from unit_cost and qty.

    use_qty=True : cost = unit cost x quantity, and x days for equipment and
                   scaffold (the same formula as the price view); a row without
                   a numeric quantity is not costed (problem = NO_QTY), nor is
                   equipment without days (NO_DAYS) or a line whose type is
                   unknown / undecided (NO_TYPE / TYPE_CONFLICT).
    use_qty=False: cost = unit cost alone; quantity, days and type are not needed.
    A row with no unit cost is never costed (problem = NO_SAGE). A row whose
    document rechargeability contradicts SAGE's tag is not costed either
    (problem = DISPARITY), whatever the quantity."""
    rows = _ensure_columns(rows)
    problem = pd.Series("", index=rows.index, dtype=object)
    problem[rows["unit_cost"].isna()] = NO_SAGE
    both = (RECHARGEABLE, NON_RECHARGEABLE)
    differs = (rows["bucket"].isin(both) & rows["sage_bucket"].isin(both)
               & (rows["bucket"] != rows["sage_bucket"]) & (rows["choice"] == ""))
    problem[(problem == "") & differs] = DISPARITY
    needs_days = rows["kind"].isin([EQUIPMENT, SCAFFOLD])
    if use_qty:
        # The type decides whether days apply, so a type that is unknown or still undecided is not costed.
        problem[(problem == "") & (rows["type_status"] == "unknown")] = NO_TYPE
        problem[(problem == "") & (rows["type_status"] == "conflict")] = TYPE_CONFLICT
        problem[(problem == "") & rows["qty"].isna()] = NO_QTY
        problem[(problem == "") & needs_days & rows["days"].isna()] = NO_DAYS
    cost = (rows["unit_cost"] * rows["qty"] * rows["days"].where(needs_days, 1.0)
            if use_qty else rows["unit_cost"].astype(float))
    rows["cost"] = cost.where(problem == "")
    rows["problem"] = problem
    return rows[ROW_COLUMNS]


def price_view(rows: pd.DataFrame, days: dict[str, float] | None = None,
               markup_rate: float | None = None,
               include_scaffold: bool = False) -> pd.DataFrame:
    """The price view of rows that have been through apply_manual(): the same
    columns, but `unit_cost` is the rate, `cost` is the price and `problem` says
    why a row has none, so summarize() and the lists work on it unchanged.

    price = quantity × rate, × days for equipment and scaffold, and × (1 +
    markup) for consumables when `markup_rate` is given (a fraction, e.g. 0.065).
    A Non-Rechargeable row is billed at 0. The rate is the one the row carries
    (the Generator's own rate cell), else — equipment — the USD Pricebook rate
    (`equip_rate`), else — consumables — SAGE's unit cost. `days` ({STOCK
    CODE: days}) fills the days a row does not carry. Scaffold lines are dropped
    unless `include_scaffold`. Unresolved disparities stay out (DISPARITY); a row
    whose type is unknown or still needs a decision, or with no quantity, rate or
    (equipment / scaffold) days, is left out with NO_TYPE / TYPE_CONFLICT / NO_QTY /
    NO_RATE / NO_DAYS — nothing is assumed."""
    out = rows if include_scaffold else rows[~rows["scaffold"]]
    out = out.copy()
    if out.empty:
        return out[ROW_COLUMNS]
    kind = out["kind"]
    # A line that carries its own rate cell is never priced from SAGE behind its back.
    sage_rate = out["unit_cost"].where((kind == CONSUMABLE) & ~out["own_rate"])
    pricebook_rate = out["equip_rate"].where((kind == EQUIPMENT) & ~out["own_rate"])
    rate = out["rate"].where(out["rate"].notna(), pricebook_rate.where(pricebook_rate.notna(), sage_rate))
    # Days: a line's own; an MR line's days taken from its matching CTR row ("from …") can be overridden by
    # what the user types; where there are none, what the user typed fills in.
    d = effective_days(out, days)
    needs_days = kind.isin([EQUIPMENT, SCAFFOLD])
    mult = out["qty"] * rate * d.where(needs_days, 1.0)
    if markup_rate:
        mult = mult.where(kind != CONSUMABLE, mult * (1.0 + markup_rate))
    non = out["bucket"] == NON_RECHARGEABLE
    problem = pd.Series("", index=out.index, dtype=object)
    problem[(out["problem"] == DISPARITY)] = DISPARITY
    ok = problem == ""
    problem[ok & ~non & (out["type_status"] == "unknown")] = NO_TYPE
    problem[ok & ~non & (out["type_status"] == "conflict")] = TYPE_CONFLICT
    ok = problem == ""
    problem[ok & ~non & out["qty"].isna()] = NO_QTY
    ok = problem == ""
    problem[ok & ~non & rate.isna()] = NO_RATE
    ok = problem == ""
    problem[ok & ~non & needs_days & d.isna()] = NO_DAYS
    out["unit_cost"] = rate
    out["days"] = d
    out["source"] = ""
    out["cost"] = mult.where(~non, 0.0).where(problem == "")
    out["problem"] = problem
    return out[ROW_COLUMNS]


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
    `items` (rows costed), `no_cost` (rows left out), `no_qty` (the subset of
    those lacking a quantity) and `disparity` (the subset whose rechargeability
    differs from SAGE)."""
    by = by or ["side", "doc"]
    cols = by + list(BUCKETS) + ["Total cost"] + [f"{b} %" for b in BUCKETS] + \
        ["Non/Rech", "items", "no_cost", "no_qty", "disparity"]
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
        out["disparity"] = int((g["problem"] == DISPARITY).sum())
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
                "unit_cost", "source", "sage_bucket", "name",
                "description", "kind", "rate", "days", "scaffold", "own_rate", "equip_rate", "days_note", "days_options"]

DISPARITY_COLUMNS = ["Side", "Document", "Stock Code", "Item", "Document says", "SAGE says", "Chosen", "Lines"]


def disparities(rows: pd.DataFrame, include_resolved: bool = False) -> pd.DataFrame:
    """The Stock Codes whose rechargeability in the document differs from SAGE,
    one line per side / document / Stock Code (with how many lines it covers).
    Unresolved ones only (the rows left out of the totals), or — with
    include_resolved — also those the user has already chosen a source for
    ("Chosen" says which)."""
    empty = pd.DataFrame(columns=DISPARITY_COLUMNS)
    if rows is None or rows.empty or "problem" not in rows.columns:
        return empty
    both = (RECHARGEABLE, NON_RECHARGEABLE)
    doc = rows["doc_bucket"] if "doc_bucket" in rows.columns else rows["bucket"]
    choice = rows["choice"] if "choice" in rows.columns else pd.Series("", index=rows.index)
    unresolved = rows["problem"] == DISPARITY
    resolved = (choice != "") & doc.isin(both) & rows["sage_bucket"].isin(both) & (doc != rows["sage_bucket"])
    bad = rows[unresolved | resolved] if include_resolved else rows[unresolved]
    if bad.empty:
        return empty
    bad = bad.assign(_doc=doc.loc[bad.index], _choice=choice.loc[bad.index])
    g = bad.groupby(["side", "doc", "key"], sort=True).agg(
        d=("_doc", "first"), sage=("sage_bucket", "first"), ch=("_choice", "first"),
        nm=("name", "first"), n=("key", "size")).reset_index()
    names = {CHOICE_SAGE: "SAGE", CHOICE_DOC: "Document", "": ""}
    chosen = [names.get(c, c) for c in g["ch"]]
    return pd.DataFrame({"Side": g["side"], "Document": g["doc"], "Stock Code": g["key"],
                         "Item": g["nm"], "Document says": g["d"], "SAGE says": g["sage"],
                         "Chosen": chosen, "Lines": g["n"]})


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
    df["sage_bucket"] = df["sage_bucket"].fillna("")        # saved before this existed: no disparities
    df["name"] = df["name"].fillna("")
    # saved before the price view existed: no description / kind / rate / days
    df["description"] = df["description"].fillna("")
    df["kind"] = df["kind"].fillna("")
    df["rate"] = pd.to_numeric(df["rate"], errors="coerce")
    df["days"] = pd.to_numeric(df["days"], errors="coerce")
    df["scaffold"] = _as_bool(df["scaffold"])
    df["own_rate"] = _as_bool(df["own_rate"])
    df["equip_rate"] = pd.to_numeric(df["equip_rate"], errors="coerce")
    df["days_note"] = df["days_note"].fillna("")
    df["days_options"] = df["days_options"].fillna("")
    return recost(df, use_qty)


def _summary_frame(rows: pd.DataFrame, word: str, basis: str) -> pd.DataFrame:
    """The per-document summary sheet; `word` is "cost" or "price"."""
    s = summarize(rows)
    return pd.DataFrame({
        "Side": s["side"],
        "Document": s["doc"].where(s["doc"] != TOTAL_LABEL, "ALL DOCUMENTS"),
        f"Rechargeable {word}": s[RECHARGEABLE],
        "Rechargeable %": s[f"{RECHARGEABLE} %"],
        f"Non-Rechargeable {word}": s[NON_RECHARGEABLE],
        "Non-Rechargeable %": s[f"{NON_RECHARGEABLE} %"],
        f"Unknown {word}": s[UNKNOWN],
        "Unknown %": s[f"{UNKNOWN} %"],
        f"Total {word}": s["Total cost"],
        **({f"Non-Rechargeable ÷ Rechargeable ({word})": s["Non/Rech"]} if word == "cost" else {}),
        "Items counted": s["items"],
        "Items not counted": s["no_cost"],
        "…of which no quantity": s["no_qty"],
        "…of which differs from SAGE": s["disparity"],
        f"{word.capitalize()} basis": basis,
    })


def _choice_label(rows: pd.DataFrame) -> pd.Series:
    return rows["choice"].map({CHOICE_SAGE: "SAGE", CHOICE_DOC: "Document"}).fillna("")


def _item_description(rows: pd.DataFrame) -> pd.Series:
    """The line's own description, else SAGE's name for the Stock Code."""
    return rows["description"].where(rows["description"] != "", rows["name"])


def report_frames(rows: pd.DataFrame, use_qty: bool = True,
                  manual: dict[str, float] | None = None,
                  choices: dict[str, str] | None = None,
                  stock_types: dict | None = None,
                  days: dict[str, float] | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(summary, items) of the COST view for the Excel report — every row, no
    document or type filter applied. `summary` has one line per document and a
    total per side. Scaffold lines have no SAGE cost and are not in it."""
    rows = apply_manual(rows[~rows["scaffold"]] if "scaffold" in rows.columns else rows,
                        manual or {}, use_qty, choices, stock_types, days)
    basis = "unit cost × quantity (× days for equipment)" if use_qty else "unit cost only"
    summary = _summary_frame(rows, "cost", basis)
    items = pd.DataFrame({
        "Side": rows["side"], "Document": rows["doc"], "Sheet": rows["sheet"],
        "Stock Code": rows["key"], "Description": _item_description(rows),
        "Item (SAGE name)": rows["name"], "Type": rows["category"],
        "Rechargeability": rows["bucket"], "Document says (rechargeability)": rows["doc_bucket"],
        "SAGE says (rechargeability)": rows["sage_bucket"],
        "Source chosen": _choice_label(rows),
        "Qty": rows["qty"], "Unit cost (SAGE expected)": rows["unit_cost"], "Unit cost source": rows["source"],
        "Cost": rows["cost"], "Not counted because": rows["problem"],
    }).sort_values(["Side", "Document", "Cost"], ascending=[True, True, False],
                   na_position="last", kind="mergesort").reset_index(drop=True)
    return summary, items


def price_report_frames(rows: pd.DataFrame, manual: dict[str, float] | None = None,
                        choices: dict[str, str] | None = None,
                        stock_types: dict | None = None,
                        days: dict[str, float] | None = None,
                        markup_rate: float | None = None,
                        include_scaffold: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(summary, items) of the PRICE view for the Excel report — the same rows
    and rules as the panel in Document price mode, with no filter applied."""
    rows = price_view(apply_manual(rows, manual or {}, True, choices, stock_types, days),
                      days, markup_rate, include_scaffold)
    basis = ("quantity × rate (× days for equipment and scaffold)"
             + (f", consumables with {markup_rate * 100:g}% markup" if markup_rate else
                ", consumables without markup")
             + ("; scaffold included" if include_scaffold else "; scaffold not included"))
    summary = _summary_frame(rows, "price", basis)
    items = pd.DataFrame({
        "Side": rows["side"], "Document": rows["doc"], "Sheet": rows["sheet"],
        "Stock Code": rows["key"], "Description": _item_description(rows),
        "Type": rows["kind"].where(rows["kind"] != "", rows["category"]),
        "Rechargeability": rows["bucket"], "Source chosen": _choice_label(rows),
        "Qty": rows["qty"], "Rate": rows["unit_cost"], "Days": rows["days"], "Days note": rows["days_note"],
        "Price": rows["cost"], "Not counted because": rows["problem"],
        "How to fix": [how_to_fix(p, k, n) for p, k, n in zip(rows["problem"], rows["kind"], rows["type_note"])],
    }).sort_values(["Side", "Document", "Price"], ascending=[True, True, False],
                   na_position="last", kind="mergesort").reset_index(drop=True)
    return summary, items
