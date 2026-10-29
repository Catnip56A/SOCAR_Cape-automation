# Handoff — 2026-10-29

## Goal
Release 1.0.23 (VERSION and CHANGELOG [1.0.23] written): the Ctrl+F crash fix, on top of 1.0.22 (PDMS proposal, CBAR rates/currencies, PDMS overview sheet), which the user verified on Windows (nine checks passed).

## Done (full offscreen suite 241/241 on the 1.0.23 state, 2026-10-29)
- Ctrl+F crash fixed: the Windows crash.log (access violation in `common/find_bar.py`) came from the Find bar keeping matches of a results-table model that a new Compare had replaced (the bar was installed while the table had no model, so it never attached). `FindBar._attach_model` now follows the current model and `_goto` ignores foreign matches; `tests/test_find_bar.py` has a regression test that fails on the old code.
- Version 1.0.23: VERSION, CHANGELOG [1.0.23] (the Ctrl+F entry moved out of [1.0.22]); `setup.iss` reads VERSION (no change); README/USER_GUIDE hold no version.
- 1.0.22 delivered (committed c82f142, 88f6e06): PDMS Word proposal, Hotbed line, per-cell currencies with totals in manats, CBAR rates (log, validation, Yes/No for unusual rates, usage log, Inspector tab), PDMS overview sheet, Re-match All question, test-suite fixes.
- Task log: 12 rows closed at 100 % under 1.0.22 (MA197–MA200, MA204, MA206–MA212); MA202, MA203 closed earlier; MA214 (Ctrl+F) at 90 %; backup `..._start_of_1.0.23.xlsx` made.

## In progress / not fully done
- UNCOMMITTED: `common/find_bar.py`, `tests/test_find_bar.py`, `VERSION`, `CHANGELOG.md`, `.claude/handoff.md`, `.claude/task_log/short_summaries.json`. The user commits and pushes (never me), planned as two commits: (1) the fix, (2) the release.
- Build of 1.0.23 (`python -m PyInstaller app.spec` from Windows, then `iscc setup.iss`) and its Windows test are for the user.
- After the commit: run /tasklog so 1.0.23 gets its row (`missing` lists 1.0.23 until a row has that version; MA214 is the Ctrl+F row).

## Decisions (with reasoning)
- The Find bar compares the view's current model on every use (weak reference) instead of relying on signals, because the model is replaced after the bar is created.
- Onshore schedule-of-activities text stays the same as Offshore for now (MA201); wording/capitals wait for the commercial department (MA205); parked rows are changed at the end of feature development.
- I never commit or push; I suggest commit titles. Windows checks are not listed as open issues.

## Next
1. User commits the two commits (titles below), builds 1.0.23 and runs the Windows test. 2. /tasklog after the commit. 3. Next version: different Onshore text (MA201), then the parked rows.

## Open questions
- None for 1.0.23. (MA201 and MA205 wait for the commercial department.)

## Parked
- MA124, MA127–MA130, MA133, MA194 stay until feature development ends; MA164, MA193 are the user's tests; Disparities segfault (crash.log 2026-10-15) not reproduced — note the 2026-10-29 crashes were the Find bar (fixed), not this one.

## Windows checks (1.0.23)
- Compare, search with Ctrl+F, Compare again with different files, then F3 / Enter and Ctrl+F: the app must stay up and the matches must be from the new table.
- Quick regression: PDMS box on/off, one PDMS proposal and the usual AZN/USD CTRs still generate; installer upgrades over 1.0.22.

## Suggested commits (user runs them)
1. `Fix Ctrl+F crash after a new Compare (Find bar followed a replaced model)` — common/find_bar.py, tests/test_find_bar.py
2. `1.0.23: release` — VERSION, CHANGELOG.md, .claude/handoff.md, .claude/task_log/short_summaries.json
