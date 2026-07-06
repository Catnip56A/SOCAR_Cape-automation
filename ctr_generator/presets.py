"""
ctr_generator/presets.py

Named presets for the Generate section's input fields.
Stored as JSON in a stable per-user data directory (see paths.py) so saved
presets survive restarts of the packaged .exe — not next to this module,
which under PyInstaller onefile bundling is a temp dir wiped every launch.

Fields saved per preset: client, sub_client, location, scope,
revision, job_ref, output_dir.

'date' is intentionally excluded — it defaults to today and is usually
overridden by the CTR Request anyway. Contract No (AZN/USD) is also
excluded — it's specific to each generated document, so loading a preset
must never overwrite whatever the user has already typed there.
"""

from __future__ import annotations

import json
from pathlib import Path

from ctr_generator.paths import user_data_dir

_PRESETS_PATH = user_data_dir() / "ctr_presets.json"
_BUNDLED_DEFAULT_PATH = Path(__file__).parent / "ctr_presets.json"

_PRESET_FIELDS = (
    "client", "sub_client", "location", "scope",
    "revision", "job_ref", "output_dir",
)


def _seed_from_bundled_default() -> None:
    """On first run, copy any presets shipped with the app into the
    writable user data directory, so they're available immediately without
    clobbering what a returning user has already saved there."""
    if _PRESETS_PATH.exists() or not _BUNDLED_DEFAULT_PATH.exists():
        return
    try:
        _PRESETS_PATH.write_text(
            _BUNDLED_DEFAULT_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
    except OSError:
        pass


def load_presets() -> dict[str, dict]:
    """Returns {preset_name: {field: value, ...}}. Empty dict if file missing."""
    _seed_from_bundled_default()
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
