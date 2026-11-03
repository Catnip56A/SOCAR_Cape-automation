"""
comparator/match_keys.py

How MR and CTR rows are matched in Compare: the join key (`_KEY_`) of every row, by match mode.

    stock   only the Stock Code            (the original behaviour)
    desc    only the Description           (letters and digits, upper case: punctuation and spaces ignored)
    both    the Stock Code first; the rows still unmatched are then matched by Description (default)

Pure pandas, no Qt. This is NOT the Match By aliases (ctr_tools/aliases) nor the Description edits.
"""
from __future__ import annotations

import re

import pandas as pd

MATCH_STOCK = "stock"
MATCH_DESC = "desc"
MATCH_BOTH = "both"
MATCH_MODES = (MATCH_BOTH, MATCH_STOCK, MATCH_DESC)
DEFAULT_MATCH_MODE = MATCH_BOTH
MATCH_MODE_KEY = "compare/match_mode"          # QSettings

# Stock Code values that mean "no code" (compared after strip + upper case).
_NO_CODE = {"", "NAN", "-", "NONE", "STOCKCODE", "0"}


def normalize_col(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def stock_key(v) -> str:
    """The Stock Code as the join key; "" when the cell holds no usable code."""
    s = str(v).strip().upper()
    return "" if s in _NO_CODE else s


def desc_key(v) -> str:
    """Only letters and digits count, upper case: "Hot-wash  hose 3/8\"" == "HOT WASH HOSE 3/8"."""
    if v is None or (isinstance(v, float) and v != v):
        return ""
    s = "".join(ch for ch in str(v) if ch.isalnum()).upper()
    return "" if s in ("NAN", "NONE") else s


def find_stock_col(columns: list) -> str | None:
    """A Stock Code column only (never falls back to the Description, unlike app.find_key_col)."""
    for token in ("stockcode", "equipmentcode", "itemcode", "code"):
        for col in columns:
            if token in normalize_col(col):
                return col
    return None


def find_desc_col(columns: list) -> str | None:
    for col in columns:
        if "description" in normalize_col(col):
            return col
    return None


def _col_keys(df: pd.DataFrame, col: str | None, fn) -> pd.Series:
    if not col or col not in df.columns:
        return pd.Series([""] * len(df), index=df.index, dtype=object)
    return df[col].map(fn)


def assign_keys(mr: pd.DataFrame, ctr: pd.DataFrame, mode: str,
                stock_col_fallback: dict[str, str | None] | None = None):
    """Returns (mr, ctr, problem): copies with a `_KEY_` column, the rows without a usable key
    dropped. `problem` is (side, columns_available) when a side lacks the column the mode needs,
    else None (then the frames are returned unchanged).

    stock_col_fallback = {"MR": col, "CTR": col}: the column the original `find_key_col` chose;
    used only by the `stock` mode so that it behaves exactly as before."""
    mr, ctr = mr.copy(), ctr.copy()
    sides = {"MR": mr, "CTR": ctr}
    sk: dict[str, pd.Series] = {}
    dk: dict[str, pd.Series] = {}
    for side, df in sides.items():
        cols = list(df.columns)
        s_col = find_stock_col(cols)
        d_col = find_desc_col(cols)
        if mode == MATCH_STOCK:
            s_col = (stock_col_fallback or {}).get(side) or s_col
            if not s_col:
                return mr, ctr, (side, cols)
        elif mode == MATCH_DESC:
            if not d_col:
                return mr, ctr, (side, cols)
        elif not s_col and not d_col:
            return mr, ctr, (side, cols)
        sk[side] = _col_keys(df, s_col, stock_key)
        dk[side] = _col_keys(df, d_col, desc_key)

    if mode == MATCH_STOCK:
        keys = sk
    elif mode == MATCH_DESC:
        keys = dk
    else:
        common = (set(sk["MR"]) & set(sk["CTR"])) - {""}
        # Second pass: of the rows the Stock Code did not match, only those whose Description the
        # other side also has switch to the Description key. The rest keep their Stock Code (so
        # Only in MR / Only in CTR still list it, and rows sharing a Description do not merge).
        left_desc = {side: {d for s, d in zip(sk[side], dk[side]) if s not in common and d}
                     for side in sides}
        by_desc = left_desc["MR"] & left_desc["CTR"]
        keys = {}
        for side in sides:
            keys[side] = pd.Series(
                [s if s in common else (d if d in by_desc else (s or d))
                 for s, d in zip(sk[side], dk[side])],
                index=sides[side].index, dtype=object)

    out = []
    for side, df in sides.items():
        df["_KEY_"] = keys[side]
        df["_CODE_"] = sk[side]           # the row's own Stock Code ("" = an item without one); the statistics use it
        out.append(df[df["_KEY_"] != ""])
    return out[0], out[1], None
