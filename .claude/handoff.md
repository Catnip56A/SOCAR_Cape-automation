# Handoff — 2026-11-19

## Goal
CTR Generator / PDMS proposal, version 1.0.26 (not bumped): short + long proposal from one request, work schedule choice, PDF through Word. UNCOMMITTED (together with the part 3 notes of the previous handoff, if not yet committed).

## Done
- Short/Long selector next to the PDMS tick box + two pickers "PDMS Template (short)" / "(long)", only the chosen one required (`window.py` `_pdms_kind`, `_pdms_template_path`); kind and both paths remembered. Offscreen tests (`test_pdms_kind.py`, incl. a real Generate for both kinds).
- Work schedule choice in the notes window (`pdms_notes.SHIPPED_SCHEDULES`, `NewScheduleDialog`): 4 shipped + own, unselected until chosen, sets location/days/hours/`{overtime_days}`/coefficient, Generate warns when none is chosen; saved `sched-overtime` with the old text is updated on load. Tests in `test_pdms_notes*.py`.
- Long template: `TEST_Files/CTR creator/PDMS_TEMPLATE_long.docx` = tagged copy of `PDMS_old/long proposal.docx` (tagging script was in the scratchpad, lost). Builder: Position/Shift columns, mark-up columns, own Table D, letters A-D, cover text boxes, optional summary table, clear error for an untagged proposal. `test_builder_pdms_long.py`; rendered with LibreOffice; user generated a long proposal and a CTR on Windows: works.
- PDF by Word (`pdf_exporter.export_docx_to_pdf`): PowerShell/COM, falls back to LibreOffice; `CTR_PDF_ENGINE=libreoffice` forces LO (tests set it). Run for real from WSL (Word 22-25 s, fallbacks without Word / without PowerShell / on timeout checked by hand). User confirmed the PDF is good.
- Suite: full run 393 passed 0 failed, then `-k pdms` 76 and `-k pdf` 3 passed after the last changes; the whole suite was NOT re-run after the final pdf_exporter edits.

## In progress / not fully done
- CHANGELOG (1.0.26 section, date kept 2026-11-15), README and USER_GUIDE updated for this work (version stays 1.0.26, decided by the user); task log updated (MA128 80 %, MA130 60 %, MA124 55 %). Docs not yet re-read in rendered form.
- MA130 (from before): notes block in the PDMS overview sheet (`pdms_overview.py`), Settings row "PDMS notes", price remarks place — still not built.
- Not verified: Word rendering of the long template layout beyond the user's check; Python running natively on Windows (path code untested); a machine with neither Word nor LibreOffice (old "PDF skipped" path).
- Contents page numbers are updated only inside the PDF, not in the .docx (user must update the field in Word).

## Decisions (with reasoning)
- Builder adapts to the tags a template has (`{{T_DESC}}` = own third-party table, `{{L_POSITION}}` = long labour table); no "kind" flag in the builder.
- Terms and Conditions stay fixed text in the long template; "Dear Sir/Madam" fixed; "(Table A)" after schedule headings dropped (the user can add it to the `sched-head` note).
- Table D Q-ty = quantity x duration (no duration column); consumables show price before mark-up + mark-up column; transport mark-up caption = most common line mark-up.
- Word only started by us: on timeout stop only a new windowless WINWORD process; never kill others (the user runs Word/the app at the same time).

## Next
Commit (full suite 397 passed 0 failed just before), then the Windows checks below; no version bump.

## Open questions
- Two price remarks ("indicative quotation", VAT) as a fourth notes place? "CHECK" note shows third-party mark-up 5 % (was 10 %) — correct?
- How to give colleagues the templates (TEST_Files is git-ignored; the long one is ~10 MB)?
- User's saved `sched-standby` note ends with a stray `{night_coef}` — remove?

## Parked
- Alias support for near-identical descriptions; remembering the match mode in saved comparisons; stale "Join key" paragraph in USER_GUIDE Step 3.
- Excel report / statistics with a proposal (SAGE cost empty for rows matched by Description) not checked.
- MA124, MA128, MA129, MA133, MA194 stay until feature development ends.
- Test progress in `tests/run_all.py`: count the tests first and print `[n/total]` (and a rough time left) on every line, so a long run can be followed even through a pipe.

## Windows checks
- Short/Long selector + both Browse rows with the window at normal size; kind and paths after a restart.
- Notes window: Work schedule row (wrapping), Add/Delete schedule, red warning when none is chosen; Sunday/Saturday overtime line; short proposal overtime wording.
- Long proposal in Word: cover boxes, Tables A-D (Total/Shift columns on one line), empty optional tables disappear, 8.2 notes, Provided by client; PDF layering and contents page.
- PDF with Word closed and with another Word document open (it must stay open); on a PC without Word (LibreOffice fallback).
