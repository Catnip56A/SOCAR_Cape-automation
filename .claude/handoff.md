# Handoff — 2026-11-05

## Goal
MR vs CTR Comparator 1.0.25 — match modes, Proposal mode (PDMS MR vs proposal), items without a Stock Code. VERSION is 1.0.25 (new release); committed as c960349; the .exe has not been rebuilt.

## Done
- Match by (Stock Code then Description / Stock Code only / Description only), remembered in QSettings `compare/match_mode`; keys in `comparator/match_keys.py` (`assign_keys`, also sets `_CODE_`), selector on the hint line above Compare. Verified: `tests/test_match_modes.py`, window minimumSizeHint 444 px after Compare.
- Proposal mode: checkbox next to Match by; Browse and "Locate it…" accept `.docx`; `comparator/proposal_parser.py` reads the items table (totals skipped, Transportation is a normal line). Verified on the real PDMS pair: 13 matched / 2 only MR / 3 only proposal; `tests/test_proposal.py`.
- Proposal-mode view: Stock Code header shown as "Identifier"; Identifier, Rechargeable (MR), Allocation, Days (MR/CTR), Local cost start hidden (`PROPOSAL_HIDDEN_COLS`, `_load_view`); Rate-vs-Rechargeable check skips `.docx` rows.
- Items without a Stock Code (forklift, truck, minibus, scaffold, tool box) are kept by both parsers when they have a Description + Quantity > 0 (not totals/signature rows), compared by Description, excluded from the cost statistics (`_build_stats_items` uses `_CODE_`). Verified: `tests/test_no_code_items.py` and a scan of the real sample files (only real items kept).
- Fixed: columns hidden by the user came back when the statistics finished (`_apply_local_costs`; the regression test fails without the fix).
- Full offscreen suite 336/336 after the last code change (5 old tests updated: their row counts were +3 for the kept no-code lines).
- Task log updated 2026-11-05: MA124 25 %, MA127 28 % (name and Remaining text corrected), MA233–MA236 added, 1.0.25 summary in `short_summaries.json` (uncommitted).
- "Window looks small under just run" was NOT the app: /mnt/wslg/weston.log shows the Windows RDP client reported scale 200 until 12:40 and 0 after a reconnect at 14:21 (UI drawn 1x). Fix is on the Windows side (`wsl --shutdown`, user ran it); no repo change.

## In progress / not fully done
- Nothing half-built. NOT verified: the real Windows app / a rebuilt .exe; "Locate it…" for a moved `.docx`; the Excel report and statistics with a proposal (SAGE cost is empty for rows matched by Description).
- USER_GUIDE "Step 3 — Settings" still has an old paragraph about a "Join key" dropdown the app no longer has.
- Saved comparisons do not remember the match mode or Proposal mode (shown as normal).

## Decisions (with reasoning)
- A proposal's price is display only (Rate column), never compared: the MR has no price and SAGE needs a Stock Code.
- Description key = letters and digits only, upper case; unmatched rows keep their Stock Code (a first version used the description and merged same-description rows).
- Only the header text becomes "Identifier"; the data column stays "Stock Code" (25 uses: exports, statistics).
- Items without a code stay out of the statistics (user's choice); scaffold is kept too, so it shows under Only in CTR when the MR has no match.
- Task log `--comment` REPLACES the cell: pass the old history + " | " + the new entry.
- Win lock files `~$…` appear in TEST_Files/the repo root when a file is open in Word/Excel: tests skip them, the task log stops on them.

## Next
1. Build the .exe / installer and run the Windows checks below. 2. Then the loose ends (In progress, Parked).

## Open questions
- None.

## Parked
- Alias support for near-identical descriptions (e.g. "SUPERWOOL … 25MM"); remembering the match mode in saved comparisons; fixing the stale "Join key" text in USER_GUIDE.
- MA124, MA127–MA130, MA133, MA194 stay until feature development ends; MA205 waits for the commercial department.

## Windows checks
- Match by dropdown + Proposal mode checkbox fit and are readable; window still fits maximised; the mode survives closing and reopening.
- Compare per mode on a real multi-sheet MR + CTR: default mode keeps the old result for files with unique codes.
- Proposal mode: Browse offers the `.docx`; the PDMS pair gives about 13 / 2 / 3; Columns reads "Columns (6)"; with SAGE set, Days and Local cost stay hidden after the costs fill and after ticking "Local cost total"; Columns / header right-click bring them back.
- Items without a Stock Code: a CTR with forklift/truck/minibus lines shows them (Matched by Description or Only in CTR); the Statistics tab does not list them; the Step 2 note says "compared by Description only".
- Move a loaded proposal, reload, "Locate it…" offers Word files. (The app has no drag-and-drop.)
