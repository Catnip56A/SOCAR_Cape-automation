# Handoff — 2026-10-22

## Goal
MR vs CTR Comparator, version 1.0.22, part 1 (small UI/behaviour fixes). Part 2 (the major part) starts in a new chat.

## Done
- MR column is now **Rechargeable (MR)** in the Matched / Only in MR / Only in CTR tables, Excel report, USER_GUIDE; old saved comparisons are renamed on load. Verified: offscreen tests (mismatch_nav, local_cost, attention).
- **Qty mode** drop-down next to Compare Values ("Qty: all differences" / "Qty: CTR < MR only", i.e. CTR − MR < 0), for the detail tables and the Combined View (Qty totals + Diff red, ↑/↓ follow it), Units always red, remembered in QSettings (`compare/qty_mode`). Verified: tests/test_qty_mode.py (4 tests).
- **Grey missing cells/columns**: empty cell in an MR/CTR column is grey (`_EMPTY_BG`); Description/Qty/Unit pairs, Rechargeable (MR), Allocation, Rate (CTR) are always present (empty if the files lack them) and skipped by Compare Values (`_absent_cols`). Highlights still override grey. Also fixed an old bug: an MR file without Unit showed the CTR's Unit as "Unit (MR)" (`_own` in `_build_display`).
- **xcb on Linux** (`app.py` top, `QT_QPA_PLATFORM` setdefault): WSLg's Wayland plugin left drop-down popups as unclosable windows. Cost: UI is blurry in WSLg (X11 screen is 1280x800, upscaled 2x); user accepted it.
- **Wheel no longer changes drop-downs** (`common/no_wheel.py`, installed in `app.py` `__main__`); wheel scrolls the page; spin boxes keep the wheel. Verified: tests/test_no_wheel.py.
- The user tried the changes themselves and said they work.

## In progress / not fully done
- NOT verified on Windows (the built .exe): header text, grey colours, Qty drop-down in the toolbar, window min height. Not tested with a real multi-sheet MR/CTR pair or an older saved comparison.
- Full offscreen suite did not finish in 500 s; two test_generate_rules tests timed out (~123 s) in that run but pass alone (file 5/5). Cause unknown, not re-run before my changes.
- Nothing is committed. VERSION bumped to 1.0.22 and a CHANGELOG [1.0.22] section (2026-10-22) written for part 1; add part 2 to the same section. New files: common/no_wheel.py, tests/test_qty_mode.py, tests/test_no_wheel.py. Task log updated 2026-10-22: rows MA196–MA200 added at 90 % ("In process", waiting for the Windows check and the commit); change their Version/pct when 1.0.22 is released. Parked rows untouched.

## Decisions (with reasoning)
- xcb stays the default on Linux: Wayland popups are unusable, blur accepted.
- Wheel is blocked only for drop-downs, not spin boxes (the wheel is intended there).
- Grey applies to MR/CTR columns only, not Stock Code / Days / Local cost (those have their own marks).
- "CTR < MR only" skips rows with a quantity on one side only; Unit differences stay in both modes.

## Next
1. New chat for part 2 of 1.0.22: continue the same version (VERSION and CHANGELOG already at 1.0.22); /release at the end.
2. After the Windows check, set MA196–MA200 to 100 % (use /tasklog).

## Open questions
- Commit part 1 now or with part 2? (user said "not right now")
- Push the older local commits to origin? Still not answered.

## Parked
- MA124, MA127–MA130, MA133, MA194 stay ignored until the user says.
- MA193 (Windows test of the reload/browse fixes) and MA164 (screen-ratio test, 1080p at 150 %) still to do by the user.
- Disparities segfault (crash.log 2026-10-15) not reproduced.

## Windows checks
- Matched table: "Rechargeable (MR)" header, blue tint, red Rate/Rechargeable mismatch after Compare Values; open one older saved comparison.
- Qty drop-down: both modes, in detail and Combined view; fits the toolbar; choice survives a restart; window min height unchanged.
- Grey cells: Only in MR / Only in CTR tabs, a file missing a column, multi-sheet files; red/orange still on top; selected-row colours.
- Scrolling over a drop-down (also inside a table cell) scrolls the page; the Markup Rate box still changes with the wheel.
