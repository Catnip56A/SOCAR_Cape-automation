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
| `test_settings.py` | reset / restore / backups kept / unwritable folder |

The Generator tests depend on the test CTR Request workbook (its scaffold line, the roles
and transport lines in it); if that file is edited, update the tests with it.
