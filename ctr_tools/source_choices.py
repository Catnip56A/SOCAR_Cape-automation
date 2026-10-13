"""
ctr_tools/source_choices.py — which source is right for a Stock Code's rechargeability.

When a document and SAGE disagree about whether a Stock Code is rechargeable (a
"disparity"), the user chooses per Stock Code: "DOC" (the document is right) or
"SAGE". The choices are remembered between runs, so the same code does not have
to be decided again, and are shared by every statistics list in the app.

Stored as {STOCK CODE (upper case): "DOC" | "SAGE"} in `source_choices.json` in
the per-user data folder (see ctr_tools/paths.py).
"""

from __future__ import annotations

import json
import logging

from ctr_tools.paths import user_data_dir

log = logging.getLogger(__name__)

_CHOICES_PATH = user_data_dir() / "source_choices.json"
_VALID = ("DOC", "SAGE")


def load_choices() -> dict[str, str]:
    if not _CHOICES_PATH.exists():
        return {}
    try:
        data = json.loads(_CHOICES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to load %s, starting empty: %s", _CHOICES_PATH, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).strip().upper(): v for k, v in data.items() if v in _VALID and str(k).strip()}


def save_choices(choices: dict[str, str]) -> None:
    try:
        _CHOICES_PATH.write_text(json.dumps(choices, indent=2, ensure_ascii=False, sort_keys=True),
                                 encoding="utf-8")
    except OSError as exc:
        # Not fatal: the choices just will not survive a restart.
        log.warning("Failed to save %s: %s", _CHOICES_PATH, exc)
