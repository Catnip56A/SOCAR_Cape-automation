# Handoff — 2026-10-28

## Goal
CTR Generator, version 1.0.22 part 2 (same version as part 1; VERSION and CHANGELOG [1.0.22] already updated): PDMS proposal (Word), CBAR rates/currencies, PDMS overview sheet.

## Done (full offscreen suite 236/236 passed on 2026-10-27; LibreOffice Calc recalculation checked the sheet)
- PDMS proposal: checkbox in the CTR Generator header → .docx from the tagged PDMS template (`builder_pdms.py`: `compute_pdms` figures + `build_pdms`); the sample's own tables are filled; labour shows days and day rate (shift length from the Encoding); transport lines sit in the consumables table; PDF export works (user saw it).
- Hotbed line under Manpower (rate 59 editable, currency, days, Include box); the user enters it by hand each time.
- Currencies: category defaults + per-cell currency (number left, sign right, drop-down, one click); totals in manats; box titles list currencies. Order: convert → mark-up → round line totals to 2 decimals. Mark-up 10 % in PDMS mode, 6.5 % otherwise.
- CBAR: rates group with Reload per currency/all, Settings → CBAR currencies, rate log + validation, Yes/No question for an unusual rate, last checked/last action, document-usage log, Inspector CBAR tab, Storage/snapshot entries.
- "PDMS overview" first sheet in the statistics workbook (formulas, stock codes, encodings) = same figures as the Word file.
- Re-match All buttons ask first and list consequences; notes under the buttons.
- Test fix: `test_file_changes` left `QMessageBox.warning` replaced (caused the 120 s Generate hangs); harness resets PDMS state between tests.
- Docs (CHANGELOG, README, USER_GUIDE, tests/README) updated. Task log: MA202, MA203 closed; MA205 (wording) added.

## In progress / not fully done
- Part 1 is committed (c82f142). Part 2 is NOT committed (19 files; new: `ctr_tools/builder_pdms.py`, `pdms_overview.py`, `tests/test_builder_pdms.py`, `test_cbar_log.py`, `test_pdms_overview.py`); the user commits and pushes (never me).
- Release rows for the built work are not in the task log yet: write them together with MA127's percentage (user will say) at release time.
- Not verified on the real Windows app / Word / Excel (user checks at build time, tracked in MA204).

## Decisions (with reasoning)
- Unusual CBAR rate is always logged, never silently dropped: the user is asked and may approve it (it may be right); an unreadable entry keeps the old rate.
- Currency is stored on the rate cell (no extra column); only turned-on currencies can be chosen.
- PDMS template is shared by the department, not in git; the Word proposal has no stock-code column (the overview sheet does).
- Hotbed: with rate and days it is calculated whatever Include says; incomplete → inserted as given only while Include is ticked.
- Wording/capitals/visual choices wait for the commercial department's first look (MA205); parked rows are changed at the end of feature development.

## Next
1. User builds the .exe and runs the Windows checks (MA204). 2. User commits part 2 (suggested title: "1.0.22 - part 2: PDMS proposal (Word), CBAR rates and currencies, PDMS overview sheet"). 3. /tasklog with MA127 for the release rows, then /release.

## Open questions
- MA201: Onshore schedule-of-activities text — stays as is for 1.0.22; to be made different in the next version.
- MA127 (short proposal, 1 %): the user will say what percentage it should show.

## Parked
- MA124, MA127–MA130, MA133, MA194 untouched until feature development ends; MA164, MA193 are the user's tests; Disparities segfault (crash.log 2026-10-15) not reproduced.

## Windows checks
- Generated .docx in Word: no repair prompt, tables/fills/page break/header/footer; PDMS overview sheet in Excel (change a rate).
- Reload per currency against the real CBAR site; the Yes/No question; Settings → CBAR currencies.
- In-cell currency drop-down (click the right half), NON-RECHARGEABLE from the left, Re-match All question.
- Inspector CBAR tab (9 tabs fit; hover/click); PDMS checkbox does not raise the window's minimum height.
- Part 1 (MA197–MA200): Qty drop-down in the toolbar, grey cells with a multi-sheet pair, wheel over a drop-down.
