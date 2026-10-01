# SOCAR Cape Automation

A PySide6 desktop application for the SOCAR Cape commercial team, combining three tools in one window:

- **MR vs CTR Comparator** — compares **Material Requisition (MR)** and **Cost Time Resource (CTR)** Excel documents, detecting tables automatically via Excel named ranges (no manual row selection required).
- **CTR Generator** — generates the AZN and USD CTR spreadsheets (plus PDF exports) from a CTR Request, matching manpower/equipment/consumables against pricebook and SAGE reference data.
- **CTR Tracker** — writes a generated CTR's header fields and cost-breakdown totals into the master CTR tracker workbook, in batches, with revision-aware row placement.

See [USER_GUIDE.md](USER_GUIDE.md) for full step-by-step usage of all three tools.

---

## Features

### MR vs CTR Comparator
- Upload multiple MR and/or CTR files at once
- Automatic table detection via named ranges (`equip_start/end`, `cons_start/end`), with a keyword-based fallback when they're absent
- Select which sheets to include per file using a checklist, with a live preview of the combined checked data; an MR's Socar-Cape cover sheet is available as an opt-in toggle, and any loaded file can be removed individually with its ✕
- Configurable join key (default Stock Code) and optional stock code filters, independently for each side
- Comparison results across **Matched**, **Only in MR**, **Only in CTR**, **Needs Review**, and **Error Data** tabs
- **Combined View** — aggregates matched rows to one line per Stock Code, summing Qty across every contributing document per side; Stock Codes whose rows disagree on Unit are held out for manual approval in Needs Review
- **Compare Values** — highlights Qty/Unit mismatches (or, in Combined View, Stock Codes spanning more than one source document), with ↑/↓ navigation between them
- Download a multi-sheet Excel report, and save/reload a full comparison later without the original files
- Sortable result tables (click any column header); right-click a header to hide a column (visual only — the Excel report keeps every column)

### CTR Generator
- Reads a CTR Request (Manpower, Plant & Equipment, Consumables, plus an Additional Info/Scaffold/Transport block) from a Combined DB workbook
- Matches each line against the AZN Pricebook (manpower) and SAGE/equipment-names reference data (equipment, consumables), with on-screen review and correction before anything is written
- Remembers manual corrections ("Saved Renames") so the same CTR Request description matches automatically next time
- Fills the AZN and USD CTR templates with all rows, totals, and header fields, and exports each to PDF via LibreOffice
- Presets for recurring project field combinations (client, location, scope, revision, job ref, output folder)

### CTR Tracker
- Reads a generated CTR's header fields and cost-breakdown totals straight off the file (client, CTR number, date, revision, description, value, currency, Location)
- Batches up to 10 CTRs before writing, with a per-CTR editable review table
- Writes each CTR into its pre-created row in the master tracker workbook, matched by CTR number and Location — rows are never created or shifted on the normal path
- Revision handling: the same CTR + currency + revision overwrites its row in place; a different revision gets its own row in ascending revision order — a spare row sharing that CTR number if it keeps that order, otherwise a new one inserted via Excel automation
- Optional timestamped backup of the tracker workbook before every write

---

## Running the app (development)

