# Handoff — 2026-10-29

## Goal
CTR Generator 1.0.24 (in progress, not released): PDMS manpower in days, formulas in number cells, Reload. VERSION is still 1.0.23; no 1.0.24 CHANGELOG section yet.

## Done (offscreen tests only: new suites + ~25 neighbouring ones pass; full suite NOT run; user checked 1-5 of the formula list and "works" for pick/reload/rename popup on the real app)
- Part 1 (commit a15b079): PDMS mode shows Manpower as Quantity (Days) / Rate/Day / UOM "Days" (hours ÷ shift length from the Encoding, hourly rate × shift length, 2 decimals, cells keep hours); a row with no shift length stays in hours with a red "!" + reason tooltip. Formulas `=1+2` (`common/formula.py`, `common/formula_widgets.py`): every number cell of the four request tables, Hotbed, CBAR rate boxes, Markup box, statistics Unit cost/Days (reopen on the formula; a formula in a Days cell is in days; stale once the value is rewritten elsewhere); hover text per column and on Totals.
- Part 2 (commit 6e949ac): "Re-match All" → "Reload all" (+ Transport): re-reads the CTR Request file, replaces cells the user changed after a question. Ctrl+Shift+click picks cells (blue frame, clears row selection); on release a popup reloads every cell in picked rows × picked columns. A reloaded Description is the file's text; a popup asks whether to also delete the saved Description rename (Yes/No/Cancel).
- Tests: test_pdms_days_view, test_formulas, test_reload; test_cbar_log's Re-match test rewritten.

## In progress / not fully done
- Formulas are not saved in presets (only the Markup value); Markup formula is lost on preset load.
- No hover meaning for the Hotbed total label; README/USER_GUIDE have no text for days view, formulas or Reload.
- Not verified: Ctrl+Shift on Windows (may clash with the keyboard-layout shortcut); transport group got a note line — window height after Maximize not re-measured on Windows.

## Decisions (with reasoning)
- A formula lives beside the value (`_FORMULA_ROLE` = formula, value text, unit) and is live only while the cell still holds that value: no hooks needed in re-match/reload code, and every `float(item.text())` reader stays untouched.
- Reload builds the row with the first-load code (`_append_source_row`) and copies only the chosen cells; unpicked columns are never touched, even if the fresh row prices differently.
- Editor state is kept on the delegate per cell, not on the editor object (the view hands delegates a fresh Python wrapper; a test holding the editor hid this bug).
- Reload Description ignores saved renames (they are the per-category Description renames, not Match By aliases); the user decides about deleting them.

## Next
1. Ask which Phase D is: PDMS overview sheet header block (`ctr_tools/pdms_overview.py` info list: add city, signer, signer title, client, sub-client, revision, contract no, project type, comments) or the header fields in the window; then do it.
2. Then decide: formulas in presets; release steps for 1.0.24 (ask new release vs continuation; /release; README/USER_GUIDE; CHANGELOG).

## Open questions
- Phase D meaning (above). Nothing else unresolved.

## Parked
- MA201/MA205 (Onshore text, wording) wait for the commercial department; MA124, MA127–MA130, MA133, MA194 until feature development ends; Disparities segfault (2026-10-15) not reproduced.

## Windows checks
- Formulas typed in each table, Hotbed, CBAR, Markup, statistics cells; days view (normal/PDMS, "!" with Encoding cleared); Ctrl+Shift picks + popup + saved-rename question; "Reload all" for each of the four tables; window still fits when maximised.
