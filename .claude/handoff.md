# Handoff — 2026-11-03

## Goal
CTR Generator / PDMS / MR vs CTR Comparator 1.0.24 — complete (VERSION 1.0.24, the same version throughout; the .exe has not been rebuilt). Part 4 (below) is committed together with this handoff.

## Done (full offscreen suite 315/315; the user ran the full real-app check A–H on 2026-11-03: everything OK)
- Part 4 (2026-11-03): after Column keywords change the app asks "Re-read the loaded files?" (No leaves them marked ⟳ + the Reload changed files button; every parsed table carries `"kw"` = keywords fingerprint); an MR page naming another CTR gets "⚠ other CTR" in Step 2 (warn only, stays ticked; `"ref"` read by `sheet_parser.find_ctr_ref`); Settings › Template config = the user's own copy of `template_config.json` in the data folder (Open file / Merge shipped / Open folder / Reset / Restore); CTR presets also save Customer, Customer city, Signer, Signer title.
- Earlier in 1.0.24 (committed): PDMS days/day-rate display, skip-with-warning, overview header, Hotbed "Reload standard", `=` formulas (also written into Excel), Reload all + Ctrl+Shift picks, Manpower in statistics, Column keywords editor (+ Test on a file, Import/Export), parser notes, "No tables" / "Problems found" reports, NaT fix.
- Task log updated 2026-11-03: MA227, MA228, MA229, MA231 closed, MA230 closed as not needed, MA232 (presets) added; summaries for the three 1.0.24 groups are in `.claude/task_log/short_summaries.json`.

## In progress / not fully done
- Nothing open for 1.0.24. NOT verified: a rebuilt .exe / the real Windows app (the user's checks ran under WSL).

## Decisions (with reasoning)
- Template config uses "full copy + Merge shipped" (the user's choice, like Stock Code types / Column keywords): a later shipped change to an existing key does NOT reach an existing copy (Merge shipped adds only missing keys, Reset replaces everything); Settings shows the number of differing values. Template positions apply at the next start; only the PDMS values re-read at once (Hotbed button).
- CTR load speed was dropped: measured 0.8 s (the CTR) and ≤3.6 s (biggest MR); the "16,379 columns" is only declared, pandas reads 75 × 52.
- Column keywords: one list for naming columns AND finding header rows; `=word` exact, otherwise matches from the start of a word; case/punctuation ignored; the most specific word wins.
- The user dropped two ideas on 2026-11-03: a "Teach headers…" button and an in-app editor for Template config (Open file opens the system's default program, e.g. Chrome — accepted).

## Next
1. Build the .exe / installer and run the Windows check (repeat A, B, D, G of the 2026-11-03 list). 2. Otherwise nothing planned; ask the user for the next task (then `/pickup`).

## Open questions
- None.

## Parked
- MA124, MA127–MA130, MA133, MA194 stay until feature development ends; MA205 waits for the commercial department; Disparities segfault (crash.log 2026-10-15) not reproduced.

## Windows checks
- None open now (they happen at build time). After the build: Column keywords + the re-read question, the MR page hint, Template config row, formulas, Reload all + Ctrl+Shift picks, Hotbed Reload standard, presets with the PDMS header, window size with PDMS ticked.
