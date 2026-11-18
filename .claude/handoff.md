# Handoff — 2026-11-18

## Goal
CTR Generator / PDMS proposal, version 1.0.26 (not bumped): part 2 small fixes (committed by the user), part 3 = PDMS proposal notes (MA130), UNCOMMITTED.

## Done
- Transport lines share one mark-up; editing asks All lines / Only this line; exceptions (fuel 0 %) in italics; right-click changes status without a figure; shipped vehicle mark-up 5 % (incl. `_default`). `window.py` `_on_transport_markup_edited`, `_shared_third_party_markup`.
- Settings > Template config > Merge shipped lists differences, takes ticked ones, warns before replacing, backup restorable (`config.py` `config_differences`, `settings_view._ConfigMergeDialog`).
- PDMS notes: `ctr_tools/pdms_notes.py` (JSON list `pdms_notes.json`, places with pre-tick + order, placeholders, remembered values, search), `pdms_notes_view.py` (floating window, note editor, New placeholder, Manage values), `builder_pdms._fill_notes` + tags in `TEST_Files/CTR creator/PDMS_TEMPLATE_short_v2.docx`; Notes button red until opened (red again for a new CTR Request / Job Ref), Generate asks Continue/Cancel (Settings > Generate switch); 18 shipped optional notes in every place, never pre-ticked, with hints; Generate warns when the template has no note tags.
- Verified: full offscreen suite 380 passed 0 failed; generated proposal old vs v2 rendered with LibreOffice (same text, numbering, 3 pages); window rendered offscreen. User confirmed notes work after selecting the v2 template.

## In progress / not fully done
- MA130 at 50 %: notes block in the PDMS overview sheet (`pdms_overview.py` `add_overview_sheet`) not built; Settings row "PDMS notes" (Edit/Reset/Restore) not built (model: `source_choices` + `_saved_row`, `check_file()` exists); price remarks under the totals not yet a place; notes per category and scope filters undefined.
- NOT verified: real Word rendering (only LibreOffice), Windows UI, window width at 1280 px.
- MA242: colleagues' own `template_config.json` keeps 6.5 % until they use Merge shipped; not yet shared.

## Decisions (with reasoning)
- Note kinds are only numbered | bullet: "header" had no visible effect (an old file's "header" loads as numbered).
- Shipped notes merge into an existing list on load (`merge_shipped`); a deleted shipped note is tracked in `removed` and stays deleted. Hints (ONLY AZN, CHECK …) are shown, searched, never written to Word.
- `{markup_third_party}` = the shared transport mark-up (empty when every line is an exception); `{markup_materials}` = Markup Rate box; a typed value wins over what the app fills.
- Reload rebuilds transport lines from `transport_rates`; exceptions are re-derived (typed mark-ups lost, as before).
- User's own wording kept ("it will calculated as a full day", trailing `;`).

## Next
Build the Settings row "PDMS notes" in `common/settings_view.py` (copy the Rechargeability choices row; use `copy_backup`, `pdms_notes.check_file`), then the overview-sheet block.

## Open questions
- Make the two price remarks under the totals ("indicative quotation", VAT) a fourth place?
- "CHECK" note: third-party mark-up shows 5 % (was 10 % in the user's text) — correct?
- How to give colleagues `PDMS_TEMPLATE_short_v2.docx` (TEST_Files is git-ignored)?

## Parked
- Alias support for near-identical descriptions; remembering the match mode in saved comparisons; stale "Join key" paragraph in USER_GUIDE Step 3.
- Excel report / statistics with a proposal (SAGE cost empty for rows matched by Description) not checked.
- MA124, MA128, MA129, MA133, MA194 stay until feature development ends.

## Windows checks
- Generator PDMS mode: Notes button row (red, then normal), floating window wrapping/size/position after restart, typing in a placeholder box (popup must not steal focus), New placeholder / Manage values dialogs.
- Transport table: question on a Mark Up % edit, italics, right-click menu, sorting/editing; Settings > Template config > Merge shipped dialog and Restore.
- Disparities Choose for selected, Hotbed Include after restart, empty Output folder with stale paths.
