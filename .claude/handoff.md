# Handoff — 2026-11-03

## Goal
MR vs CTR Comparator 1.0.25 — match modes + Proposal mode (PDMS MR vs proposal). VERSION is 1.0.25 (new release, bumped on the user's request); the .exe has not been rebuilt.

## Done
- Match by (Stock Code then Description / Stock Code only / Description only), remembered in QSettings `compare/match_mode`; keys in `comparator/match_keys.py`, selector on the hint line above Compare. Verified: `tests/test_match_modes.py` (9 pass, incl. the window's minimumSizeHint 444 px after Compare).
- Proposal mode: checkbox next to Match by; CTR Browse and "Locate it…" accept `.docx`; `comparator/proposal_parser.py` reads the items table (totals rows skipped, Transportation is a normal line). Verified on the real PDMS pair (TEST_Files/MRvsCTR/PDMS MR vs CTR): 13 matched / 2 only MR / 3 only proposal; `tests/test_proposal.py` (6 pass).
- Proposal mode view: Stock Code header shown as "Identifier"; Identifier, Rechargeable (MR), Allocation, Days (MR/CTR), Local cost start hidden (`PROPOSAL_HIDDEN_COLS`, `_load_view`); Rate-vs-Rechargeable check skips `.docx` rows.
- Fixed: columns hidden by the user came back when the statistics finished (`_apply_local_costs` re-hides the tail columns; test fails without the fix).
- The small window under `just run` was NOT the app: WSLg's /mnt/wslg/weston.log shows the Windows RDP client reported desktopScaleFactor 200 (UI drawn 2x) until 12:40 and 0 after a reconnect at 14:21 (UI drawn 1x). Fix is on the Windows side (`wsl --shutdown`); no repo change. Not yet confirmed by the user after the restart.
- Items without a Stock Code (forklift, truck, minibus, scaffold, tool box) are kept by both parsers when they have a Description + Quantity > 0 (not totals/signature rows), compared by Description, and EXCLUDED from the cost statistics (`_build_stats_items` uses `_CODE_`). Verified: `tests/test_no_code_items.py` (6 pass) and a scan of the real sample files (only real items kept, no junk). Full-suite rerun was started after this change; check `scratchpad/full6.txt` result before trusting it.
- VERSION, CHANGELOG [1.0.25], README, USER_GUIDE updated; setup.iss reads VERSION (no diff). Full offscreen suite: 329/330, the one failure (Generate test, a 6100 s clock gap) passes alone, so 330/330.

## In progress / not fully done
- Nothing half-built. NOT verified: the real Windows app / a rebuilt .exe; the "Locate it…" dialog for a moved `.docx` (needs a moved file); the Excel report/statistics with a proposal (SAGE cost is empty for rows matched by Description).
- USER_GUIDE "Step 3 — Settings" still has an old paragraph about a "Join key" dropdown that the app no longer has (not touched).
- Saved comparisons do not remember the match mode or Proposal mode (shown as normal).

## Decisions (with reasoning)
- Price of a proposal is display only (Rate column), never compared: the MR has no price; SAGE price needs a Stock Code.
- Description key = letters and digits only, upper case; unmatched rows keep their Stock Code (a first version gave them their description and broke 7 tests, plus merged same-description rows).
- Only the header text becomes "Identifier"; the data column stays "Stock Code" (25 uses in app.py, exports, statistics).
- "Local cost" / "Local cost total (MR/CTR)" are one hiding family (the toggle renames them).
- Win lock files `~$…` appear in TEST_Files when a sample is open in Word: tests skip them.

## Next
1. Build the .exe and run the Windows checks (list below). 2. Then the loose ends under In progress / Parked.

## Open questions
- Where exactly should non-stock-code items be added (Proposal mode tables, Only-in lists, parser)?

## Parked
- Possible follow-ups: alias support for near-identical descriptions (e.g. "SUPERWOOL … 25MM"), remembering the match mode in saved comparisons, fixing the stale "Join key" text in USER_GUIDE.

## Windows checks
- Items without a Stock Code: a CTR with forklift/truck/minibus lines shows them (Only in CTR or Matched by Description); the Statistics tab does not list them; "ⓘ" note in Step 2 says they are compared by Description only.
- Match by dropdown + Proposal mode checkbox fit on the hint line; window still fits maximised; mode survives closing and reopening.
- Compare per mode on a real multi-sheet MR + CTR: counts as expected (default mode keeps the old result for files with unique codes).
- Proposal mode: Browse offers the `.docx`; MR + proposal gives about 13 / 2 / 3; Columns reads "Columns (6)"; with SAGE set, Days and Local cost stay hidden after the costs fill and after ticking "Local cost total"; right-click header / Columns bring them back.
- Move a loaded proposal, reload, "Locate it…" offers Word files. (There is no drag-and-drop in the app.)
- `just run` looks small again only if WSLg lost the Windows scale (see Done); `wsl --shutdown` restores it.
