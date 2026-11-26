"""Which Word proposal template a proposal is made from (no Qt here, so it can be tested on its own).

The user picks ONE folder. A template in it is found by its name:

    Word_Proposal_Template_<short|long>[_v<N>][_<Task type>].docx

    Word_Proposal_Template_long.docx                      -> long,  General
    Word_Proposal_Template_short_v2.docx                  -> short, General   (the highest _v<N> of a kind wins)
    Word_Proposal_Template_long_Industrial_Cleaning.docx  -> long,  Industrial Cleaning   (underscores = spaces)

Names change, so a **mapping** wins over the name: the user can map any .docx file (in any folder) to a
(kind, task type). `resolve` looks at the mapping first, then at the names found in the folder.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

GENERAL = "General"          # a template whose name carries no task type
KINDS = ("short", "long")

_NAME = re.compile(r"^Word_Proposal_Template_(short|long)(?:_v(\d+))?(?:_(.+))?\.docx$", re.IGNORECASE)


def parse_name(name: str) -> tuple[str, str, int] | None:
    """(kind, task type, version) for a template file name, None for any other file."""
    m = _NAME.match(name)
    if not m:
        return None
    task = (m.group(3) or "").replace("_", " ").strip() or GENERAL
    return m.group(1).lower(), task, int(m.group(2) or 0)


def discover(folder: str | Path) -> dict[tuple[str, str], Path]:
    """{(kind, task type): file} for every template in the folder named by the rule; of two files for the
    same kind and type (short and short_v2) the higher version wins. An unreadable folder gives {}."""
    out: dict[tuple[str, str], tuple[int, Path]] = {}
    try:
        folder = Path(folder)
        files = sorted(folder.iterdir()) if str(folder) and folder.is_dir() else []
    except OSError:
        return {}
    for f in files:
        parsed = parse_name(f.name)
        if parsed is None or not f.is_file():
            continue
        kind, task, version = parsed
        if (kind, task) not in out or version > out[(kind, task)][0]:
            out[(kind, task)] = (version, f)
    return {k: v[1] for k, v in out.items()}


def map_key(kind: str, task: str) -> str:
    return f"{kind}|{task}"


def load_map(text: str) -> dict[str, str]:
    """The saved mapping ({"kind|task type": path}); anything unusable gives {}."""
    try:
        data = json.loads(text) if text else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, str) and v.strip() and k.split("|")[0] in KINDS and "|" in k:
            out[k] = v
    return out


def dump_map(mapping: dict[str, str]) -> str:
    return json.dumps(mapping, ensure_ascii=False, sort_keys=True)


def types_for(kind: str, found: dict, mapping: dict[str, str]) -> list[str]:
    """The task types that have a template of this kind (from the folder or the mapping): General first, then A-Z."""
    types = {t for (k, t) in found if k == kind}
    types |= {key.split("|", 1)[1] for key in mapping if key.split("|", 1)[0] == kind}
    return sorted(types, key=lambda t: (t != GENERAL, t.lower()))


def resolve(kind: str, task: str, found: dict, mapping: dict[str, str]) -> Path | None:
    """The file for (kind, task type): the mapping first, else the one found by name. None = there is none."""
    mapped = mapping.get(map_key(kind, task))
    if mapped:
        return Path(mapped)
    return found.get((kind, task))


def source_of(kind: str, task: str, found: dict, mapping: dict[str, str]) -> str:
    """"mapped" / "by name" / "" (nothing) — how the template of this row was chosen."""
    if mapping.get(map_key(kind, task)):
        return "mapped"
    return "by name" if (kind, task) in found else ""
