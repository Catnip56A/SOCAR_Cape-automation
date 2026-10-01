# Handoff — 2026-09-29 (session 2)

## Working on
CTR Tracker write path (v1.0.16): fixing revision rows landing out of order
when a CTR has several pre-made spare rows. All code + docs done, **uncommitted**.
(The 1.0.15 MR-comparator work from the earlier session is already committed as
caf64db.)

## Key decisions (with reasoning)
- **Root cause found:** the user's real tracker pre-creates *three* blank rows
  per CTR (rows 8296–8298 for CTR-26-281, height 31.5; neighbouring CTRs have
  one 15-height row). `_classify_target_row` returns the first blank row and
  the "empty" branch skipped all revision-order logic, so USD → 8296, AZN
  rev 1 → 8297, AZN blank → 8298 (below rev 1). Confirmed against the pristine
  `TEST_Files/CTR tracker/CTR-Tracker copy.xlsm` (not the "error files" copy).
- **Fixed mode, no UI choices** (user's call after consulting their users):
  removed the "Overwrite rows that already have data" checkbox and both radios.
  Every write = `allow_overwrite=True, revision_mode="separate"`. Consequence
  the user accepted: the old "skip a filled row" default is gone; the backup
  checkbox (default on) is the only safety net.
- **Spare rows are used only if they keep ascending revision order**
  (`_check_spare_row`: every lower revision above the spare, every higher one
  below). Otherwise a row is inserted at the ordered position (Option B) and the
  spare is left blank and reported in the results dialog.
- **No Excel → skip the entry with a message**, never fall back to the
  out-of-order spare (user confirmed).
- **Beyond the literal ask:** re-loading a revision that already exists now
  overwrites that row in place even when spares exist (previously it filled
  another spare → duplicate). User believed it already did this; the memory
  rule says it must.
- **USD-shows-as-date was OUR bug** (I first accepted the user's "it's the
  file author's" and was wrong; the pristine backup has the same styles):
  `value_style_by_currency` hard-coded USD → style id 159, which in the real
  tracker is a date format with a different fill (the right one is 157,
  `"$"#,##0.00`). Style ids are positions in each workbook's style table, so
  ids can't be fixed in config. Replaced by `value_format_by_currency` +
  `_style_with_number_format` (looks up, in the tracker's own `styles.xml`,
  the style identical to the cell's current one but with the currency format;
  falls back to leaving the cell's style alone). Old USD rows already written
  by earlier versions are NOT repaired.
- **Version:** bumped to 1.0.16 since 1.0.15 was already committed and dated;
  the two tracker entries moved to a new `[1.0.16]` section. `pyproject.toml`
  still deliberately left alone (VERSION is the source).

## Current state
- Verified on Linux only: replayed USD → AZN rev1 → AZN blank → rev1 again →
  rev2 on a scratchpad copy of the pristine tracker, with a **fake
  openpyxl row-inserter standing in for Excel**. Order, in-place correction,
  and the flag ("row 8299 left blank…", post-shift numbering) all correct.
  Tracker window builds offscreen; not exercised interactively.
- `write_entries_fast` now returns 4 values (`written, skipped, backup, notes`);
  only `tracker_window.py` calls it.
- **User confirmed on real Windows/Excel ("I checked, its fine"):** the v1.0.16
  tracker changes work. Details of what exactly was exercised weren't given.
- **Was not verified on Linux:** real Excel insertion (`tracker_xlwings.insert_revision_rows`)
  with these new row numbers; the results dialog with the new "Spare rows left
  blank" section; the confirm dialog wording. Needs a relayed Windows session.
- Nothing committed. Suggested message:
  `v1.0.16 - CTR Tracker: revisions stay in order across spare rows, fixed separate-revision mode (overwrite options removed), spare-row flag in results`
- `.claude/handoff.md` was already modified in git before this session (an
  older handoff), now overwritten by this one.

## Open questions
- **Empty formula cells after a write** (user hit it): writer saves formulas with an
  empty `<v/>` and workbook `calcPr` has no `fullCalcOnLoad`, so Excel doesn't
  recalc on open. Workaround documented (Ctrl+Alt+Shift+F9, USER_GUIDE +
  CHANGELOG "Known issues"). Permanent fix (set `fullCalcOnLoad="1"` in
  `write_entries_fast`) proposed, user chose docs-only for now.
- Does the real Windows run insert at the right row and keep formulas intact
  for the new blank-vs-rev1 case? (Memory note: tracker_xlwings behaviour has
  changed since the v1.0.12 real-Excel verification.)
- Existing USD rows written before this fix still have the date style; offered
  a read-only check that lists them — user hasn't answered.
- The user's "rev1" AZN file has `0` in its Revision cell (E5); the `AI = 1` on
  row 8297 must have been typed in the window. Never confirmed with the user.
- Carried over: named-range scoping in `_collect_named_range_refs`, real-MR
  check of the cover-sheet toggle, AZN `Estimated CTR Total` omitting
  Contingency, Word template blocker, `cbar_rates.py` wiring.

## Files changed
- `ctr_tools/tracker_fast.py` — `_filled_revision_rows` (extracted),
  `_check_spare_row` (new), `_plan_insertions` (empty-branch order check,
  4th return `spare_skipped`, in-place rows), `write_entries_fast`
  (defaults True/"separate", `notes` return, Excel-failure clears the flag).
- `ctr_tools/tracker_window.py` — removed overwrite checkbox/radios,
  `_on_overwrite_toggled`, `_revision_mode`; `_WriteWorker` signal now carries
  `notes`; confirm and results dialogs reworded/extended; unused imports gone.
- `ctr_tools/config.py` + `template_config.json` — `value_format_by_currency`
  replaces `value_style_by_currency`.
- `VERSION` (1.0.16), `CHANGELOG.md`, `USER_GUIDE.md`, `README.md`.
- `.kilo/worktrees/galvanized-plier/` has stale copies of these files —
  deliberately untouched.

## Next step
Commit (message above). Optionally: make the `fullCalcOnLoad` fix, and/or a
read-only check listing old USD rows that still carry the date style.