Requires **Python 3.11+** and [uv](https://docs.astral.sh/uv/).

```bash
# Install dependencies
just setup          # or: uv sync --all-extras

# Launch the desktop app
just run            # or: uv run python app.py
```

---

## Building the standalone .exe

The `.exe` bundles Python, PySide6, pandas, openpyxl, xlwings, and lxml — colleagues need nothing installed.

> **Important:** the build must run on **Windows** (not WSL), since PyInstaller targets the OS it runs on.

### How this was actually built (WSL project → Windows PyInstaller)

The source code lives in WSL, but the build runs in a **Windows PowerShell** session that points at the WSL filesystem via `\\wsl$\<distro>`.

1. **Find your WSL distro name** (run this in PowerShell):

   ```powershell
   wsl --list
   ```

   The distro name shown (e.g. `Ubuntu-22.04`) is what you use in the path below.
2. **Open PowerShell and navigate to the project**:

   ```powershell
   cd "\\wsl$\Ubuntu-22.04\home\alhiko56\projects\SOCAR_Cape-automation"
   ```
3. **Install dependencies** (`uv` may not be on the Windows PATH — use plain pip instead):

   ```powershell
   pip install PySide6 pandas openpyxl pyinstaller
   ```
4. **Build the exe**:

   ```powershell
   python -m PyInstaller app.spec
   ```

   Note the capital `P` in `PyInstaller` — use `python -m PyInstaller` if `pyinstaller` is not found directly.
5. **Collect the output** from `dist\MR_CTR_Comparator.exe` — copy it to wherever you want to distribute it.

> **Tip:** if `\\wsl$\Ubuntu` gives a "path not found" error, run `wsl --list` to get the exact distro name (it is often `Ubuntu-22.04`, not just `Ubuntu`).

Or use GitHub Actions (see `.github/workflows/` if configured) to build automatically on push and download the artifact from the Actions tab.

### Building the installer (.exe setup)

Once `just` ([just.systems](https://just.systems)) and [Inno Setup](https://jrsoftware.org/isinfo.php) are installed on the Windows side, `just installer` builds the app (`just build`) and then packages it into a Windows installer via `setup.iss` — from the same WSL-mounted path as above:

```powershell
PS Microsoft.PowerShell.Core\FileSystem::\\wsl$\Ubuntu-22.04\home\alhiko56\projects\SOCAR_Cape-automation> cd "\\wsl$\Ubuntu-22.04\home\alhiko56\projects\SOCAR_Cape-automation"
>> just installer
```

The resulting installer is written wherever `setup.iss` configures its output (see that file for the exact path).

---

## Logs (installed .exe)

The app writes two log files to a `logs` folder next to the installed `.exe` (`app_logging.py`):

- **`app.log`** — rotating log (max 5 MB, 5 backups) with startup info, warnings, errors, and Qt-level messages.
- **`crash.log`** — native crash traces (`faulthandler`) plus any uncaught Python exception that would otherwise make the app silently close.

Default install location is under Program Files (via `setup.iss`'s `DefaultDirName={autopf}\SOCAR Cape automation\Commercial-automation`), so the logs would normally be at:

```
C:\Program Files (x86)\SOCAR Cape automation\Commercial-automation\logs\
```

**In practice, that folder often isn't writable** without elevated rights (Program Files is protected), so the app falls back to a temp directory instead — check there first if `logs\` is missing or empty next to the `.exe`:

```
%TEMP%\socar_cape_automation_logs\
```

(Press `Win+R`, paste that path, press Enter — resolves to `C:\Users\<username>\AppData\Local\Temp\socar_cape_automation_logs`.)

This is separate from the app's **data** folder (saved presets/renames), which always lives at `%APPDATA%\SOCAR\CTRGenerator` (`ctr_tools/paths.py`) regardless of install location.

---

## Project structure

```
SOCAR_Cape-automation/
├── app.py                  # Main window: MR vs CTR Comparator tab + wiring for the other two
├── sheet_parser.py         # Comparator's Excel parsing logic (named-range table detection)
├── comparison_history.py   # Save/reload full comparisons
├── app_logging.py          # Rotating app.log / crash.log setup
├── ctr_tools/               # CTR Generator + CTR Tracker
│   ├── window.py            # CTR Generator tab
│   ├── builder_azn.py       # AZN CTR spreadsheet writer
│   ├── builder_usd.py       # USD CTR spreadsheet writer
│   ├── parser.py            # CTR Request / Combined DB parsing
│   ├── pdf_exporter.py      # LibreOffice-based xlsx → PDF export
│   ├── config.py            # Loads template_config.json
│   ├── tracker_window.py    # CTR Tracker tab
│   ├── tracker.py           # CTR Tracker data model + CTR-file field extraction
│   ├── tracker_fast.py      # Raw-XML write path (normal case)
│   ├── tracker_xlwings.py   # Excel-COM write path (row-insert fallback, Windows + Excel only)
│   └── aliases.py / desc_renames.py / presets.py   # Persisted user corrections/presets
├── pyproject.toml          # Dependencies and project metadata
├── justfile                # Task runner (run / build / setup / clean)
├── app.spec                # PyInstaller build spec
└── .gitignore              # Excludes .xlsx/.xlsm and env/
```

---

## Named range conventions (MR vs CTR Comparator)

The Comparator's parser expects the following named ranges defined in the Excel workbook. The CTR Generator parses its CTR Request/Combined DB inputs differently — by fixed sheet names and column positions — see `USER_GUIDE.md`'s CTR Generator section for that format.

| Range name      | Document | Table                   |
| --------------- | -------- | ----------------------- |
| `equip_start` | CTR      | Plant & Equipment start |
| `equip_end`   | CTR      | Plant & Equipment end   |
| `cons_start`  | CTR / MR | Consumables start       |
| `cons_end`    | CTR / MR | Consumables end         |

Each named range points to a single cell: `_start` is the header row / first column; `_end` is the last data row / last column. All cells within that rectangle are read as the table.
