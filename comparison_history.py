"""
comparison_history.py — Save/restore full MR vs CTR comparison results.

Each comparison is saved as its own JSON file under comparisons_data_dir()
(a sibling of the CTR Generator's own data folder, not nested inside it —
see ctr_generator/paths.py), so a past comparison can be reopened exactly
as it was reviewed and decided — even if the original MR/CTR source files
have since been edited, renamed, or moved. This snapshots the *results*
(the Matched/Only-in-MR/Only-in-CTR tables, the Combined-view aggregates,
and any unit-conflict Approve/Reject decisions) as well as the raw
per-sheet MR/CTR tables behind them (schema version 2+), so a reopened
comparison can have more MR/CTR files added and be re-compared, instead of
being purely a read-only snapshot. Nothing is re-parsed or re-compared on
load itself, though — that only happens if the user adds files and runs
Compare again.

Public API
----------
    from comparison_history import save_comparison, load_comparison, \
        list_saved_comparisons, default_label, suggest_save_path, comparisons_dir

    path = suggest_save_path(default_label(mr_files, ctr_files))
    save_comparison(path, label=..., mr_files=..., ctr_files=...,
                     display_df=..., omr_df=..., octr_df=...,
                     combined_raw=..., combined_decisions=...,
                     mr_tables=..., ctr_tables=...)
    data = load_comparison(path)  # -> dict of the fields above, as DataFrames
                                   #    (mr_tables/ctr_tables as list[dict])
    entries = list_saved_comparisons()  # -> [{path, label, timestamp, mr_files, ctr_files}, ...]
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ctr_generator.paths import comparisons_data_dir

_SCHEMA_VERSION = 2

# A module attribute (not just a function return value) so tests can
# sandbox it the same way as _PRESETS_PATH/_ALIASES_PATH/_DESC_RENAMES_PATH
# in the other persistence modules — see CLAUDE.md's Testing Safety section.
_COMPARISONS_DIR = comparisons_data_dir()


def comparisons_dir() -> Path:
    return _COMPARISONS_DIR


def _slug(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return s[:max_len] or "comparison"


def default_label(mr_files: list[str], ctr_files: list[str]) -> str:
    # Each side gets its own length budget rather than truncating the whole
    # joined string — MR filenames here tend to be long, and slugging the
    # combined label as one blob could cut off the CTR side entirely.
    mr_part  = _slug("+".join(Path(f).stem for f in mr_files[:2]) or "MR",  30)
    ctr_part = _slug("+".join(Path(f).stem for f in ctr_files[:2]) or "CTR", 30)
    return f"{mr_part} vs {ctr_part}"


def suggest_save_path(label: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = comparisons_dir() / f"{ts}__{_slug(label)}.json"
    if not base.exists():
        return base
    # Two saves within the same second (the timestamp's own resolution) would
    # otherwise collide on this exact filename and silently overwrite each
    # other — disambiguate instead of risking that.
    stem = base.stem
    n = 2
    while True:
        candidate = base.with_name(f"{stem}_{n}.json")
        if not candidate.exists():
            return candidate
        n += 1


def _json_default(obj):
    """Handles the value types real Excel data produces that plain
    json.dumps doesn't know: numpy scalars, pandas/py Timestamps, and NaN
    (pandas' own float NaN is a plain float and passes through fine, but
    this covers pd.NaT and any stray numpy NaN that isn't)."""
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if obj is pd.NaT:
        return None
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return str(obj)


def _tables_to_json(tables: list[dict]) -> list[dict]:
    """Encodes the raw per-sheet parsed tables (source_file/source_sheet/
    table_name/data) the same way the upload flow holds them in memory, so
    a loaded comparison can be extended with more files later — see
    _tables_from_json."""
    return [
        {
            "source_file":  t["source_file"],
            "source_sheet": t["source_sheet"],
            "table_name":   t["table_name"],
            "data":         t["data"].to_dict(orient="records"),
        }
        for t in tables
    ]


def _tables_from_json(raw: list[dict]) -> list[dict]:
    return [
        {
            "source_file":  t["source_file"],
            "source_sheet": t["source_sheet"],
            "table_name":   t["table_name"],
            "data":         pd.DataFrame(t["data"]),
        }
        for t in raw
    ]


def save_comparison(
    path: Path,
    *,
    label: str,
    mr_files: list[str],
    ctr_files: list[str],
    display_df: pd.DataFrame,
    omr_df: pd.DataFrame,
    octr_df: pd.DataFrame,
    combined_raw: dict,
    combined_decisions: dict,
    mr_tables: list[dict] | None = None,
    ctr_tables: list[dict] | None = None,
) -> None:
    data = {
        "version":             _SCHEMA_VERSION,
        "label":               label,
        "timestamp":           datetime.now().isoformat(timespec="seconds"),
        "mr_files":            list(mr_files),
        "ctr_files":           list(ctr_files),
        "display_df":          display_df.to_dict(orient="records"),
        "omr_df":              omr_df.to_dict(orient="records"),
        "octr_df":             octr_df.to_dict(orient="records"),
        "combined_raw":        combined_raw,
        "combined_decisions":  combined_decisions,
        # Raw per-sheet tables (as parsed, before merge) — lets a reopened
        # comparison have more MR/CTR files added and be re-compared,
        # instead of being a read-only snapshot. Absent/empty on saves
        # made before this was added (schema version 1).
        "mr_tables":           _tables_to_json(mr_tables or []),
        "ctr_tables":          _tables_to_json(ctr_tables or []),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )


def load_comparison(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        "label":               data.get("label", Path(path).stem),
        "timestamp":           data.get("timestamp", ""),
        "mr_files":            data.get("mr_files", []),
        "ctr_files":           data.get("ctr_files", []),
        "display_df":          pd.DataFrame(data.get("display_df") or []),
        "omr_df":              pd.DataFrame(data.get("omr_df") or []),
        "octr_df":             pd.DataFrame(data.get("octr_df") or []),
        "combined_raw":        data.get("combined_raw", {}),
        "combined_decisions":  data.get("combined_decisions", {}),
        # Empty for comparisons saved before schema version 2 — those can
        # still be reopened, just not extended with more MR/CTR files.
        "mr_tables":           _tables_from_json(data.get("mr_tables") or []),
        "ctr_tables":          _tables_from_json(data.get("ctr_tables") or []),
    }


def list_saved_comparisons() -> list[dict]:
    """Lightweight listing (no dataframes) of every saved comparison, newest
    first — for a picker UI, so the user chooses from what's actually there
    instead of having to browse the filesystem for it."""
    out = []
    for p in comparisons_dir().glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({
            "path":      p,
            "label":     data.get("label", p.stem),
            "timestamp": data.get("timestamp", ""),
            "mr_files":  data.get("mr_files", []),
            "ctr_files": data.get("ctr_files", []),
        })
    out.sort(key=lambda d: d["timestamp"], reverse=True)
    return out
