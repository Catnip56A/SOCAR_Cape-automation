# Handoff — 2026-11-24 (part 10)

## Goal
1.0.26 part 10: logging + Inspector for purchase orders and the CTR Tracker, then a test Windows build. Version stays 1.0.26. Parts 1-9 are committed (part 9 = ce510bc, purchase orders); part 10 is UNCOMMITTED.

## Done
- Logging (part 10): `app.log` has one line per step for POs and the Tracker (PO read/refused, tracker rows read with seconds, suggestions, row picked, values already in the tracker and the answer, write, result, failed write, CTR batch, Settings list saved). Buyer data is never read or logged. Verified: 11 new tests `tests/test_po_logging.py` (log lines captured, no e-mail in the log), and a real run on the two sample POs.
- Inspector: Data tab lists "Purchase orders (items)" and "Tracker rows (PO picker)", Storage lists PO files and picked CTR files, Variables shows PO counts only, Tasks shows the tracker-row read next to the PO write; Snapshot carries counts, no PO/CTR text (tested with `build_snapshot`). Accessors `inspector_items/files/rows_state` on `POSectionWidget`.
- Full suite 462 passed, 0 failed, 0 skipped.
- Docs: CHANGELOG, USER_GUIDE (new title/contents/numbered parts, PO in the Tracker part, Inspector rows), tests/README; task log rows MA258 (PO, now 100 %), MA260 (logging), MA261 (guide), MA124 -> 60 %, MA130 -> 85 %; General Form checked.
- Windows test build made from WSL with the Windows Python (PyInstaller 6.21): `dist/Commercial-automation.exe` (98.6 MB; mine 10:46, rebuilt by the user 10:55 — the later one was tested). Checked in the build tables: rapidfuzz (Python + compiled .pyd incl. AVX2) and pypdf and `ctr_tools.po/po_view` are bundled; only optional-extra warnings for pypdf. I ran `pip install rapidfuzz` (3.14.6) on the Windows Python 3.13 for this.
- Verified by the user on the Windows build (2026-11-24): the app works, single and multi-item POs work as intended, no errors in the logs for a week. MA258 (purchase orders) is therefore 100 % in the task log.
- Start test by me: the exe starts ("Main window shown" 5 s after start), `crash.log` empty, no import errors.

## In progress / not fully done
- Nothing built is unfinished. Not checked by me (only reported by the user as working): the details of each Windows check, for example the tooltip colours in the PO group and the Excel formulas after a write.
- Waiting on commercial staff (config values only, not code): the delivery-date column Q, the meaning of T, whether AG is filled.

## Decisions (with reasoning)
- Delivery date -> column Q ("PO Valid from"), AG left empty, T = PO value in the row's currency: to be confirmed by commercial staff (config values, easy to change).
- Snapshot/Variables for the PO section list counts only (the generic variable dump would print row snippets).
- Test waits use `H.wait_stats(win)` (the statistics job starts 400 ms after Compare, so "no worker running" is true too early).
- The user commits; no Co-Authored-By line. Build artifacts (`dist/`, `build/`) are git-ignored.

## Next
Commit part 10 (the user). Then the next part of 1.0.26 (the user decides what part 11 is) or the commercial answers above; if the answers differ from Q/T/AG, change `ctr_tracker_po` in `template_config.json` (and `config.py`), no code.

## Open questions
- Consumables mark-up (AU): CTR's amount (on full consumables) or the rate on the reduced amount? (unanswered)
- Commercial staff: delivery date column Q, meaning of T, whether AG is filled.

## Parked
- MA124, MA128, MA129, MA133, MA194 stay until feature development ends; MA242 after release; MA130 notes work.
- Docs were checked by keyword against the CHANGELOG only, not line by line against the code (user may point at a stale section).

## Windows checks
Done by the user on the 2026-11-24 build (PO reading, the CTR box, writing, Inspector, logs). Remaining, only if something changes: after the next build start the exe once and look at `logs/app.log` for errors; if the build is rebuilt after a dependency change, check `rapidfuzz` and `pypdf` are still in `build/app/PYZ-00.toc`.
