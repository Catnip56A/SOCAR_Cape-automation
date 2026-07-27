# MR vs CTR Comparator — SOCAR Cape

A desktop application for comparing **Material Requisition (MR)** and **Cost Time Resource (CTR)** Excel documents. Detects tables automatically using Excel named ranges — no manual row selection required.

---

## Features

- Upload multiple MR and/or CTR files at once
- Automatic table detection via named ranges (`equip_start/end`, `cons_start/end`)
- Select which sheets to include per file using a checklist
- Side-by-side preview of MR and CTR data before comparing
- Configurable join key and optional stock code filters
- Comparison results:
  - **Matched** — items found in both, with Description, Qty, Unit, Rechargeable, Allocation, Rate
  - **Differences** — field-level mismatches with source document noted
  - **Only in MR** — items not found in any CTR
  - **Only in CTR** — items not found in any MR
- Download a four-sheet Excel report (Matched / Differences / Only in MR / Only in CTR)
- Sortable result tables (click any column header)

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

The `.exe` bundles Python, PySide6, pandas, and openpyxl — colleagues need nothing installed.

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

This is separate from the app's **data** folder (saved presets/renames), which always lives at `%APPDATA%\SOCAR\CTRGenerator` (`ctr_generator/paths.py`) regardless of install location.

---

## Project structure

```
SOCAR_Cape-automation/
├── app.py                  # PySide6 desktop application (main)
├── sheet_parser.py         # Excel parsing logic (shared by both UIs)
├── pyproject.toml          # Dependencies and project metadata
├── justfile                # Task runner (run / build / setup / clean)
├── app.spec                # PyInstaller build spec
├── .gitignore              # Excludes .xlsx/.xlsm and env/
└── legacy_streamlit.py     # LEGACY — browser UI kept for reference only
```

---

## Named range conventions

The parser expects the following named ranges defined in the Excel workbook:

| Range name    | Document | Table                  |
|---------------|----------|------------------------|
| `equip_start` | CTR      | Plant & Equipment start |
| `equip_end`   | CTR      | Plant & Equipment end   |
| `cons_start`  | CTR / MR | Consumables start       |
| `cons_end`    | CTR / MR | Consumables end         |

Each named range points to a single cell: `_start` is the header row / first column; `_end` is the last data row / last column. All cells within that rectangle are read as the table.
