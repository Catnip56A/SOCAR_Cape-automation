from pathlib import Path

# Single source of truth for the app version — setup.iss reads this same
# file at compile time so the installer version and in-app version can't drift.
__version__ = (Path(__file__).parent.parent / "VERSION").read_text().strip()
