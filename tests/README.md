# Offscreen tests

Plain Python, no pytest needed:

    python tests/run_all.py            # everything (~3 minutes: the first test loads the pricebooks and SAGE)
    python tests/run_all.py -k encoding  # only tests whose name contains "encoding"

They run the real windows with `QT_QPA_PLATFORM=offscreen` against the files in
`TEST_Files/CTR creator` (read-only). **Nothing real is touched**: the data folder,
QSettings and the aliases / renames / presets files are redirected into a temporary
folder before the app is imported, and generated CTRs go to a temporary output
folder (`tests/harness.py`; the path is printed). Use the same harness for any new test.

| File | Covers |
|---|---|
| `test_generate_rules.py` | nothing assumed, incomplete rows left out, builders refuse to guess, Stock Code not in SAGE |
| `test_quantities.py` | quantity 0 or less: warning, hidden transport lines, section left out |
| `test_encoding.py` | manpower Encoding, Onshore/Offshore separation, rows waiting for Load Files |
| `test_go_to_value.py` | Go to value (click, list), orange cell, statistics double-click |
| `test_find_bar.py` | Ctrl+F on tables, trees, proxy views |
| `test_hover_text.py` | full text of a cut-off cell on hover |
| `test_disparities.py` | documents vs SAGE rechargeability: not counted, choose a source per Stock Code, shared by both lists, remembered, Generate held back |
| `test_price_stats.py` | Document price: rate × quantity × days, non-rechargeable = 0, equipment from the pricebook / consumables from SAGE, markup and scaffold toggles and their Settings defaults, the editable rechargeability choices, the Generator's price totals, the MR vs CTR window and both Excel files |
| `test_window_size.py` | the window's minimum height after Compare stays small (MA164); Maximize / Hide results still work |
| `test_builder_pdms.py` | the PDMS proposal: tags, the sample's tables filled (header rows, fills, totals), hours → days and day rate, the Hotbed (calculated / inserted as given / left out), transport rows, per-line currencies, empty sections removed and tables renumbered, the window's PDMS mode |
| `test_reload.py` | Reload: rows know their request item and what the load gave them; Reload all restores the file's values and drops typed values / formulas; Ctrl+Shift picks take the picked rows × columns only; a changed file is picked up, vanished rows stay, new ones are added; hand-added rows are only priced again; the question before anything is replaced; the real worker re-reads the file; the buttons |
| `test_formulas.py` | `=` formulas in number cells: the parser (+ − × ÷, brackets, decimal comma, everything else refused), a formula kept beside its value and offered again in the editor, stale once something else rewrites the cell, refused when bad, written in days in PDMS mode, the hover text |
| `test_pdms_days_view.py` | PDMS mode shows the manpower table in days and a day rate (hours ÷ shift length, hourly rate × shift length, 2 decimals) while the cells stay in hours; Quantity / Rate / UOM labels; editing in days stores hours; a row without a shift length stays in hours with a red "!" and its reason |
| `test_pdms_overview.py` | the "PDMS overview" sheet: layout, formulas recalculated with LibreOffice Calc equal the Word totals, an incomplete Hotbed, editing a rate, the statistics export |
| `test_cbar_log.py` | CBAR: the rate log and validation, one line and Reload per turned-on currency, the Yes/No question for an unusual rate, last checked / last action, the documents-that-used-a-rate log and the Inspector's CBAR tab (hover, click, currency selector); the in-cell currency (sign, drop-down, one click, NON-RECHARGEABLE), totals in manats, box titles; the Hotbed in the price totals and its Include box; the 10 % / 6.5 % mark-up |
| `test_no_wheel.py` | the wheel never changes a drop-down (the page scrolls instead); spin boxes keep it |
| `test_qty_mode.py` | Compare Values Qty mode (all differences / only CTR < MR) in the detail and Combined views, remembered |
| `test_mismatch_navigation.py` | MR vs CTR results: header click cycle (asc / desc / original), arrows follow the sorted order, red-only / orange-only stepping, 200 filter suggestions |
| `test_tracker_calc.py` | the CTR Tracker's fast write path sets recalculate-on-open and the saved file still opens (temp copy of the test tracker) |
| `test_stock_types.py` | the Stock Code types list: file module (seeding, merge, reset), how a kind is decided and when the user is asked, the decisions window, the Settings editor, 'how to fix' hints, git-ignored and bundled |
| `test_mr_days.py` | MR days from the matching CTR, user choice when CTRs disagree, orange Days cell when none, typed days override, Excel Days note |
| `test_days_edit.py` | Days cells editable in the tables and the Statistics list, the sync pop-up (sync / only this side / cancel), per-side days, bad input, a real double-click, saved with the comparison |
| `test_waiting_and_inspector.py` | F12 Data tab previews for request lines / MR-CTR tables / tracker batch, ⏳ loading states, waiting marks in the tables and Generator, leaving an edited cell empty keeps its warning |
| `test_file_changes.py` | files edited on disk: parsed tables reused only while unchanged, Load Files renews them and refreshes the statistics, Compare warns about changed MR / CTR files, Reload changed files (edited, deleted, located, unreadable, unticked sheets kept) |
| `test_attention.py` | red / orange highlights keep their colours when selected, in the results tables and the Statistics list (real pixels), including a colour kind added later |
| `test_local_cost.py` | Days and Local cost (unit, or the total by days × cost × qty) at the end of Matched / Only in MR / Only in CTR, blank-with-reason cells, the box shown only on those tabs, kept sort and hidden columns, Excel sheets |
| `test_settings.py` | reset / restore / backups kept / unwritable folder |

The Generator tests depend on the test CTR Request workbook (its scaffold line, the roles
and transport lines in it); if that file is edited, update the tests with it.
