# Handoff — 2026-10-28

## Goal
CTR Generator, version 1.0.22 part 2 (same version as part 1): PDMS proposal (Word), CBAR rates/currencies, PDMS overview sheet. Both parts are committed (c82f142, 88f6e06); the working tree is clean.

## Done (full offscreen suite 240/240 passed on 2026-10-28; LibreOffice Calc recalculation checked the sheet; reviewer agent: no blockers, its 9 points fixed)
- PDMS proposal: checkbox in the CTR Generator header → .docx from the tagged PDMS template (`builder_pdms.py`: `compute_pdms` figures + `build_pdms`); the sample's own tables are filled; labour shows days and day rate (shift length from the Encoding); transport lines sit in the consumables table; PDF export works (user saw it).
- Hotbed line under Manpower (rate 59 editable, currency, days, Include box); the user enters it by hand each time.
- Currencies: category defaults + per-cell currency (number left, sign right, drop-down, one click); totals in manats; box titles list currencies. Order: convert → mark-up → round line totals to 2 decimals. Mark-up 10 % in PDMS mode, 6.5 % otherwise.
- CBAR: rates group with Reload per currency/all, Settings → CBAR currencies, rate log + validation, Yes/No question for an unusual rate, last checked/last action, document-usage log (an unfetched built-in rate is logged as "default"), Inspector CBAR tab, Storage/snapshot entries.
- "PDMS overview" first sheet in the statistics workbook (formulas, exact days, stock codes, encodings) = same figures as the Word file; if it cannot be built the other sheets are still saved and the message says so.
- Re-match All buttons ask first and list consequences; notes under the buttons.
- Test fixes: `test_file_changes` left `QMessageBox.warning` replaced (caused the 120 s Generate hangs); harness resets PDMS state; assertions that could never fail were rewritten.
- Docs (CHANGELOG, README, USER_GUIDE, tests/README) updated; no real names/job codes in code or tests (customer and signer defaults only in `template_config.json`).

## In progress / not fully done
- Task log: release rows for the built work are written (see /tasklog of 2026-10-28); MA127 15 %, MA124 20 %.
- Not verified on the real Windows app / Word / Excel (user checks at build time, tracked in MA204).

## Decisions (with reasoning)
- Unusual CBAR rate is always logged, never silently dropped: the user is asked and may approve it (it may be right); an unreadable entry keeps the old rate.
- Currency is stored on the rate cell (no extra column); only turned-on currencies can be chosen.
- PDMS template is shared by the department, not in git; the Word proposal has no stock-code column (the overview sheet does).
- Hotbed: with rate and days it is calculated whatever Include says; incomplete → inserted as given only while Include is ticked.
- Wording/capitals/visual choices wait for the commercial department's first look (MA205); parked rows are changed at the end of feature development.
- I never commit or push (the user does) and suggest commit titles; Windows checks are not listed as issues.

## Next
1. User builds the .exe and runs the Windows checks (MA204). 2. Then /release for 1.0.22 (VERSION already 1.0.22). 3. Next version: different Onshore schedule text (MA201).

## Open questions
- MA201: Onshore schedule-of-activities text stays the same for 1.0.22; to be made different in the next version.

## Parked
- MA124, MA127–MA130, MA133, MA194 stay until feature development ends; MA164, MA193 are the user's tests; Disparities segfault (crash.log 2026-10-15) not reproduced; pushing the older local commits is the user's call.

## Windows checks
- Generated .docx in Word: no repair prompt, tables/fills/page break/header/footer; PDMS overview sheet in Excel (change a rate).
- Reload per currency against the real CBAR site; the Yes/No question; Settings → CBAR currencies.
- In-cell currency drop-down (click the right half), NON-RECHARGEABLE from the left, Re-match All question.
- Inspector CBAR tab (9 tabs fit; hover/click); PDMS checkbox does not raise the window's minimum height.
- Part 1 (MA197–MA200): Qty drop-down in the toolbar, grey cells with a multi-sheet pair, wheel over a drop-down.
