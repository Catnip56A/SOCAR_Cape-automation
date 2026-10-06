# Handoff — 2026-10-01

## Working on
CTR Tracker write path, released as v1.0.16 (committed, 41b6cfe, working tree
clean at last check). After the release the session was Q&A on how the tracker
and the MR vs CTR comparator behave; no code changed after the commit.

## Key decisions (with reasoning)
- **Root cause of out-of-order revisions:** the user's real tracker pre-creates
  *three* blank rows per CTR (rows 8296–8298 for CTR-26-281, height 31.5;
  neighbouring CTRs have one 15-height row). The writer took the first blank
  row and the "empty" branch skipped all revision-order logic. Confirmed against
  the pristine `TEST_Files/CTR tracker/CTR-Tracker copy.xlsm`, not the
  "error files" copy (that is an output).
- **Fixed mode, no UI choices** (user's call after consulting their users):
  every write is `allow_overwrite=True, revision_mode="separate"`. The old
  "skip a filled row" default is gone; the backup checkbox (default on) is the
  only safety net. A spare row is used only if it keeps ascending revision
  order, else a row is inserted via Excel and the spare is listed in the results
  dialog. No Excel → the entry is skipped with a message, never placed out of
  order.
- **Beyond the literal ask:** re-loading an existing revision now overwrites
  that row in place even when spares exist (it used to fill another spare and
  duplicate). The user believed it already did this; the memory rule says it must.
- **USD-shows-as-date was OUR bug.** I first accepted the user's "it's the
  file author's" without checking and was wrong: the template has the same
  styles. A hard-coded style id (159) is a date format in the real tracker.
  Lesson: compare template vs output before attributing a formatting problem to
  the input. Fixed by looking up the style by number format in the tracker's own
  `styles.xml` (`value_format_by_currency`). Old USD rows written by earlier
  versions are NOT repaired.
- **Empty formula cells after a write:** writer saves formulas with empty `<v/>`
  and `calcPr` has no `fullCalcOnLoad`, so Excel doesn't recalculate on open.
  The user chose docs-only: workaround is Ctrl+Alt+Shift+F9 (USER_GUIDE,
  CHANGELOG "Known issues"). Plain F9 doesn't work.
- `pyproject.toml` version deliberately left alone (VERSION is the source).

## Current state
- v1.0.16 committed. The user checked the tracker changes on real Windows/Excel
  ("I checked, its fine"), without detailing which cases they ran.
- On Linux only the planning logic was replayed (fake row-inserter standing in
  for Excel, on scratchpad copies); results dialog and confirm text were never
  exercised on Linux.
- Facts established this session that are not in the code comments:
  - **AZN tracker rows** get C, K, L, N, O, P, S, AC, AI (if a revision exists)
    and formulas AJ, AM (Labor), AU, AX. Equipment/3rd party/transport/
    consumables columns are only filled for USD. **The AZN CTR's Third Party
    section (transport) is not carried into the tracker's 3rd party column** —
    looks like a gap, not traced whether intended.
  - **MR vs CTR "Flag" column** (Combined view and its Excel report only):
    blank = units agree; "⚠ Unit conflict — approved" = the user approved
    summing an item whose MR/CTR units disagree (Needs Review → Approve);
    Reject moves it to Error Data. Now documented in USER_GUIDE.md (see
    "Decisions made after the commit").

## Decisions made after the commit (2026-10-01)
- **`fullCalcOnLoad` stays OFF for now.** The user will keep using the
  Ctrl+Alt+Shift+F9 workaround (documented in USER_GUIDE + CHANGELOG "Known
  issues"). Revisit only if it becomes a real pain for users. The permanent fix
  would be `fullCalcOnLoad="1"` in `write_entries_fast`
  (`ctr_tools/tracker_fast.py`); cost: slightly slower open and a "save
  changes?" prompt on close. Worth timing on the real ~8000-row tracker first.
- **Dropped:** read-only scan for old USD rows with the date style; AZN Third
  Party → tracker gap. The user says neither is needed.
- **Revision correction already exists:** after loading a CTR file, the add
  form's Revision field (`tracker_window.py` `_revision_edit`, filled in
  `_load_fields`, read in the add handler) is editable, so mistakes are fixed
  there before the entry is added to the batch. This explains the `AI = 1` on
  row 8297 of the "rev1" AZN file (E5 = 0): it was typed in the window. Not
  checked: whether a revision can be changed after an entry is already in the
  batch table.
- **Flag column** is now explained in USER_GUIDE.md ("Combined View, Needs
  Review and Error Data" section, after Compare Values). Doc-only; not checked
  against a real Combined-view run.

## Open questions
- Carried over (to be discussed later): named-range scoping in `_collect_named_range_refs`, real-MR
  check of the cover-sheet toggle, AZN `Estimated CTR Total` omitting
  Contingency, Word template blocker, `cbar_rates.py` wiring.

## Files changed
None since commit 41b6cfe (this handoff aside). The commit touched
`ctr_tools/tracker_fast.py`, `tracker_window.py`, `config.py`,
`template_config.json`, `VERSION`, `CHANGELOG.md`, `USER_GUIDE.md`, `README.md`.
`.kilo/worktrees/galvanized-plier/` holds stale copies — deliberately untouched.

## Next step
Carried-over items, to be discussed with the user: real-MR check of the
cover-sheet toggle first.
