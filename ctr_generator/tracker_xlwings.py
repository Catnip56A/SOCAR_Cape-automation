"""
ctr_generator/tracker_xlwings.py

Structural row insertion for the CTR Tracker, via real Excel automation
(xlwings/COM on Windows, xlwings/AppleScript on macOS) — used only for the
"add as separate revision" write mode when no spare pre-created row is
available to reuse (see tracker_fast.py's _classify_target_row).

Why Excel and not openpyxl: openpyxl's ws.insert_rows() moves cell values
and styles down but does not touch formula text, data-validation ranges, or
conditional-formatting ranges — anything elsewhere in the sheet that refers
to a row at or after the insertion point stays pointing at the old row
number. An audit of the real tracker file found nothing to actually corrupt
(no merged cells, no Excel Tables, no data validation, and every one of its
~38,000 formulas references only its own row — see the CTR Tracker feature
notes), which is why the ordinary write path stays on openpyxl/raw-XML.
Real row insertion is still routed through actual Excel here rather than
reimplemented, so the one operation that does need reference-shifting
(conditional formatting, the AutoFilter range) gets it for free, correctly,
the same way it would if a person inserted the row by hand.

Cost of that choice: this module only works on Windows or macOS, only when
Excel itself is installed, and it opens the real workbook in a live Excel
process rather than editing the zip/XML directly — meaningfully slower than
the raw-XML fast path, which is why it's used only for this one rare case.

IMPORTANT — untested by the author of this module: this was written and
reviewed without access to a Windows/macOS machine with Excel installed
(the development environment is Linux), so it has not been run against a
real file. Test it thoroughly against a COPY of the real tracker workbook
before relying on it for a live write.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)


class ExcelAutomationUnavailable(RuntimeError):
    """Raised when this platform, or this machine, can't drive Excel."""


def available() -> bool:
    """True if this platform can plausibly drive Excel (Windows/macOS) —
    doesn't confirm Excel is actually installed, just rules out Linux."""
    return sys.platform in ("win32", "darwin")


def insert_revision_rows(
    tracker_path: str | Path,
    sheet_name: str,
    insertions: list[tuple[int, str, str]],
    *,
    row_local_formulas: list[tuple[str, str]] | None = None,
    col_row_counter: str | None = None,
    row_counter_formula: str | None = None,
) -> None:
    """
    Inserts one blank row for each (insert_at_row, ctr_number, col_ctr_number)
    in `insertions`, shifting insert_at_row and everything below it down by
    one, with the new row's formatting copied from the row above it (the
    row it's a revision of). Each new row's CTR-number cell is set to
    ctr_number so the ordinary fast writer can find it afterward exactly
    like any other pre-created spare row.

    A plain row insert in Excel copies formatting but not formulas into the
    new blank row, so every other row-local helper formula the sheet's real
    rows carry (see CFG["ctr_tracker"]) has to be seeded explicitly too, or
    the new row is conspicuously missing them (or invisible to anything
    that keys off them, e.g. a search/lookup column):

      row_local_formulas: [(col_letter, template), ...] for formulas that
        reference only cells in their *own* row (e.g. "IF(I{row}>0,...)")
        — {row} is filled in with insert_at_row and the result seeded onto
        the new row. A plain Excel row-insert already renumbers these
        correctly on every row that *shifts*, so nothing else needs doing
        for them.

      col_row_counter / row_counter_formula: a running counter formula
        that instead references the row *above* it (e.g. "B{prev_row}+1")
        — {prev_row} is filled in with insert_at_row - 1 and seeded onto
        the new row, same idea. But the row pushed down by the insert (the
        one now at insert_at_row + 1) keeps *its* original formula
        unchanged, because that formula's own reference (to the row above
        the insertion point) never moved — Excel only rewrites references
        to cells that actually shifted, and that source cell didn't. Left
        alone, the pushed-down row would end up duplicating the new row's
        count instead of continuing it. So if that row already has a
        formula there, it's rewritten too, now pointing at the new row.

    insertions is processed from the highest insert_at_row to the lowest,
    so earlier insertions in the list don't shift the row numbers later
    ones are targeting — pass row numbers computed against the sheet's
    *current* (pre-insertion) state; this function handles the ordering.

    Opens and saves the real file through an actual Excel process — see
    the module docstring for why, and its limitations. Raises
    ExcelAutomationUnavailable if this platform/machine can't do that, or
    RuntimeError wrapping whatever xlwings/Excel itself raised.
    """
    if not insertions:
        return
    if not available():
        raise ExcelAutomationUnavailable(
            "Inserting a row for a separate revision requires Excel — this "
            f"only works on Windows or macOS (this machine is {sys.platform}). "
            "Use \"Overwrite this row\" instead, or pre-create a spare row "
            "for this CTR number by hand."
        )

    try:
        import xlwings as xw
    except ImportError as exc:
        raise ExcelAutomationUnavailable(
            "The xlwings package is required to insert a separate-revision "
            "row and isn't installed."
        ) from exc

    tracker_path = str(Path(tracker_path))
    # Highest row first: each insertion shifts every row below it down by
    # one, so working top-down would invalidate the row numbers of every
    # insertion still to come. Bottom-up, an earlier (higher-numbered)
    # insertion never affects the row numbers of the ones still queued.
    ordered = sorted(insertions, key=lambda t: t[0], reverse=True)

    app = xw.App(visible=False, add_book=False)
    try:
        book = app.books.open(tracker_path)
        try:
            sheet = book.sheets[sheet_name]
            for insert_at_row, ctr_number, col_ctr_number in ordered:
                log.info(
                    "tracker_xlwings: inserting row %d (CTR %s) in %s",
                    insert_at_row, ctr_number, tracker_path,
                )
                target = sheet.range(f"{insert_at_row}:{insert_at_row}")
                # copy_origin is Windows-only per xlwings' own docs — on
                # macOS the new row keeps Excel's own default (usually the
                # row above anyway), so this is a no-op there rather than
                # an error.
                if sys.platform == "win32":
                    target.insert(shift="down", copy_origin="format_from_left_or_above")
                else:
                    target.insert(shift="down")
                # Label the new row with the CTR number, same as a human
                # pre-creating a spare row by hand — the ordinary fast
                # writer (tracker_fast.py) finds it from here exactly like
                # any other still-empty reserved row.
                sheet.range(f"{col_ctr_number}{insert_at_row}").value = ctr_number

                for col, template in (row_local_formulas or []):
                    sheet.range(f"{col}{insert_at_row}").formula = (
                        "=" + template.format(row=insert_at_row)
                    )

                if col_row_counter and row_counter_formula:
                    sheet.range(f"{col_row_counter}{insert_at_row}").formula = (
                        "=" + row_counter_formula.format(prev_row=insert_at_row - 1)
                    )
                    # Repair the pushed-down row's now-stale counter (see
                    # docstring above) — only if it actually had one, so a
                    # genuinely blank trailing row past the real data stays
                    # blank rather than gaining a formula it never had.
                    pushed_down_cell = sheet.range(f"{col_row_counter}{insert_at_row + 1}")
                    if pushed_down_cell.formula:
                        pushed_down_cell.formula = (
                            "=" + row_counter_formula.format(prev_row=insert_at_row)
                        )
            book.save()
        finally:
            book.close()
    except Exception as exc:
        raise RuntimeError(f"Excel row insertion failed: {exc}") from exc
    finally:
        app.quit()
