# Handoff — 2026-11-24

## Goal
CTR Tracker purchase orders (backlog item 8) and the follow-ups of items 1-7, version 1.0.26 (not bumped; continues the 1.0.26 section). UNCOMMITTED.

## Done
- Tracker: older revision -> replace or new row (3 modes), the CTR's own editable consumables mark-up, scaffold materials in the consumables block count as scaffold. Offscreen tests on copies of the test tracker.
- Purchase Orders section (Tracker tab): `ctr_tools/po.py` (pypdf reader, suggestions), `po_view.py` (UI), `tracker_fast.read_tracker_rows` / `write_pos_fast`. Writes only H, I, C, M, Q, T, E of the picked row; existing different values are asked per PO; red warnings, yellow non-blocking reminders; a failed write keeps everything in the window (not saved to disk). Verified: `tests/test_po.py` 26 passed, plus real typing with QTest; written files read back; sample PDFs (2 real POs) parsed.
- Description suggestions = RapidFuzz on the description only (CTR number in the PO text = exact first); typed search exact, typo fallback per word, CTR numbers never fuzzy; the box is prefilled with the PO description (picks nothing).
- Settings > PO company short names (AIOC, BPSD, BPCS, BTC, GPC); nine more Tracker company codes; Type of PO defaults CONTRACT/LIMIT/LIMIT/FIX/STANDARD/STANDARD/FIX.
- Tests: 9 order-dependent failures fixed (`H.wait_stats`, used in 10 test files); `H.tracker_copy()` finds the test tracker whatever its capitals. Full suite 451 passed, 0 failed, 0 skipped (before the last CTR-box edit; `test_po` re-run after: 26 passed).
- Docs updated: CHANGELOG (1.0.26), README, USER_GUIDE, tests/README, justfile/app.spec (pypdf, rapidfuzz). Task log rows MA255-MA259 added, MA124 -> 60 %, MA130 -> 85 %; General Form checked.

## In progress / not fully done
- PO feature (MA258, 85 %): NOT verified on Windows/real Excel, nor the exe build (rapidfuzz is a compiled package, hidden import added only). Only two single-item sample POs were seen: multi-item layout (amount = last money figure of the item) is a guess; a real multi-item PO was requested by the user.
- Description matching cannot find a PO whose text shares no words with its CTR (PO 4301562851 vs row 8438): pick by CTR number.

## Decisions (with reasoning)
- Delivery date -> column Q ("PO Valid from"): in the real tracker R is empty in 2026 rows; AG (order date) left empty; T = PO value in the row's currency (AK divides T by 1.7 for AZN). To be confirmed by commercial staff.
- Type of PO is typed (not on the PDF), suggestions kept in QSettings `ctr_tracker/po_types`; defaults only when nothing is saved, so deleted ones stay deleted.
- The picked CTR file (pricebook check) is UI only, never written; forgotten when the CTR row changes.
- CLAUDE.md/memory: the user commits; no Co-Authored-By line.

## Next
Run the user's commit (commands in the chat), then ask what the "presentation" is (not specified in this session) before starting it.

## Open questions
- Consumables mark-up (AU): keep the CTR's amount (on full consumables) or the rate on the reduced amount? (unanswered)
- Commercial staff: delivery date column, meaning of T, whether AG is filled.

## Parked
- MA124, MA128, MA129, MA133, MA194 stay until feature development ends; MA242 (share template_config.json with 5 % transport mark-up) after release; MA130 notes work.

## Windows checks
- PO section: load both sample POs, type in the CTR box (list pops up, Down/Up only open it, Enter/click picks), pricebook warning and yellow reminder, write into a tracker copy, open in Excel (A and AK formulas after Ctrl+Alt+Shift+F9).
- Tooltips inside the Purchase Orders group (a style sheet on the group may turn them black); Settings > PO company short names; the window still fits (Tracker tab scrolls).
- Build: `pip install pypdf rapidfuzz`, then the exe starts and reads a PO PDF.
