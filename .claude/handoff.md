# Handoff — 2026-11-24 (part 10)

## Goal
1.0.26 part 10: logging + Inspector for purchase orders and the CTR Tracker, then a test Windows build. Version stays 1.0.26. Parts 1-9 are committed (part 9 = ce510bc, purchase orders); part 10 is UNCOMMITTED.

## Done
- Logging (part 10): `app.log` has one line per step for POs and the Tracker (PO read/refused, tracker rows read with seconds, suggestions, row picked, values already in the tracker and the answer, write, result, failed write, CTR batch, Settings list saved). Buyer data is never read or logged. Verified: 11 new tests `tests/test_po_logging.py` (log lines captured, no e-mail in the log), and a real run on the two sample POs.
- Inspector: Data tab lists "Purchase orders (items)" and "Tracker rows (PO picker)", Storage lists PO files and picked CTR files, Variables shows PO counts only, Tasks shows the tracker-row read next to the PO write; Snapshot carries counts, no PO/CTR text (tested with `build_snapshot`). Accessors `inspector_items/files/rows_state` on `POSectionWidget`.
- Full suite 462 passed, 0 failed, 0 skipped.
- Docs: CHANGELOG, USER_GUIDE (new title/contents/numbered parts, PO in the Tracker part, Inspector rows), tests/README; task log rows MA258 (PO, 85 %), MA260 (logging), MA261 (guide), MA124 -> 60 %, MA130 -> 85 %; General Form checked.
- Windows test build made from WSL with the Windows Python (PyInstaller 6.21): `dist/Commercial-automation.exe` (98.6 MB, 2026-11-24 10:46). Checked in the build tables: rapidfuzz (Python + compiled .pyd incl. AVX2) and pypdf and `ctr_tools.po/po_view` are bundled; only optional-extra warnings for pypdf. I ran `pip install rapidfuzz` on the Windows Python (3.14.6) for this.
- The user tested a multi-item PO ("works as intended"): the multi-item layout is verified.

## In progress / not fully done
- NOT verified: the exe has not been started; PO reading/suggestions inside the exe; real Excel opening of a written tracker; tooltips in the PO group (a style sheet on a container may turn them black).
- PO feature (MA258, 85 %) stays below 100 % until the Windows checks and the commercial answers below.

## Decisions (with reasoning)
- Delivery date -> column Q ("PO Valid from"), AG left empty, T = PO value in the row's currency: to be confirmed by commercial staff (config values, easy to change).
- Snapshot/Variables for the PO section list counts only (the generic variable dump would print row snippets).
- Test waits use `H.wait_stats(win)` (the statistics job starts 400 ms after Compare, so "no worker running" is true too early).
- The user commits; no Co-Authored-By line. Build artifacts (`dist/`, `build/`) are git-ignored.

## Next
Commit part 10 (the user), then the user starts `dist/Commercial-automation.exe` and tells me: I read `logs/app.log`, `crash.log` and the Inspector Snapshot for errors; then the Windows checks below.

## Open questions
- Consumables mark-up (AU): CTR's amount (on full consumables) or the rate on the reduced amount? (unanswered)
- Commercial staff: delivery date column Q, meaning of T, whether AG is filled.

## Parked
- MA124, MA128, MA129, MA133, MA194 stay until feature development ends; MA242 after release; MA130 notes work.
- Docs were checked by keyword against the CHANGELOG only, not line by line against the code (user may point at a stale section).

## Windows checks
- Start the exe (no import error in `logs/app.log`); load both sample POs and a multi-item one; type in the CTR box (list, Up/Down only open it, Enter/click picks); pricebook warning and yellow reminder; write into a tracker copy and open it in Excel (A and AK formulas after Ctrl+Alt+Shift+F9).
- Tooltips in the Purchase Orders group; Settings > PO company short names; window still fits.
- Inspector F12: Data / Storage / Variables / Tasks show the PO state; the log lines appear in the Logs tab.
