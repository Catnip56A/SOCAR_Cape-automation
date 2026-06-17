"""
mr_ctr_comparator.py  —  Streamlit app

Compares Material Requisition (MR) and Cost Time Resource (CTR) Excel
workbooks using fully deterministic, signal-based layout detection.
No LLM, no hardcoded row numbers, no manual row-range selection.
"""

import re
import streamlit as st
import pandas as pd
from io import BytesIO
from pathlib import Path
import sys, os

# Import the deterministic sheet parser from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sheet_parser import parse_workbook

st.set_page_config(page_title="MR vs CTR Comparator", layout="wide")
st.title("MR vs CTR Comparator")
st.markdown(
    "Upload your MR and CTR Excel files. Tables are detected automatically — "
    "no row pickers, no AI calls. Select which detected tables to compare, "
    "choose your join key, and get a highlighted diff report."
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_col(name):
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def find_key_col(columns):
    priority = ["stockcode", "equipmentcode", "itemcode", "code",
                "description", "item", "name"]
    for token in priority:
        for col in columns:
            if token in normalize_col(col):
                return col
    return columns[0] if columns else None


def parse_codes(text):
    return [c.strip() for c in text.split(",") if c.strip()]


def _serialize(tables):
    return [
        {
            "source_file":  t["source_file"],
            "source_sheet": t["source_sheet"],
            "table_name":   t["table_name"],
            "data_json":    t["data"].to_json(orient="split"),
            "columns":      list(t["data"].columns),
            "n_rows":       len(t["data"]),
        }
        for t in tables
    ]


def restore_df(cached_table):
    return pd.read_json(cached_table["data_json"], orient="split")


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------

col1, col2 = st.columns(2)
with col1:
    mr_files = st.file_uploader(
        "MR file(s)", type=["xlsx", "xls", "xlsm"], key="mr",
        accept_multiple_files=True
    )
with col2:
    ctr_files = st.file_uploader(
        "CTR file(s)", type=["xlsx", "xls", "xlsm"], key="ctr",
        accept_multiple_files=True
    )

if not mr_files or not ctr_files:
    st.info("Upload at least one MR file and one CTR file to begin.")
    st.stop()

# ---------------------------------------------------------------------------
# Parse all uploaded files  (no caching — always reads fresh bytes)
# ---------------------------------------------------------------------------

def load_group(files, label):
    all_tables = []
    for f in files:
        with st.spinner(f"Parsing {f.name}…"):
            try:
                tables = parse_workbook(BytesIO(f.getvalue()), filename=f.name)
                all_tables.extend(_serialize(tables))
            except Exception as e:
                st.warning(f"Could not parse {f.name}: {e}")
    return all_tables

mr_tables  = load_group(mr_files,  "MR")
ctr_tables = load_group(ctr_files, "CTR")

if not mr_tables:
    st.error("No tables detected in MR files.")
    st.stop()
if not ctr_tables:
    st.error("No tables detected in CTR files.")
    st.stop()

# ---------------------------------------------------------------------------
# Table selection
# ---------------------------------------------------------------------------

st.subheader("Detected tables")
sel1, sel2 = st.columns(2)

def table_label(t):
    fname = Path(t["source_file"]).stem
    return f"{fname} › {t['table_name']}  ({t['n_rows']} rows)"

with sel1:
    st.markdown("**MR tables**")
    mr_options = {table_label(t): i for i, t in enumerate(mr_tables)}
    mr_chosen  = st.multiselect(
        "Select MR tables to include",
        options=list(mr_options.keys()),
        default=list(mr_options.keys()),
        key="mr_table_sel"
    )

with sel2:
    st.markdown("**CTR tables**")
    ctr_options = {table_label(t): i for i, t in enumerate(ctr_tables)}
    ctr_chosen  = st.multiselect(
        "Select CTR tables to include",
        options=list(ctr_options.keys()),
        default=list(ctr_options.keys()),
        key="ctr_table_sel"
    )

if not mr_chosen or not ctr_chosen:
    st.info("Select at least one table from each side.")
    st.stop()

# Combine selected tables
mr_parts  = [restore_df(mr_tables[mr_options[k]])  for k in mr_chosen]
ctr_parts = [restore_df(ctr_tables[ctr_options[k]]) for k in ctr_chosen]

mr_df  = pd.concat(mr_parts,  ignore_index=True, sort=False)
ctr_df = pd.concat(ctr_parts, ignore_index=True, sort=False)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

st.subheader("Comparison settings")
c1, c2 = st.columns(2)

mr_key_default  = find_key_col(list(mr_df.columns))
ctr_key_default = find_key_col(list(ctr_df.columns))

with c1:
    mr_key = st.selectbox(
        "Join key column (MR)", mr_df.columns,
        index=list(mr_df.columns).index(mr_key_default) if mr_key_default else 0,
        key="mr_key"
    )
with c2:
    ctr_key = st.selectbox(
        "Join key column (CTR)", ctr_df.columns,
        index=list(ctr_df.columns).index(ctr_key_default) if ctr_key_default else 0,
        key="ctr_key"
    )

# Drop CTR rows that have no usable value in the join-key column
_bad_keys = {"", "nan", "-", "0", "none"}
ctr_df = ctr_df[
    ~ctr_df[ctr_key].astype(str).str.strip().str.lower().isin(_bad_keys)
].reset_index(drop=True)

# Stock code filter
st.markdown("**Filter by key value (optional)**")
fc1, fc2 = st.columns(2)
with fc1:
    mr_filter  = st.text_input("Keys to include from MR (comma-separated)", key="mr_filter")
with fc2:
    ctr_filter = st.text_input("Keys to include from CTR (comma-separated)", key="ctr_filter")

# ---------------------------------------------------------------------------
# Preview  (reflects filters immediately)
# ---------------------------------------------------------------------------

def _apply_filter(df, key_col, filter_text):
    if not filter_text.strip():
        return df
    codes = {c.strip().upper() for c in filter_text.split(",") if c.strip()}
    return df[df[key_col].astype(str).str.strip().str.upper().isin(codes)]

st.subheader("Preview")
p1, p2 = st.columns(2)
with p1:
    pv_mr = _apply_filter(mr_df, mr_key, mr_filter)
    st.caption(f"MR — {len(pv_mr)} rows" + (" (filtered)" if mr_filter.strip() else ""))
    st.dataframe(pv_mr, use_container_width=True, height=400)
with p2:
    pv_ctr = _apply_filter(ctr_df, ctr_key, ctr_filter)
    st.caption(f"CTR — {len(pv_ctr)} rows" + (" (filtered)" if ctr_filter.strip() else ""))
    st.dataframe(pv_ctr, use_container_width=True, height=400)

# Column pair selection (fuzzy-matched)
mr_norm  = {normalize_col(c): c for c in mr_df.columns
            if c not in (mr_key,)  and not c.startswith("_")}
ctr_norm = {normalize_col(c): c for c in ctr_df.columns
            if c not in (ctr_key,) and not c.startswith("_")}

matched  = sorted(set(mr_norm) & set(ctr_norm))
col_pairs = {mr_norm[n]: ctr_norm[n] for n in matched}

st.markdown("**Fields to compare**")
if not col_pairs:
    st.warning("No matching column names found between the selected tables. "
               "Only missing-key analysis will run.")
    selected_pairs = {}
else:
    display_opts  = [f"{a}  ↔  {b}" if a != b else a
                     for a, b in col_pairs.items()]
    opt_to_pair   = dict(zip(display_opts, col_pairs.items()))
    chosen_fields = st.multiselect(
        "Select fields", display_opts, default=display_opts
    )
    selected_pairs = {opt_to_pair[o][0]: opt_to_pair[o][1]
                      for o in chosen_fields}

# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

if st.button("Compare", type="primary"):
    mr  = mr_df.copy()
    ctr = ctr_df.copy()

    mr["_KEY_"]  = mr[mr_key].astype(str).str.strip().str.upper()
    ctr["_KEY_"] = ctr[ctr_key].astype(str).str.strip().str.upper()

    # Drop unusable keys
    bad = {"", "NAN", "-", "NONE", "STOCKCODE", "0"}
    mr  = mr[~mr["_KEY_"].isin(bad)]
    ctr = ctr[~ctr["_KEY_"].isin(bad)]

    if parse_codes(mr_filter):
        mr  = mr[mr["_KEY_"].isin([c.upper() for c in parse_codes(mr_filter)])]
    if parse_codes(ctr_filter):
        ctr = ctr[ctr["_KEY_"].isin([c.upper() for c in parse_codes(ctr_filter)])]

    merged = pd.merge(mr, ctr, on="_KEY_", how="outer",
                      suffixes=("_MR", "_CTR"), indicator=True)

    only_mr_df  = merged[merged["_merge"] == "left_only"].copy()
    only_ctr_df = merged[merged["_merge"] == "right_only"].copy()
    only_mr  = only_mr_df["_KEY_"].tolist()
    only_ctr = only_ctr_df["_KEY_"].tolist()
    matched  = merged[merged["_merge"] == "both"]

    # Determine source-file columns after the outer-merge suffix expansion
    src_mr_col  = "_SourceFile_MR"  if "_SourceFile_MR"  in matched.columns else "_SourceFile"
    src_ctr_col = "_SourceFile_CTR" if "_SourceFile_CTR" in matched.columns else None

    diff_rows = []
    for mr_col, ctr_col in selected_pairs.items():
        col_mr  = f"{mr_col}_MR"  if f"{mr_col}_MR"  in matched.columns else mr_col
        col_ctr = f"{ctr_col}_CTR" if f"{ctr_col}_CTR" in matched.columns else ctr_col
        if col_mr not in matched.columns or col_ctr not in matched.columns:
            continue

        a, b     = matched[col_mr], matched[col_ctr]
        both_nan = a.isna() & b.isna()
        mismatch = (a.astype(str).str.strip() != b.astype(str).str.strip()) & ~both_nan

        label = mr_col if mr_col == ctr_col else f"{mr_col} / {ctr_col}"
        for idx in matched[mismatch].index:
            row = {
                "Stock Code":   matched.loc[idx, "_KEY_"],
                "MR Document":  matched.loc[idx, src_mr_col] if src_mr_col in matched.columns else "",
                "CTR Document": matched.loc[idx, src_ctr_col] if src_ctr_col and src_ctr_col in matched.columns else "",
                "Field":        label,
                "MR Value":     matched.loc[idx, col_mr],
                "CTR Value":    matched.loc[idx, col_ctr],
            }
            diff_rows.append(row)

    diff_df = pd.DataFrame(diff_rows)

    # --- Results ---
    st.subheader("Results")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Matched keys",      len(matched))
    m2.metric("Field differences", len(diff_df))
    m3.metric("Keys only in MR",   len(only_mr))
    m4.metric("Keys only in CTR",  len(only_ctr))

    # Matched keys table
    if not matched.empty:
        def _find_col(df, *candidates):
            """Return the first column whose normalised name exactly matches any candidate."""
            norm_map = {normalize_col(c): c for c in df.columns}
            for cand in candidates:
                hit = norm_map.get(normalize_col(cand))
                if hit:
                    return hit
            return None

        mc = matched.copy()
        # Columns shared by both DFs get _MR/_CTR suffix; unique columns keep original name.
        desc_mr   = _find_col(mc, "Description_MR",  "Description")
        desc_ctr  = _find_col(mc, "Description_CTR")
        qty_mr    = _find_col(mc, "Qty_MR",           "Qty")        # MR-only → no suffix
        unit_mr   = _find_col(mc, "Unit_MR",           "Unit")
        rech_col  = _find_col(mc, "Rechargeable_MR",  "Rechargeable")  # MR-only
        alloc_col = _find_col(mc, "Allocation_MR",    "Allocation")     # MR-only
        qty_ctr   = _find_col(mc, "Quantity_CTR",     "Quantity")   # CTR-only → no suffix
        unit_ctr  = _find_col(mc, "Unit_CTR")
        rate_col  = _find_col(mc, "Rate_CTR",         "Rate")

        display = pd.DataFrame({"Stock Code": mc["_KEY_"]})
        if src_mr_col in mc.columns:
            display["MR Document"]      = mc[src_mr_col]
        if src_ctr_col and src_ctr_col in mc.columns:
            display["CTR Document"]     = mc[src_ctr_col]
        if desc_mr:
            display["Description (MR)"] = mc[desc_mr]
        if desc_ctr:
            display["Description (CTR)"] = mc[desc_ctr]
        if qty_mr:
            display["Qty (MR)"]          = mc[qty_mr]
        if unit_mr:
            display["Unit (MR)"]         = mc[unit_mr]
        if rech_col:
            display["Rechargeable"]      = mc[rech_col]
        if alloc_col:
            display["Allocation"]        = mc[alloc_col]
        if qty_ctr:
            display["Qty (CTR)"]         = mc[qty_ctr]
        if unit_ctr:
            display["Unit (CTR)"]        = mc[unit_ctr]
        if rate_col:
            display["Rate (CTR)"]        = mc[rate_col]

        display = display.sort_values("Stock Code").reset_index(drop=True)
        with st.expander(f"Matched keys ({len(display)})", expanded=True):
            st.dataframe(display, use_container_width=True,
                         height=min(400, 35 * len(display) + 38))

    if not diff_df.empty:
        st.markdown("**Field-level differences**")
        st.dataframe(diff_df, use_container_width=True)
    else:
        st.success("No field-level differences found in matched records.")

    if only_mr:
        with st.expander(f"Keys only in MR ({len(only_mr)})"):
            omr = pd.DataFrame({"Stock Code": only_mr_df["_KEY_"].values})
            _s = "_SourceFile_MR" if "_SourceFile_MR" in only_mr_df.columns else (
                "_SourceFile" if "_SourceFile" in only_mr_df.columns else None)
            if _s:
                omr.insert(1, "Document", only_mr_df[_s].values)
            st.dataframe(omr, use_container_width=True)
    if only_ctr:
        with st.expander(f"Keys only in CTR ({len(only_ctr)})"):
            octr = pd.DataFrame({"Stock Code": only_ctr_df["_KEY_"].values})
            _s = "_SourceFile_CTR" if "_SourceFile_CTR" in only_ctr_df.columns else (
                "_SourceFile" if "_SourceFile" in only_ctr_df.columns else None)
            if _s:
                octr.insert(1, "Document", only_ctr_df[_s].values)
            st.dataframe(octr, use_container_width=True)

    # --- Download ---
    if not diff_df.empty or only_mr or only_ctr:
        out = BytesIO()
        omr_dl  = pd.DataFrame({"Stock Code": only_mr_df["_KEY_"].values})
        _smr = "_SourceFile_MR" if "_SourceFile_MR" in only_mr_df.columns else (
            "_SourceFile" if "_SourceFile" in only_mr_df.columns else None)
        if _smr:
            omr_dl.insert(1, "Document", only_mr_df[_smr].values)

        octr_dl = pd.DataFrame({"Stock Code": only_ctr_df["_KEY_"].values})
        _sctr = "_SourceFile_CTR" if "_SourceFile_CTR" in only_ctr_df.columns else (
            "_SourceFile" if "_SourceFile" in only_ctr_df.columns else None)
        if _sctr:
            octr_dl.insert(1, "Document", only_ctr_df[_sctr].values)

        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            diff_df.to_excel(writer, index=False, sheet_name="Differences")
            omr_dl.to_excel(writer, index=False, sheet_name="Only in MR")
            octr_dl.to_excel(writer, index=False, sheet_name="Only in CTR")
        st.download_button(
            "Download report (Excel)",
            data=out.getvalue(),
            file_name="mr_ctr_diff_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )