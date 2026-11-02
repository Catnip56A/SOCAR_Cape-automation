# Handoff — 2026-11-02

## Goal
CTR Generator / PDMS / MR vs CTR Comparator 1.0.24 — built and committed as the release (parts 1–3, last commit 45c0a91); VERSION is 1.0.24; the .exe has not been rebuilt.

## Done (full offscreen suite 304/304 on the committed state; no real-Windows run yet)
- PDMS: Manpower shown in days / day rate (cells keep hours); rows with no shift length skipped after a Yes/Cancel warning; overview sheet header lists the Word letter + CTR header fields; CTR header rows stay visible in PDMS mode; Hotbed "Reload standard" button (re-reads `template_config.json` pdms block; days untouched).
- CTR Generator: `=` formulas in every number cell/box (also written as Excel formulas into the AZN/USD CTRs, the PDMS overview and statistics Price Items); hover meanings; "Reload all" + Ctrl+Shift picks (rows × columns) + saved-rename question; Manpower list in the Cost statistics box. The user checked formulas, days view, reload/picks/rename popup in the real app (WSL).
- MR vs CTR: CTR parser finds a Stock Code column without a header, never drops a line silently (Parse Notes in Step 2 / status / report sheet), tolerant header rows ("Tot", "CTR total"), warnings for unrecognised header / duplicate column / missing Quantity or Description (names the unrecognised headers), "No tables found" and "Problems found in the files" dialogs + red ⚠ file entry, `NaT` column fixed.
- Settings › Column keywords (`comparator/column_keywords.py`, `common/keywords_view.py`): editable header words and MR / CTR equipment / CTR consumables header rows; Edit, Merge shipped, Undo my entries, Reset, Restore, Import/Export, Test on a file (grid of found tables), checks before saving; Stock Code types got Undo my entries + the same More ▾ menu. Verified by tests only.
- Task log updated 2026-11-02 (MA215–MA231 added, MA124/127/205 raised); `.claude/task_log/short_summaries.json` has the new summaries but is NOT committed.

## In progress / not fully done
- Column keywords (MA227, 90 %): not confirmed on the real app. The user's test failed only because the word was `=Descript` while the file header is `Descrip` (checked 2026-11-02); after the spelling is fixed and the file reloaded it should work.
- Saved Column keywords do not re-read files that are already loaded (user must reload; "Reload changed files" only watches the file on disk) — MA228.

## Decisions (with reasoning)
- Keywords: a full copy in the user data folder seeded from the shipped lists (like Stock Code types), not additions-only, so the user can delete shipped words; one list for naming columns AND finding header rows; `=word` = exact header, otherwise starts-at-a-word match, case/punctuation ignored, most specific word wins.
- Excel formulas are stored in a role beside the cell value and live only while the cell still holds that value (no hooks in re-match / reload code).
- The installed .exe reads the `template_config.json` bundled in it (edits apply to dev runs only) — user wants an editable copy later (MA231).

## Next
0. REMINDER (user asked 2026-11-02): after the evening items are done, run `/tasklog` to add them — MA228–MA231 are logged as planned (0 %); raise them and add any new work; remind the user at the end of the session.
1. Commit `.claude/task_log/short_summaries.json` (and this file). 2. The evening items the user planned (task log rows MA228–MA231): re-read loaded files after saving Column keywords; MR-page-refers-to-another-CTR warning; faster CTR load (Pricing sheet declares 16,379 columns); editable `template_config.json` for the .exe.

## Open questions
- None unresolved.

## Parked
- MA124, MA127–MA130, MA133, MA194 stay until feature development ends; MA205 waits for the commercial department; Disparities segfault (crash.log 2026-10-15) not reproduced.

## Windows checks
- None open (they happen at build time). After the .exe is built: formulas, days view, Reload all + Ctrl+Shift picks, Column keywords editor + Test on a file, the "Problems found" dialog with CTR-26-319, Hotbed Reload standard, window still fits when maximised.
