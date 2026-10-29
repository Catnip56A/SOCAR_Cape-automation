# Handoff — 2026-10-29

## Goal
Release 1.0.23 (VERSION and CHANGELOG [1.0.23] written): the Ctrl+F crash fix, on top of 1.0.22 (PDMS proposal, CBAR rates/currencies, PDMS overview sheet), which the user verified on Windows (nine checks passed).

## Done (full offscreen suite 241/241 on the 1.0.23 state, 2026-10-29)
- Ctrl+F crash fixed: the Windows crash.log (access violation in `common/find_bar.py`) came from the Find bar keeping matches of a results-table model that a new Compare had replaced (the bar was installed while the table had no model, so it never attached). `FindBar._attach_model` now follows the current model and `_goto` ignores foreign matches; `tests/test_find_bar.py` has a regression test that fails on the old code.
- Version 1.0.23: VERSION, CHANGELOG [1.0.23] (the Ctrl+F entry moved out of [1.0.22]); `setup.iss` reads VERSION (no change); README/USER_GUIDE hold no version.
- 1.0.22 delivered (committed c82f142, 88f6e06): PDMS Word proposal, Hotbed line, per-cell currencies with totals in manats, CBAR rates (log, validation, Yes/No for unusual rates, usage log, Inspector tab), PDMS overview sheet, Re-match All question, test-suite fixes.
- Task log: 12 rows closed at 100 % under 1.0.22 (MA197–MA200, MA204, MA206–MA212); MA202, MA203 closed earlier; MA214 (Ctrl+F) at 100 % under 1.0.23 (Windows test of the build passed); backup `..._start_of_1.0.23.xlsx` made.

## In progress / not fully done
- Committed by the user: the fix (201b0ff) and the release (8fad0a3, VERSION 1.0.23); the working tree was clean. The user pushes (never me).
- Task log: MA214 (the one 1.0.23 row) is 100 %; the 1.0.23 build passed its Windows test. MA127 is at 20 %, MA124 at 20 %.

## Decisions (with reasoning)
- The Find bar compares the view's current model on every use (weak reference) instead of relying on signals, because the model is replaced after the bar is created.
- Onshore schedule-of-activities text stays the same as Offshore for now (MA201); wording/capitals wait for the commercial department (MA205); parked rows are changed at the end of feature development.
- I never commit or push; I suggest commit titles. Windows checks are not listed as open issues.

## Next
1. Next version: different Onshore text (MA201); wording after the commercial department's first look (MA205); then the parked rows.

## Open questions
- None for 1.0.23. (MA201 and MA205 wait for the commercial department.)

## Parked
- MA124, MA127–MA130, MA133, MA194 stay until feature development ends; MA164, MA193 are the user's tests; Disparities segfault (crash.log 2026-10-15) not reproduced — note the 2026-10-29 crashes were the Find bar (fixed), not this one.

## Windows checks
- None open: the 1.0.23 build (Ctrl+F fix) passed its Windows test.
