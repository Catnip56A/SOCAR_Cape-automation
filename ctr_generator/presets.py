"""
ctr_generator/presets.py

Named presets for the Generate section's input fields.
Stored as JSON next to this module; keys are preset names.

Fields saved per preset: client, sub_client, location, scope,
contract_no_azn, contract_no_usd, revision, job_ref, output_dir.

'date' is intentionally excluded — it defaults to today and is usually
overridden by the CTR Request anyway.
"""

from __future__ import annotations

import json
from pathlib import Path

_PRESETS_PATH = Path(__file__).parent / "ctr_presets.json"

_PRESET_FIELDS = (
    "client", "sub_client", "location", "scope",
    "contract_no_azn", "contract_no_usd",
    "revision", "job_ref", "output_dir",
)


def load_presets() -> dict[str, dict]:
    """Returns {preset_name: {field: value, ...}}. Empty dict if file missing."""
    if not _PRESETS_PATH.exists():
        return {}
    try:
        data = json.loads(_PRESETS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def save_presets(presets: dict[str, dict]) -> None:
    try:
        _PRESETS_PATH.write_text(
            json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass
