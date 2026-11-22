# Handoff — 2026-11-21

## Goal
CTR Tracker + CTR Generator fixes, version 1.0.26 (not bumped; this work continues the 1.0.26 section). UNCOMMITTED.

## Done
- Tracker: scaffold lines of the USD CTR's Pricing-sheet equipment block go to AO and are taken out of AN (`tracker._scaffold_total`, `tracker_fast.write_entries_fast`). Checked on the real scaffold CTR (81,421.74 of 88,717.74) and tracker copies.
- Tracker: every cost column is filled whatever the CTR currency; AZN figures are written as `=x/1.7`, USD as numbers, zero = empty. The user checked 337 in Excel: all figures match.
- Tracker: the row's cost columns, date, value and revision are cleared before a new CTR is written; soft fields (client, location, project code, job type) kept.
- Tracker: Value rounding to 6 digits fixed (`tracker_window._value_text`, was `{:g}`).
- Settings > Tracker company codes (Edit / Reset / Restore, saved in the user's template_config.json); order tests company/location.
- Generator: Save statistics (Excel) works without equipment/consumables (Manpower + Price Totals sheets, now in every export); Date = today after a request loads; remembered paths survive a missing file and are saved after Load Files; Short/Long selector hidden outside PDMS.
- Verified: full offscreen suite 415 passed, 0 failed (48 files, sandbox only). CHANGELOG (1.0.26), README, USER_GUIDE updated; task log rows MA245-MA253 added, General Form checked.

## In progress / not fully done
- Item 4 (replace-or-new prompt when an older revision exists) and item 8 (PO in the Tracker): not started, no rows in the task log yet.
- NOT verified: Windows UI, the Excel row-insert path (Linux cannot run it), real Word. 337 was checked in Excel by the user, but the 281 AZN rev1 test file has typed Summary numbers (2, 12, 1223) that do not match its own sections, so its Tracker rows look odd.
- MA130 (PDMS notes: overview-sheet block, Settings row "PDMS notes", price remarks) untouched this session.

## Decisions (with reasoning)
- Tracker cost columns are USD whatever the CTR currency; AZN is divided by `azn_to_usd_rate` (like Labor always was), because an AZN CTR can hold everything (337).
- Scaffold only from the equipment block (labour rows "Scaffolder" also contain the word); AX = SUM(AL:AW)-AS already includes AO, so AX unchanged.
- Item 4 design: prompt only when an older revision of that CTR exists; the user picks which old row to replace, or a new row. The rule "same CTR+currency+revision overwrites in place, different revision gets its own row" stays otherwise.
- Company code always overrides the project code; matching is the CTR's Client text, exact, capitals ignored.
- Date: today after every request load (new request overwrites a typed date). Statistics Excel: option (a), Manpower + Price Totals in every export.

## Next
Plan item 4 (touches `_classify_target_row`, `_plan_insertions`, `tracker_window`; the insertion path needs Windows) and show the user the plan before editing. Then ask what PO (item 8) should do.

## Open questions
- Consumables mark-up column AU uses a fixed 6.5 %; 337 has 10 % (43.67 USD difference). Use the CTR's own mark-up?
- Scaffold materials in the consumables block (e.g. "SCAFFOLD BOARD END BAND" in 337): count and take out of consumables?
- Pricebook paths not remembered between sessions: not reproduced; the user dropped it after the fix. Tell me if it recurs (note the drive/OS).

## Parked
- MA124, MA128, MA129, MA133, MA194 stay until feature development ends; MA242 (colleagues Merge shipped for 5 % transport mark-up) after release.

## Windows checks
- Tracker: write a scaffold USD CTR and 337 into a real tracker copy; check AN, AO, AX after Ctrl+Alt+Shift+F9; load a CTR over leftover values; Settings > Tracker company codes (dialog, Restore) and the Settings window still fits.
- Generator: Save statistics for a manpower-only request (Manpower, Price Totals sheets); Date is today after loading a request; PDMS tick shows/hides the Short/Long selector without changing the window height; start with a pricebook drive disconnected, reconnect, restart.
