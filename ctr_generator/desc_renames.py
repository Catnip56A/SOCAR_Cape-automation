"""
ctr_generator/desc_renames.py

Persists user-taught Description overrides — what a requested item's
Description should *display* as in the generated CTR (e.g. the request
says "PUMP ABC", the user wants "Submersible Pump" to show instead).

This is a separate concept from aliases.py's Match-By renames, which
teach the pricebook/SAGE *lookup* key, not the display text — a row can
have both, independently. Kept in its own file rather than folded into
match_aliases.json so a save of one can never silently clobber the other
(both modules independently call json.dumps on their own in-memory dict).

Stored in the same per-user data directory as match_aliases.json (see
paths.py) so it survives restarts of the packaged .exe. Keyed by category
(manpower / equipment / consumable), matching aliases.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from ctr_generator.paths import user_data_dir

_DESC_RENAMES_PATH = user_data_dir() / "desc_renames.json"

_CATEGORIES = ("manpower", "equipment", "consumable")


def load_desc_renames() -> dict:
    """Returns {"manpower": {...}, "equipment": {...}, "consumable": {...}}."""
    data = {}
    if _DESC_RENAMES_PATH.exists():
        try:
            data = json.loads(_DESC_RENAMES_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    return {cat: dict(data.get(cat, {})) for cat in _CATEGORIES}


def save_desc_renames(renames: dict) -> None:
    try:
        _DESC_RENAMES_PATH.write_text(json.dumps(renames, indent=2, ensure_ascii=False))
    except OSError:
        pass  # non-fatal — renames just won't persist across sessions


def get_desc_rename(renames: dict, category: str, requested_desc: str) -> str | None:
    key = (requested_desc or "").strip().lower()
    if not key:
        return None
    return renames.get(category, {}).get(key)


def set_desc_rename(renames: dict, category: str, requested_desc: str, renamed_to: str) -> None:
    key = (requested_desc or "").strip().lower()
    renamed_to = (renamed_to or "").strip()
    if not key or not renamed_to:
        return
    renames.setdefault(category, {})[key] = renamed_to
    save_desc_renames(renames)


def delete_desc_rename(renames: dict, category: str, requested_desc: str) -> None:
    key = (requested_desc or "").strip().lower()
    if category in renames:
        renames[category].pop(key, None)
    save_desc_renames(renames)


def export_desc_renames(renames: dict, path: str | Path) -> None:
    """Writes the renames dictionary to an arbitrary file path as pretty JSON,
    for backup or sharing with another machine/user."""
    Path(path).write_text(json.dumps(renames, indent=2, ensure_ascii=False), encoding="utf-8")


def import_desc_renames(path: str | Path) -> dict:
    """
    Reads a Description-renames JSON file (same shape as desc_renames.json)
    from an arbitrary path, tolerant of files that only contain some
    categories. Returns {"manpower": {...}, "equipment": {...}, "consumable": {...}}.
    Raises ValueError if the file isn't a JSON object, or a category isn't
    an object of {requested name: renamed-to} pairs.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("File does not contain a renames dictionary (expected a JSON object).")
    result = {}
    for cat in _CATEGORIES:
        mapping = data.get(cat) or {}
        if not isinstance(mapping, dict):
            raise ValueError(f'"{cat}" should be an object of {{requested name: renamed-to}} pairs.')
        result[cat] = {str(k): str(v) for k, v in mapping.items()}
    return result
