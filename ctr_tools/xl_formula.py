"""
xl_formula.py — a cell typed as a formula in the Generator ("=25/12") is written to Excel as that formula.

A row dict may carry "formulas": {field: "=expr"} for the cells whose number came from a formula (in the units the
CTR holds: hours and an hourly rate); the builders write the formula instead of the number. The PDMS overview sheet
shows days and a day rate, so a manpower row also carries "formulas_days" (the same fields in those units).
"""

from __future__ import annotations


def pick(row: dict, field: str, number):
    """The Excel formula of a row's field when it has one, else the number itself."""
    f = (row.get("formulas") or {}).get(field)
    return f if f else number


def pick_days(row: dict, field: str, number):
    """Same for the overview sheet, which shows manpower in days and a day rate."""
    f = (row.get("formulas_days") or row.get("formulas") or {}).get(field)
    return f if f else number
