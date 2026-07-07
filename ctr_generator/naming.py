"""
ctr_generator/naming.py

Builds the output filename for a generated CTR — shared by builder_azn.py
and builder_usd.py so both currencies follow the same convention:

    {job_ref}_{CURRENCY}_{location_acronym}_{scope}_CTR.xlsx

Any segment that comes out empty (e.g. location/scope not filled in yet)
is simply dropped rather than leaving a stray "__" in the name.
"""

from __future__ import annotations

import re

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MAX_SCOPE_LEN = 120


def derive_location_acronym(location: str) -> str:
    """
    Best-effort acronym from a location name, e.g. "West Chirag" -> "WCH":
    one word takes its own first three letters; two words take the first
    word's initial plus the second word's first two letters; three or more
    words take one initial per word. This is a heuristic, not a lookup —
    it won't reproduce established site codes coined independently of the
    location text (e.g. "DWG" for Deepwater Gunashli, "ACE").
    """
    words = [w for w in re.split(r"\s+", location.strip()) if w]
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:3].upper()
    if len(words) == 2:
        return (words[0][:1] + words[1][:2]).upper()
    return "".join(w[0] for w in words).upper()


def sanitize_filename_part(text: str) -> str:
    """Strips characters illegal in Windows/Mac/Linux filenames and trims length."""
    cleaned = _ILLEGAL_CHARS.sub("", text).strip().rstrip(".")
    return cleaned[:_MAX_SCOPE_LEN].strip()


def ctr_output_filename(job_ref: str, currency: str, location: str, scope: str) -> str:
    """Builds "{job_ref}_{CURRENCY}_{acronym}_{scope}_CTR.xlsx"."""
    parts = [
        str(job_ref),
        currency.upper(),
        derive_location_acronym(location),
        sanitize_filename_part(scope),
        "CTR",
    ]
    return "_".join(p for p in parts if p) + ".xlsx"
