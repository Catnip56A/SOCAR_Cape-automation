"""
ctr_tools/stockcode_types.py — which Stock Codes are Equipment and which are Consumables.

The MR vs CTR Document price needs to know, per Stock Code, whether a line is equipment
(priced from the USD Pricebook, per day) or a consumable (priced from SAGE, no days). This
module holds that list.

Two files:
  * `ctr_tools/stockcode_types.json` — the list that SHIPS with the app (bundled into the
    .exe by app.spec). It is deliberately NOT in git: it is the company's Stock Code catalogue,
    hidden from the public repository for privacy purposes (see .gitignore). Without it the
    app still runs; the list simply starts empty.
  * `stockcode_types.json` in the per-user data folder (see ctr_tools/paths.py) — the user's
    own copy, created from the shipped list on first use. This is what the Settings window edits.

File format (both files):
    {"equipment": ["EQ…", …],
     "consumables_stockcodes": ["PP…", …],
     "confirmed": {"CODE": "Equipment" | "Consumable"}}      # optional: the user's own decisions

A code may appear in both lists in the shipped file; resolution (see stats/cost_stats.py) proposes
Equipment for it and asks the user to confirm. `confirmed` records codes the user has accepted or
chosen, so they are not asked again.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from ctr_tools.paths import user_data_dir

log = logging.getLogger(__name__)

_TYPES_PATH = user_data_dir() / "stockcode_types.json"
_BUNDLED_DEFAULT_PATH = Path(__file__).parent / "stockcode_types.json"

EQUIPMENT = "Equipment"
CONSUMABLE = "Consumable"
_KINDS = (EQUIPMENT, CONSUMABLE)
_K_EQ, _K_CONS, _K_CONFIRMED = "equipment", "consumables_stockcodes", "confirmed"

_cache: tuple[object, dict] | None = None        # ((path, mtime), types)


def _norm(code) -> str:
    return str(code).strip().upper()


def empty_types() -> dict:
    return {"equipment": set(), "consumable": set(), "confirmed": {}}


def read_types(path: Path) -> dict | None:
    """The types file at `path` as {"equipment": set, "consumable": set, "confirmed": dict},
    or None when it is missing or not readable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    out = empty_types()
    out["equipment"] = {_norm(c) for c in data.get(_K_EQ, []) if _norm(c)}
    out["consumable"] = {_norm(c) for c in data.get(_K_CONS, []) if _norm(c)}
    conf = data.get(_K_CONFIRMED, {})
    if isinstance(conf, dict):
        out["confirmed"] = {_norm(k): v for k, v in conf.items() if v in _KINDS and _norm(k)}
    return out


def shipped_types() -> dict:
    """The list that ships with the app (empty when the file is not there)."""
    return read_types(_BUNDLED_DEFAULT_PATH) or empty_types()


def shipped_available() -> bool:
    return _BUNDLED_DEFAULT_PATH.exists() and read_types(_BUNDLED_DEFAULT_PATH) is not None


def _stamp():
    try:
        return (str(_TYPES_PATH), _TYPES_PATH.stat().st_mtime_ns)
    except OSError:
        return (str(_TYPES_PATH), None)


def load_types() -> dict:
    """The user's list. On first use it is created from the shipped list."""
    if not _TYPES_PATH.exists() and _BUNDLED_DEFAULT_PATH.exists():
        try:
            _TYPES_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_BUNDLED_DEFAULT_PATH, _TYPES_PATH)
        except OSError as exc:
            log.warning("Could not seed %s from the shipped list: %s", _TYPES_PATH, exc)
            return shipped_types()
    return read_types(_TYPES_PATH) or empty_types()


def current_types() -> dict:
    """The user's list, re-read from disk only when the file changed (the statistics call this
    on every refresh). Do not modify the returned sets: use set_type / save_types."""
    global _cache
    stamp = _stamp()
    if _cache is None or _cache[0] != stamp:
        types = load_types()
        _cache = (_stamp(), types)
    return _cache[1]


def save_types(types: dict) -> None:
    """Write the user's list. Codes are stored sorted; a code that is in both sets stays in both
    (the user resolves that in the statistics), so nothing is lost silently."""
    data = {
        _K_EQ: sorted(types["equipment"]),
        _K_CONS: sorted(types["consumable"]),
        _K_CONFIRMED: dict(sorted(types.get("confirmed", {}).items())),
    }
    try:
        _TYPES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TYPES_PATH.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("Failed to save %s: %s", _TYPES_PATH, exc)
    global _cache
    _cache = None


def _copy(types: dict) -> dict:
    return {"equipment": set(types["equipment"]), "consumable": set(types["consumable"]),
            "confirmed": dict(types.get("confirmed", {}))}


def set_type(code: str, kind: str, confirm: bool = True) -> None:
    """The user decides that `code` is `kind` (Equipment / Consumable): it moves to that list (and
    leaves the other) and is recorded as confirmed, so it is not asked about again."""
    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {_KINDS}")
    types = _copy(load_types() if not _TYPES_PATH.exists() else (read_types(_TYPES_PATH) or empty_types()))
    c = _norm(code)
    mine, other = ("equipment", "consumable") if kind == EQUIPMENT else ("consumable", "equipment")
    types[mine].add(c)
    types[other].discard(c)
    if confirm:
        types["confirmed"][c] = kind
    save_types(types)


def confirm_types(decisions: dict[str, str]) -> None:
    """Record several decisions at once ({code: kind}); same as set_type for each, one write."""
    types = _copy(read_types(_TYPES_PATH) or load_types())
    for code, kind in decisions.items():
        if kind not in _KINDS:
            continue
        c = _norm(code)
        mine, other = ("equipment", "consumable") if kind == EQUIPMENT else ("consumable", "equipment")
        types[mine].add(c)
        types[other].discard(c)
        types["confirmed"][c] = kind
    save_types(types)


def merge_shipped() -> int:
    """Add the shipped codes the user's list does not have yet (the user's own entries and
    decisions are kept). Returns how many codes were added."""
    mine = _copy(read_types(_TYPES_PATH) or empty_types())
    ship = shipped_types()
    known = mine["equipment"] | mine["consumable"]
    new_eq = ship["equipment"] - known
    new_cons = ship["consumable"] - known
    mine["equipment"] |= new_eq
    mine["consumable"] |= new_cons
    save_types(mine)
    return len(new_eq | new_cons)


def reset_to_shipped() -> None:
    """Replace the user's list with the shipped one (the caller backs the old file up first)."""
    _TYPES_PATH.unlink(missing_ok=True)
    global _cache
    _cache = None
    load_types()
