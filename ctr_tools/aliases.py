"""
ctr_tools/aliases.py

Persists user-taught name mappings between CTR Request descriptions and
pricebook/SAGE descriptions, since the same item is often named differently
across documents (e.g. request says "Rigger-National", pricebook says
"Rigger"). Once a user fixes a "No match" row by editing "Match By" in the
matching UI, the mapping is remembered here and applied automatically the
next time the same requested description appears in a future CTR Request.

Stored as a flat JSON file in a stable per-user data directory (see
paths.py) so saved renames survive restarts of the packaged .exe — not
next to this module, which under PyInstaller onefile bundling is a temp
dir wiped every launch. Keyed by category (manpower / equipment /
consumable) to avoid the same word meaning different things across item
types.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ctr_tools.paths import user_data_dir

log = logging.getLogger(__name__)

_ALIASES_PATH = user_data_dir() / "match_aliases.json"
_BUNDLED_DEFAULT_PATH = Path(__file__).parent / "match_aliases.json"

_CATEGORIES = ("manpower", "equipment", "consumable")


def _seed_from_bundled_default() -> None:
    """On first run, copy any renames shipped with the app into the
    writable user data directory, so they're available immediately without
    clobbering what a returning user has already saved there."""
    if _ALIASES_PATH.exists() or not _BUNDLED_DEFAULT_PATH.exists():
        return
    try:
        _ALIASES_PATH.write_text(
            _BUNDLED_DEFAULT_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
    except OSError as exc:
        log.warning("Failed to seed %s from bundled default: %s", _ALIASES_PATH, exc)


def load_aliases() -> dict:
    """Returns {"manpower": {...}, "equipment": {...}, "consumable": {...}}."""
    _seed_from_bundled_default()
    data = {}
    if _ALIASES_PATH.exists():
        try:
            data = json.loads(_ALIASES_PATH.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to load %s, starting empty: %s", _ALIASES_PATH, exc)
            data = {}
    return {cat: dict(data.get(cat, {})) for cat in _CATEGORIES}


def save_aliases(aliases: dict) -> None:
    try:
        _ALIASES_PATH.write_text(json.dumps(aliases, indent=2, ensure_ascii=False))
    except OSError as exc:
        # non-fatal — renames just won't persist across sessions — but
        # worth a trail, since this otherwise discards a user's typed-in
        # rename with zero feedback anywhere.
        log.warning("Failed to save %s: %s", _ALIASES_PATH, exc)


def get_alias(aliases: dict, category: str, requested_desc: str) -> str | None:
    key = (requested_desc or "").strip().lower()
    if not key:
        return None
    return aliases.get(category, {}).get(key)


def set_alias(aliases: dict, category: str, requested_desc: str, match_key: str) -> None:
    key = (requested_desc or "").strip().lower()
    match_key = (match_key or "").strip()
    if not key or not match_key:
        return
    bucket = aliases.setdefault(category, {})
    if bucket.get(key) == match_key:
        return   # already stored — skip the full-file rewrite
    bucket[key] = match_key
    save_aliases(aliases)


def delete_alias(aliases: dict, category: str, requested_desc: str) -> None:
    key = (requested_desc or "").strip().lower()
    if category in aliases:
        aliases[category].pop(key, None)
    save_aliases(aliases)


def export_aliases(aliases: dict, path: str | Path) -> None:
    """Writes the renames dictionary to an arbitrary file path as pretty JSON,
    for backup or sharing with another machine/user."""
    Path(path).write_text(json.dumps(aliases, indent=2, ensure_ascii=False), encoding="utf-8")


def import_aliases(path: str | Path) -> dict:
    """
    Reads a renames JSON file (same shape as match_aliases.json) from an
    arbitrary path, tolerant of files that only contain some categories.
    Returns {"manpower": {...}, "equipment": {...}, "consumable": {...}}.
    Raises ValueError if the file isn't a JSON object, or a category isn't
    an object of {requested name: rename} pairs.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("File does not contain a renames dictionary (expected a JSON object).")
    result = {}
    for cat in _CATEGORIES:
        mapping = data.get(cat) or {}
        if not isinstance(mapping, dict):
            raise ValueError(f'"{cat}" should be an object of {{requested name: rename}} pairs.')
        result[cat] = {str(k): str(v) for k, v in mapping.items()}
    return result
