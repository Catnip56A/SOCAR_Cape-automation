"""
ctr_generator/window.py

CTR Generator dialog for the SOCAR Cape desktop app.

Layout (top → bottom inside a QScrollArea):
  [Section 1] Source Files — AZN/USD templates (shared) + two sub-tabs:
                "Pricebook-Based"   — AZN/USD pricebook + SAGE pickers,
                                       loaded as reference data only
                "CTR Request-Based" — CTR Request picker, fills header
                                       fields and the three tables below
  [Section 2] Manpower / Equipment / Consumables — one table per category.
                Each row shows what was requested next to its pricebook/SAGE
                match, and IS the row used for CTR generation — there is no
                separate "add to CTR" copy step. A row is included in the
                generated CTR only when its Status starts with "✓"
                ("✓ Matched" via pricebook/SAGE, or "✓ Manual" for a
                hand-added row); "✗ No match" rows are skipped at generation
                time so unmatched requests never silently appear at rate 0.
  [Section 3] Generate — CTR header info, job ref, output folder,
                          Generate button.

Worker thread follows the same ParseWorker pattern used in app.py.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFileDialog,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QProgressDialog, QPushButton, QScrollArea,
    QSizePolicy, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ctr_generator.aliases import (
    load_aliases, get_alias, set_alias, delete_alias,
    save_aliases, export_aliases, import_aliases,
)
from ctr_generator.builder_azn import build_azn
from ctr_generator.builder_usd import build_usd
from ctr_generator.parser import (
    parse_azn_pricebook, parse_usd_pricebook, parse_sage, parse_ctr_request,
)
from ctr_generator.pdf_exporter import export_to_pdf

log = logging.getLogger(__name__)

# ── colour palette (matches app.py) ──────────────────────────────────────────
PRIMARY   = "#1976D2"
MR_COLOR  = "#1565C0"
CTR_COLOR = "#00695C"
MUTED     = "#757575"
BORDER    = "#E0E0E0"
MR_LIGHT  = "#E3F2FD"
CTR_LIGHT = "#E0F2F1"

_MARKUP = 0.065

# ── unified manpower table column indices ─────────────────────────────────────
# One table does double duty: shows the CTR Request match AND is the direct
# source for CTR generation — there is no separate "add to CTR" copy step.
# Type/Shift/Shift Type together select the correct AZN pricebook rate —
# the stock code encodes all three (e.g. NOV-NAT-OFF-12 = Night, Overtime,
# Offshore); see _decode_azn_stock_code / _match_azn_labor below.
_MP_TYPE      = 0   # "Onshore" / "Offshore" — editable
_MP_SHIFT     = 1   # "Day" / "Night" — editable
_MP_SHIFTTYPE = 2   # "Normal" / "Overtime" / "Rotational" / "Standby" — editable
_MP_DESC      = 3   # description — editable
_MP_MATCH     = 4   # match-by search key (work name, or a literal stock code override) — editable
_MP_MDESC     = 5   # matched pricebook item + stock code — read-only
_MP_NUMEMP    = 6   # num employees — editable
_MP_QTY       = 7   # quantity (hrs/days) — editable
_MP_UOM       = 8   # UOM — editable
_MP_RATE      = 9   # rate AZN — editable (auto-filled from match, overridable)
_MP_TOTAL     = 10  # read-only, computed
_MP_STATUS    = 11  # read-only: "✓ Matched" / "✓ Manual" / "✗ No match — ..."
_MP_NAT       = 12  # nationality — editable

_MANPOWER_HEADERS = [
    "Type", "Shift", "Shift Type", "Description", "Match By", "Matched Item",
    "Num\nEmployees", "Quantity", "UOM", "Rate AZN", "Total AZN", "Status", "Nationality",
]

# ── unified equipment table column indices ────────────────────────────────────
_EQ_DESC   = 0
_EQ_CODE   = 1
_EQ_MATCH  = 2
_EQ_MDESC  = 3
_EQ_QTY    = 4
_EQ_UNIT   = 5
_EQ_RATE   = 6
_EQ_DAYS   = 7
_EQ_TOTAL  = 8
_EQ_STATUS = 9

_EQUIPMENT_HEADERS = [
    "Description", "Stock Code", "Match By", "Matched Item",
    "Quantity", "Unit", "Rate/Day USD", "Days", "Total USD", "Status",
]

# ── unified consumables table column indices ──────────────────────────────────
_CS_DESC   = 0
_CS_CODE   = 1
_CS_MATCH  = 2
_CS_MDESC  = 3
_CS_QTY    = 4
_CS_UNIT   = 5
_CS_PRICE  = 6
_CS_TOTAL  = 7
_CS_STATUS = 8

_CONSUMABLES_HEADERS = [
    "Description", "Stock Code", "Match By", "Matched Item",
    "Quantity", "Unit", "Unit Price USD", "Total USD", "Status",
]


# ─────────────────────────────────────────────────────────────────────────────
# Worker thread
# ─────────────────────────────────────────────────────────────────────────────

class CTRWorker(QThread):
    progress = Signal(str)
    finished = Signal(list)   # list[str] of output file paths
    error    = Signal(str)

    def __init__(
        self,
        onshore_rows:  list[dict],
        offshore_rows: list[dict],
        equip_rows:    list[dict],
        consump_rows:  list[dict],
        azn_tpl:       str,
        usd_tpl:       str,
        output_dir:    str,
        job_ref:       str,
        header:        dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._onshore_rows  = onshore_rows
        self._offshore_rows = offshore_rows
        self._equip_rows    = equip_rows
        self._consump_rows  = consump_rows
        self._azn_tpl       = azn_tpl
        self._usd_tpl       = usd_tpl
        self._output_dir    = output_dir
        self._job_ref       = job_ref
        self._header        = header

    def run(self):
        log.info(
            "CTRWorker: job_ref=%s onshore=%d offshore=%d equip=%d consumables=%d output_dir=%s",
            self._job_ref, len(self._onshore_rows), len(self._offshore_rows),
            len(self._equip_rows), len(self._consump_rows), self._output_dir,
        )
        try:
            out = Path(self._output_dir)

            self.progress.emit("Writing AZN CTR spreadsheet…")
            azn_xlsx = build_azn(
                self._onshore_rows, self._offshore_rows, self._azn_tpl, out, self._job_ref,
                header=self._header,
            )
            log.info("CTRWorker: AZN spreadsheet written: %s", azn_xlsx)

            self.progress.emit("Writing USD CTR spreadsheet…")
            usd_xlsx = build_usd(
                self._equip_rows, self._consump_rows, self._usd_tpl, out, self._job_ref,
                header=self._header,
            )
            log.info("CTRWorker: USD spreadsheet written: %s", usd_xlsx)

            self.progress.emit("Exporting AZN PDF via LibreOffice…")
            try:
                azn_pdf = export_to_pdf(azn_xlsx, out)
                azn_pdf_str = str(azn_pdf)
            except RuntimeError as e:
                log.warning("CTRWorker: AZN PDF export skipped: %s", e)
                azn_pdf_str = f"[PDF skipped: {e}]"

            self.progress.emit("Exporting USD PDF via LibreOffice…")
            try:
                usd_pdf = export_to_pdf(usd_xlsx, out)
                usd_pdf_str = str(usd_pdf)
            except RuntimeError as e:
                log.warning("CTRWorker: USD PDF export skipped: %s", e)
                usd_pdf_str = f"[PDF skipped: {e}]"

            log.info("CTRWorker: finished successfully")
            self.finished.emit([str(azn_xlsx), str(usd_xlsx), azn_pdf_str, usd_pdf_str])

        except Exception as exc:
            log.exception("CTRWorker failed")
            self.error.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# UI helpers
# ─────────────────────────────────────────────────────────────────────────────

def _group_css(color: str) -> str:
    return f"""
        QGroupBox {{
            font-weight: bold; color: {color};
            border: 1.5px solid {color}; border-radius: 6px;
            margin-top: 10px; padding-top: 4px; background: white;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin; left: 10px;
            padding: 0 4px; background: white;
        }}
    """


def _make_table(headers: list[str], stretch_col: int = 0) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    # Open the cell editor on a single click, not just double-click — read-only
    # cells (built via _ro_item) are unaffected since they lack ItemIsEditable.
    t.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    t.horizontalHeader().setStretchLastSection(False)
    t.horizontalHeader().setSectionResizeMode(stretch_col, QHeaderView.ResizeMode.Stretch)
    return t


def _ro_item(text: str) -> QTableWidgetItem:
    """Create a read-only, non-editable table item."""
    item = QTableWidgetItem(str(text))
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
    return item


def _file_picker_row(label: str, ext_filter: str) -> tuple[QHBoxLayout, QLineEdit]:
    """Return (row_layout, line_edit) for a labelled file picker."""
    row = QHBoxLayout()
    row.setSpacing(6)
    lbl = QLabel(label)
    lbl.setFixedWidth(130)
    lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
    edit = QLineEdit()
    edit.setReadOnly(True)
    edit.setPlaceholderText("(not selected)")
    btn = QPushButton("Browse…")
    btn.setFixedWidth(80)

    _captured_edit   = edit
    _captured_filter = ext_filter

    def _browse():
        path, _ = QFileDialog.getOpenFileName(None, f"Select {label}", "", _captured_filter)
        if path:
            _captured_edit.setText(path)

    btn.clicked.connect(_browse)
    row.addWidget(lbl)
    row.addWidget(edit)
    row.addWidget(btn)
    return row, edit


def _match_lookup(df: pd.DataFrame, code_col: str, desc_col: str, price_col: str, key: str):
    """
    Look up a row in a reference dataframe (AZN/USD pricebook or SAGE) by
    stock code first, then by description — case-insensitive exact match.

    Returns (matched_description, rate, stock_code) or None if no match.
    """
    if df is None or df.empty or not key:
        return None
    key_norm = key.strip().lower()

    if code_col in df.columns:
        code_matches = df[df[code_col].astype(str).str.strip().str.lower() == key_norm]
        if not code_matches.empty:
            row = code_matches.iloc[0]
            return str(row[desc_col]), float(row[price_col]), str(row[code_col])

    if desc_col in df.columns:
        desc_matches = df[df[desc_col].astype(str).str.strip().str.lower() == key_norm]
        if not desc_matches.empty:
            row = desc_matches.iloc[0]
            code_val = str(row[code_col]) if code_col in df.columns else ""
            return str(row[desc_col]), float(row[price_col]), code_val

    return None


# AZN labor stock codes encode shift + shift-type in their prefix, e.g.
# "MSU-NAT-OFF-12" = Day/Normal, Offshore; "NOV-NAT-OFF-12" = Night/Overtime,
# Offshore. An optional "GE" prefix (e.g. "GENOV-...") marks a different
# labor category (seen on industrial-cleaning roles) but uses the same
# shift-type suffix scheme, so it's stripped before lookup.
_AZN_SHIFT_PREFIXES = {
    "MS":  ("day",   "normal"),
    "MSU": ("day",   "normal"),
    "OV":  ("day",   "overtime"),
    "SB":  ("day",   "standby"),
    "NS":  ("night", "normal"),
    "NOV": ("night", "overtime"),
    "NSB": ("night", "standby"),
}


def _decode_azn_stock_code(stock_code: str):
    """
    Decodes an AZN labor stock code into (location, shift, shift_type), or
    None if it doesn't match the <PREFIX>-NAT-<ON|OFF>-<hours> pattern.
    location is "on"/"off"; shift is "day"/"night"; shift_type is
    "normal"/"overtime"/"standby".
    """
    parts = str(stock_code).strip().upper().split("-NAT-")
    if len(parts) != 2 or not parts[1]:
        return None
    prefix, rest = parts
    if prefix.startswith("GE"):
        prefix = prefix[2:]
    shift_info = _AZN_SHIFT_PREFIXES.get(prefix)
    if shift_info is None:
        return None
    location = rest.split("-")[0].lower()
    if location not in ("on", "off"):
        return None
    shift, shift_type = shift_info
    return location, shift, shift_type


def _normalize_location(raw: str) -> str:
    return "off" if "off" in (raw or "").strip().lower() else "on"


def _normalize_shift(raw: str) -> str:
    return "night" if "night" in (raw or "").strip().lower() else "day"


def _normalize_shift_type(raw: str) -> str:
    raw = (raw or "").strip().lower()
    if "overtime" in raw or raw == "ot":
        return "overtime"
    if "standby" in raw or "stand by" in raw:
        return "standby"
    return "normal"   # covers "normal", "rotational", and empty/blank


def _match_azn_labor(df: pd.DataFrame, work_name: str, location: str, shift: str, shift_type: str):
    """
    Finds the AZN pricebook rate for a manpower line, picking the variant
    that matches the requested location/shift/shift-type combination.

    work_name is tried first as a literal stock code — this lets "Match By"
    be overridden with an exact code, bypassing the Type/Shift/Shift Type
    columns entirely. Otherwise work_name is matched as a description, and
    narrowed down to the row whose stock code decodes (via
    _decode_azn_stock_code) to the same location/shift/shift-type. If no
    exact combination exists, falls back to any same-location variant, then
    to any variant at all, rather than failing outright.

    Returns (matched_description, rate, stock_code) or None.
    """
    if df is None or df.empty or not work_name:
        return None

    key_norm = work_name.strip().lower()

    code_matches = df[df["stock_code"].astype(str).str.strip().str.lower() == key_norm]
    if not code_matches.empty:
        row = code_matches.iloc[0]
        return str(row["supplier_desc"]), float(row["unit_price"]), str(row["stock_code"])

    candidates = df[df["supplier_desc"].astype(str).str.strip().str.lower() == key_norm]
    if candidates.empty:
        return None

    loc = _normalize_location(location)
    sh  = _normalize_shift(shift)
    st  = _normalize_shift_type(shift_type)

    same_location_fallback = None
    for _, row in candidates.iterrows():
        decoded = _decode_azn_stock_code(row["stock_code"])
        if decoded is None:
            continue
        c_loc, c_shift, c_shift_type = decoded
        if c_loc == loc and c_shift == sh and c_shift_type == st:
            return str(row["supplier_desc"]), float(row["unit_price"]), str(row["stock_code"])
        if same_location_fallback is None and c_loc == loc:
            same_location_fallback = row

    if same_location_fallback is not None:
        return (str(same_location_fallback["supplier_desc"]),
                float(same_location_fallback["unit_price"]),
                str(same_location_fallback["stock_code"]))

    row = candidates.iloc[0]
    return str(row["supplier_desc"]), float(row["unit_price"]), str(row["stock_code"])


def _btn_style(color: str = PRIMARY, light: str = MR_LIGHT) -> str:
    return f"""
        QPushButton {{
            background: white; color: {color};
            border: 1.5px solid {color}; border-radius: 4px;
            padding: 2px 10px; font-size: 11px; min-height: 24px;
        }}
        QPushButton:hover    {{ background: {light}; }}
        QPushButton:disabled {{ color: {BORDER}; border-color: {BORDER}; }}
    """


# ─────────────────────────────────────────────────────────────────────────────
# Saved renames manager
# ─────────────────────────────────────────────────────────────────────────────

class AliasManagerDialog(QDialog):
    """Lists every learned request→pricebook rename, lets the user delete one."""

    def __init__(self, aliases: dict, parent=None, applied_count: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Manage Saved Renames")
        self.resize(640, 420)
        self._aliases = aliases
        self._applied_count = applied_count
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(
            "These renames were learned automatically when you fixed a "
            "\"No match\" row by editing \"Match By\". They are applied "
            "automatically the next time the same requested description "
            "appears in a future CTR Request. Select a row and click "
            "Delete to forget a rename."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        layout.addWidget(info)

        if self._applied_count:
            applied_lbl = QLabel(
                f"✓ Just applied {self._applied_count} of these rename(s) to "
                f"matching rows already in the Manpower/Equipment/Consumables "
                f"tables below the Generate section."
            )
            applied_lbl.setWordWrap(True)
            applied_lbl.setStyleSheet(f"color: {CTR_COLOR}; font-weight: bold; font-size: 11px;")
            layout.addWidget(applied_lbl)

        self._tbl = QTableWidget(0, 3)
        self._tbl.setHorizontalHeaderLabels(["Category", "Requested Name", "Matched As"])
        self._tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._tbl)
        self._reload_table()

        io_row = QHBoxLayout()
        import_btn = QPushButton("Import…")
        import_btn.setToolTip(
            "Load renames from a JSON file, merging them into the current "
            "dictionary (imported entries overwrite matching ones)."
        )
        import_btn.setStyleSheet(_btn_style())
        import_btn.clicked.connect(self._import_aliases)
        export_btn = QPushButton("Export…")
        export_btn.setToolTip(
            "Save the current renames dictionary to a JSON file — for "
            "backup, or to share with another machine/user."
        )
        export_btn.setStyleSheet(_btn_style())
        export_btn.clicked.connect(self._export_aliases)
        io_row.addWidget(import_btn)
        io_row.addWidget(export_btn)
        io_row.addStretch()
        layout.addLayout(io_row)

        btn_row = QHBoxLayout()
        del_btn = QPushButton("Delete Selected")
        del_btn.setStyleSheet(_btn_style())
        del_btn.clicked.connect(self._delete_selected)
        close_btn = QPushButton("Close")
        close_btn.setFixedWidth(90)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _reload_table(self):
        self._tbl.setRowCount(0)
        # Fixed category order (not dict insertion order) so Manpower,
        # Equipment and Consumable renames each show up in a stable, expected
        # place regardless of which category was edited most recently.
        for category in ("manpower", "equipment", "consumable"):
            mapping = self._aliases.get(category, {})
            for req_desc, match_key in sorted(mapping.items()):
                r = self._tbl.rowCount()
                self._tbl.insertRow(r)
                self._tbl.setItem(r, 0, QTableWidgetItem(category.capitalize()))
                self._tbl.setItem(r, 1, QTableWidgetItem(req_desc))
                self._tbl.setItem(r, 2, QTableWidgetItem(match_key))

    def _delete_selected(self):
        rows = sorted({i.row() for i in self._tbl.selectedItems()}, reverse=True)
        for r in rows:
            category = self._tbl.item(r, 0).text().lower()
            req_desc  = self._tbl.item(r, 1).text()
            delete_alias(self._aliases, category, req_desc)
        self._reload_table()

    def _export_aliases(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Renames", "ctr_renames.json", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            export_aliases(self._aliases, path)
        except OSError as e:
            QMessageBox.critical(self, "Export failed", str(e))
            return
        QMessageBox.information(self, "Exported", f"Renames exported to:\n{path}")

    def _import_aliases(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Renames", "", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            imported = import_aliases(path)
        except (ValueError, OSError) as e:
            QMessageBox.critical(self, "Import failed", str(e))
            return

        added = updated = 0
        for category, mapping in imported.items():
            existing = self._aliases.setdefault(category, {})
            for key, value in mapping.items():
                if key not in existing:
                    added += 1
                elif existing[key] != value:
                    updated += 1
                existing[key] = value

        save_aliases(self._aliases)
        self._reload_table()
        QMessageBox.information(
            self, "Imported",
            f"Imported {added} new and {updated} updated rename(s) from:\n{path}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Main dialog
# ─────────────────────────────────────────────────────────────────────────────

class CTRGeneratorWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self._azn_df:   pd.DataFrame = pd.DataFrame()
        self._usd_df:   pd.DataFrame = pd.DataFrame()
        self._sage_df:  pd.DataFrame = pd.DataFrame()
        self._workers:  list = []
        self._aliases:  dict = load_aliases()

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(0)

        # Scrollable content
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setSpacing(12)
        cl.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # ─── Section 1: File Inputs ──────────────────────────────────────────
        self._build_file_section(cl)

        # ─── Section 2: Manpower / Equipment / Consumables ────────────────────
        self._build_manpower_section(cl)
        self._build_equipment_section(cl)
        self._build_consumables_section(cl)

        # ─── Section 3: Generate ─────────────────────────────────────────────
        self._build_generate_section(cl)

        cl.addStretch()

    # ── Section 1 ─────────────────────────────────────────────────────────────

    def _build_file_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox("Source Files")
        grp.setStyleSheet(_group_css(PRIMARY))
        gl = QVBoxLayout(grp)
        gl.setSpacing(6)

        # Output templates — shared by both data-source sub-tabs below
        row4, self._azn_tpl_edit = _file_picker_row("AZN Template", "Excel (*.xlsx *.xls)")
        row5, self._usd_tpl_edit = _file_picker_row("USD Template", "Excel (*.xlsx *.xls)")
        gl.addLayout(row4)
        gl.addLayout(row5)

        sub_tabs = QTabWidget()
        sub_tabs.setStyleSheet(f"""
            QTabBar::tab {{ padding: 5px 16px; font-size: 11px; }}
            QTabBar::tab:selected {{ font-weight: bold; color: {PRIMARY}; }}
        """)
        gl.addWidget(sub_tabs)

        sub_tabs.addTab(self._build_pricebook_tab(), "Pricebook-Based")
        sub_tabs.addTab(self._build_ctr_request_tab(), "CTR Request-Based")

        parent_layout.addWidget(grp)

    def _build_pricebook_tab(self) -> QWidget:
        tab = QWidget()
        tl = QVBoxLayout(tab)
        tl.setSpacing(6)
        tl.setContentsMargins(2, 8, 2, 2)

        info = QLabel(
            "Loads these as reference data for the \"Customer Request vs "
            "Pricebook/SAGE Match\" lookups below — no rows are added to "
            "the CTR generation tables here. Use \"+ Add Row\" in a CTR "
            "table directly if you want to add an item by hand."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        tl.addWidget(info)

        row1, self._azn_pb_edit = _file_picker_row("AZN Pricebook", "Excel (*.xlsx *.xls)")
        row2, self._usd_pb_edit = _file_picker_row("USD Pricebook", "Excel (*.xlsx *.xls)")
        row3, self._sage_edit   = _file_picker_row("SAGE Export",   "Excel Macro (*.xlsm *.xlsx)")

        # Re-wire Browse buttons to pass self as parent for centering
        for layout in (row1, row2, row3):
            btn = layout.itemAt(2).widget()
            edit = layout.itemAt(1).widget()
            def _make_browse(e=edit):
                def _browse():
                    dlg_filter = {
                        self._azn_pb_edit: "Excel (*.xlsx *.xls)",
                        self._usd_pb_edit: "Excel (*.xlsx *.xls)",
                        self._sage_edit:   "Excel Macro (*.xlsm *.xlsx)",
                    }.get(e, "Excel (*.xlsx *.xls)")
                    path, _ = QFileDialog.getOpenFileName(self, "Select file", "", dlg_filter)
                    if path:
                        e.setText(path)
                return _browse
            btn.clicked.disconnect()
            btn.clicked.connect(_make_browse())

        tl.addLayout(row1)
        tl.addLayout(row2)
        tl.addLayout(row3)

        load_btn = QPushButton("Load Files")
        load_btn.setFixedHeight(36)
        load_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {PRIMARY}; color: white;
                border: none; border-radius: 6px;
                font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover   {{ background-color: {MR_COLOR}; }}
            QPushButton:pressed {{ background-color: #0D47A1; }}
        """)
        load_btn.clicked.connect(self._load_files)
        tl.addWidget(load_btn)

        self._pricebook_status = QLabel("")
        self._pricebook_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._pricebook_status.setWordWrap(True)
        tl.addWidget(self._pricebook_status)
        tl.addStretch()
        return tab

    def _build_ctr_request_tab(self) -> QWidget:
        tab = QWidget()
        tl = QVBoxLayout(tab)
        tl.setSpacing(6)
        tl.setContentsMargins(2, 8, 2, 2)

        row, self._ctr_req_edit = _file_picker_row("CTR Request", "Excel Macro (*.xlsm *.xlsx)")
        tl.addLayout(row)

        load_btn = QPushButton("Load CTR Request")
        load_btn.setFixedHeight(36)
        load_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {CTR_COLOR}; color: white;
                border: none; border-radius: 6px;
                font-size: 13px; font-weight: bold;
            }}
            QPushButton:hover   {{ background-color: #00897B; }}
            QPushButton:pressed {{ background-color: #004D40; }}
        """)
        load_btn.clicked.connect(self._load_ctr_request)
        tl.addWidget(load_btn)

        self._ctr_req_status = QLabel(
            "Parses the REQUEST sheet: fills the Client/Location/Scope/Date "
            "fields below, and shows requested manpower/equipment/consumables "
            "in the \"Customer Request vs Pricebook/SAGE Match\" section below."
        )
        self._ctr_req_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._ctr_req_status.setWordWrap(True)
        tl.addWidget(self._ctr_req_status)
        tl.addStretch()
        return tab

    # ── Section 2a: Manpower ──────────────────────────────────────────────────

    def _build_manpower_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox("Manpower — AZN")
        grp.setStyleSheet(_group_css(MR_COLOR))
        gl = QVBoxLayout(grp)
        gl.setSpacing(6)

        info = QLabel(
            "Loaded from the CTR Request, matched against the AZN pricebook. "
            "Type (Onshore/Offshore), Shift (Day/Night) and Shift Type "
            "(Normal/Overtime/Rotational/Standby) together select the "
            "correct pricebook rate — the AZN stock code encodes all three "
            "(e.g. NOV-NAT-OFF-12 = Night, Overtime, Offshore); Rotational "
            "is billed at the Normal rate. Edit any field directly — "
            "changing Type/Shift/Shift Type/Match By re-runs the lookup. "
            "Only rows with a \"✓\" Status (matched, or manually added) are "
            "included when generating; \"✗ No match\" rows are skipped."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        gl.addWidget(info)

        top_btn_row = QHBoxLayout()
        rematch_btn = QPushButton("Re-match All")
        rematch_btn.setToolTip(
            "Re-run matching for every row against the currently loaded "
            "AZN pricebook. Use this if you load the CTR Request before "
            "loading the Pricebook-Based files."
        )
        rematch_btn.setStyleSheet(_btn_style(MR_COLOR, MR_LIGHT))
        rematch_btn.clicked.connect(self._rematch_all_requests)
        top_btn_row.addWidget(rematch_btn)

        manage_btn = QPushButton("Manage Saved Renames…")
        manage_btn.setToolTip(
            "View or delete renames learned from your \"Match By\" edits "
            "across Manpower, Equipment and Consumables."
        )
        manage_btn.setStyleSheet(_btn_style(MUTED, "#F5F5F5"))
        manage_btn.clicked.connect(self._open_alias_manager)
        top_btn_row.addWidget(manage_btn)
        top_btn_row.addStretch()
        gl.addLayout(top_btn_row)

        search_row = QHBoxLayout()
        search_lbl = QLabel("Search:")
        search_lbl.setFixedWidth(50)
        search_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._manpower_search = QLineEdit()
        self._manpower_search.setPlaceholderText("Filter rows by any column…")
        self._manpower_search.textChanged.connect(
            lambda t: self._filter_table(self._req_manpower_tbl, t))
        search_row.addWidget(search_lbl)
        search_row.addWidget(self._manpower_search)
        gl.addLayout(search_row)

        self._req_manpower_tbl = _make_table(_MANPOWER_HEADERS, stretch_col=_MP_DESC)
        self._req_manpower_tbl.setMinimumHeight(220)
        self._req_manpower_tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._req_manpower_tbl.cellChanged.connect(self._on_manpower_changed)
        gl.addWidget(self._req_manpower_tbl)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Row")
        add_btn.setStyleSheet(_btn_style(MR_COLOR, MR_LIGHT))
        add_btn.clicked.connect(self._add_manpower_row)
        del_btn = QPushButton("Remove Selected Row")
        del_btn.setStyleSheet(_btn_style(MR_COLOR, MR_LIGHT))
        del_btn.clicked.connect(self._del_manpower_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        gl.addLayout(btn_row)

        tot_row = QHBoxLayout()
        self._azn_onshore_lbl = QLabel("Project Support:  ₼ 0.00")
        self._azn_onshore_lbl.setStyleSheet(
            f"color: {MR_COLOR}; font-weight: bold; font-size: 12px;")
        self._azn_offshore_lbl = QLabel("Total Offshore:  ₼ 0.00")
        self._azn_offshore_lbl.setStyleSheet(
            f"color: {MR_COLOR}; font-weight: bold; font-size: 12px;")
        self._azn_total_lbl = QLabel("AZN CTR Total:  ₼ 0.00")
        self._azn_total_lbl.setStyleSheet(
            f"color: {MR_COLOR}; font-weight: bold; font-size: 12px;")
        tot_row.addWidget(self._azn_onshore_lbl)
        tot_row.addSpacing(20)
        tot_row.addWidget(self._azn_offshore_lbl)
        tot_row.addSpacing(20)
        tot_row.addWidget(self._azn_total_lbl)
        tot_row.addStretch()
        gl.addLayout(tot_row)

        parent_layout.addWidget(grp)

    # ── Section 2b: Equipment ─────────────────────────────────────────────────

    def _build_equipment_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox("Plant & Equipment — USD")
        grp.setStyleSheet(_group_css(CTR_COLOR))
        gl = QVBoxLayout(grp)
        gl.setSpacing(6)

        search_row = QHBoxLayout()
        search_lbl = QLabel("Search:")
        search_lbl.setFixedWidth(50)
        search_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._equip_search = QLineEdit()
        self._equip_search.setPlaceholderText("Filter rows by any column…")
        self._equip_search.textChanged.connect(
            lambda t: self._filter_table(self._req_equip_tbl, t))
        search_row.addWidget(search_lbl)
        search_row.addWidget(self._equip_search)
        gl.addLayout(search_row)

        self._req_equip_tbl = _make_table(_EQUIPMENT_HEADERS, stretch_col=_EQ_DESC)
        self._req_equip_tbl.setMinimumHeight(180)
        self._req_equip_tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._req_equip_tbl.cellChanged.connect(self._on_equip_changed)
        gl.addWidget(self._req_equip_tbl)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Row")
        add_btn.setStyleSheet(_btn_style(CTR_COLOR, CTR_LIGHT))
        add_btn.clicked.connect(self._add_equip_row)
        del_btn = QPushButton("Remove Selected Row")
        del_btn.setStyleSheet(_btn_style(CTR_COLOR, CTR_LIGHT))
        del_btn.clicked.connect(self._del_equip_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        gl.addLayout(btn_row)

        tot_row = QHBoxLayout()
        self._usd_equip_lbl = QLabel("Equipment:  $0.00")
        self._usd_equip_lbl.setStyleSheet(
            f"color: {CTR_COLOR}; font-weight: bold; font-size: 12px;")
        tot_row.addWidget(self._usd_equip_lbl)
        tot_row.addStretch()
        gl.addLayout(tot_row)

        parent_layout.addWidget(grp)

    # ── Section 2c: Consumables ───────────────────────────────────────────────

    def _build_consumables_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox("Consumables — USD")
        grp.setStyleSheet(_group_css(MUTED))
        gl = QVBoxLayout(grp)
        gl.setSpacing(4)

        search_row = QHBoxLayout()
        search_lbl = QLabel("Search:")
        search_lbl.setFixedWidth(50)
        search_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._cons_search = QLineEdit()
        self._cons_search.setPlaceholderText("Filter rows by any column…")
        self._cons_search.textChanged.connect(
            lambda t: self._filter_table(self._req_cons_tbl, t))
        search_row.addWidget(search_lbl)
        search_row.addWidget(self._cons_search)
        gl.addLayout(search_row)

        self._req_cons_tbl = _make_table(_CONSUMABLES_HEADERS, stretch_col=_CS_DESC)
        self._req_cons_tbl.setMinimumHeight(150)
        self._req_cons_tbl.setMaximumHeight(300)
        self._req_cons_tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._req_cons_tbl.cellChanged.connect(self._on_cons_changed)
        gl.addWidget(self._req_cons_tbl)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Row")
        add_btn.setStyleSheet(_btn_style(MUTED, "#F5F5F5"))
        add_btn.clicked.connect(self._add_cons_row)
        del_btn = QPushButton("Remove Selected Row")
        del_btn.setStyleSheet(_btn_style(MUTED, "#F5F5F5"))
        del_btn.clicked.connect(self._del_cons_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        gl.addLayout(btn_row)

        tot_row = QHBoxLayout()
        self._usd_cons_lbl = QLabel("Consumables (incl. 6.5% markup):  $0.00")
        self._usd_cons_lbl.setStyleSheet(f"color: {MUTED}; font-weight: bold; font-size: 12px;")
        self._usd_total_lbl = QLabel("USD CTR Total:  $0.00")
        self._usd_total_lbl.setStyleSheet(f"color: {MUTED}; font-weight: bold; font-size: 12px;")
        tot_row.addWidget(self._usd_cons_lbl)
        tot_row.addSpacing(20)
        tot_row.addWidget(self._usd_total_lbl)
        tot_row.addStretch()
        gl.addLayout(tot_row)

        parent_layout.addWidget(grp)

    # ── Section 3: Generate ──────────────────────────────────────────────────

    def _build_generate_section(self, parent_layout: QVBoxLayout):
        grp = QGroupBox("Generate")
        grp.setStyleSheet(_group_css(PRIMARY))
        gl = QVBoxLayout(grp)
        gl.setSpacing(8)

        # ── CTR header info — visible & editable regardless of which
        # Source Files sub-tab was used; auto-filled by Load CTR Request.
        hdr_lbl = QLabel("CTR Header Info  (auto-filled from CTR Request, editable)")
        hf = hdr_lbl.font()
        hf.setBold(True)
        hdr_lbl.setFont(hf)
        hdr_lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        gl.addWidget(hdr_lbl)

        def _hdr_row(label: str, default: str = ""):
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(95)
            lbl.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
            edit = QLineEdit(default)
            row.addWidget(lbl)
            row.addWidget(edit)
            return row, edit

        row_c,  self._client_edit      = _hdr_row("Client:")
        row_sc, self._sub_client_edit  = _hdr_row("Sub-Client:")
        row_l,  self._location_edit    = _hdr_row("Location:")
        row_s,  self._scope_edit       = _hdr_row("Scope:")
        row_d,  self._date_edit        = _hdr_row("Date:")
        self._date_edit.setPlaceholderText("YYYY-MM-DD")
        row_cn, self._contract_no_edit = _hdr_row("Contract No:")
        row_rv, self._revision_edit    = _hdr_row("Revision:", "0")

        for row in (row_c, row_sc, row_l, row_s, row_d, row_cn, row_rv):
            gl.addLayout(row)

        gl.addSpacing(6)

        ref_row = QHBoxLayout()
        ref_lbl = QLabel("Job Ref:")
        ref_lbl.setFixedWidth(70)
        self._job_ref_edit = QLineEdit("217")
        self._job_ref_edit.setFixedWidth(120)
        self._job_ref_edit.setPlaceholderText("e.g. 217")
        ref_row.addWidget(ref_lbl)
        ref_row.addWidget(self._job_ref_edit)
        ref_row.addStretch()
        gl.addLayout(ref_row)

        out_row = QHBoxLayout()
        out_lbl = QLabel("Output folder:")
        out_lbl.setFixedWidth(100)
        self._out_dir_edit = QLineEdit()
        self._out_dir_edit.setReadOnly(True)
        self._out_dir_edit.setPlaceholderText("(select output folder)")
        out_browse = QPushButton("Browse…")
        out_browse.setFixedWidth(80)
        out_browse.clicked.connect(self._browse_output)
        out_row.addWidget(out_lbl)
        out_row.addWidget(self._out_dir_edit)
        out_row.addWidget(out_browse)
        gl.addLayout(out_row)

        gen_btn = QPushButton("Generate CTR Documents")
        gen_btn.setFixedHeight(44)
        gen_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {PRIMARY}; color: white;
                border: none; border-radius: 6px;
                font-size: 14px; font-weight: bold;
            }}
            QPushButton:hover   {{ background-color: {MR_COLOR}; }}
            QPushButton:pressed {{ background-color: #0D47A1; }}
        """)
        gen_btn.clicked.connect(self._generate)
        gl.addWidget(gen_btn)

        parent_layout.addWidget(grp)

    # ── File loading ──────────────────────────────────────────────────────────

    def _load_files(self):
        """
        Loads AZN/USD pricebook + SAGE export into memory as reference data
        for the "Customer Request vs Pricebook/SAGE Match" lookups. This does
        NOT add any rows to the Onshore/Offshore/Equipment/Consumables
        tables directly — only a confirmed match (or manual "+ Add Row")
        puts something into the CTR generation tables, so the output never
        silently includes pricebook/SAGE items the customer didn't request.
        """
        azn_pb  = self._azn_pb_edit.text().strip()
        usd_pb  = self._usd_pb_edit.text().strip()
        sage    = self._sage_edit.text().strip()

        errors = []
        if not azn_pb:  errors.append("AZN Pricebook not selected.")
        if not sage:    errors.append("SAGE Export not selected.")
        if errors:
            QMessageBox.warning(self, "Missing files", "\n".join(errors))
            return

        loaded = []
        log.info("Loading pricebook/SAGE reference files: azn=%s usd=%s sage=%s",
                  azn_pb, usd_pb or "(skipped)", sage)
        try:
            self._load_azn_pricebook(azn_pb)
            loaded.append(f"AZN Pricebook ({len(self._azn_df)} rows)")
        except Exception as e:
            log.exception("Failed to load AZN pricebook: %s", azn_pb)
            QMessageBox.critical(self, "AZN Pricebook error", str(e))

        if usd_pb:
            try:
                self._load_usd_pricebook(usd_pb)
                loaded.append(f"USD Pricebook ({len(self._usd_df)} rows)")
            except Exception as e:
                log.exception("Failed to load USD pricebook: %s", usd_pb)
                QMessageBox.warning(self, "USD Pricebook error",
                                    f"Could not load USD pricebook:\n{e}\n\n"
                                    "Equipment matching will show no matches.")

        try:
            self._load_sage(sage)
            loaded.append(f"SAGE Export ({len(self._sage_df)} rows)")
        except Exception as e:
            log.exception("Failed to load SAGE export: %s", sage)
            QMessageBox.critical(self, "SAGE Export error", str(e))

        # If a CTR Request was already loaded, re-run matching now that
        # pricebook/SAGE reference data is available.
        self._rematch_all_requests()

        if loaded:
            self._pricebook_status.setText(
                "Loaded for matching (no rows added to the CTR tables): "
                + "; ".join(loaded)
            )

    def _load_azn_pricebook(self, path: str):
        self._azn_df = parse_azn_pricebook(path)

    def _load_usd_pricebook(self, path: str):
        self._usd_df = parse_usd_pricebook(path)

    def _load_sage(self, path: str):
        self._sage_df = parse_sage(path)

    def _load_ctr_request(self):
        path = self._ctr_req_edit.text().strip()
        if not path:
            QMessageBox.warning(self, "Missing file", "Select a CTR Request file first.")
            return

        log.info("Loading CTR Request: %s", path)
        try:
            data = parse_ctr_request(path)
        except Exception as e:
            log.exception("Failed to parse CTR Request: %s", path)
            QMessageBox.critical(self, "CTR Request error", str(e))
            return

        # Populate the editable header fields below (visible on both sub-tabs)
        if data.get("client"):
            self._client_edit.setText(data["client"])
        if data.get("location"):
            self._location_edit.setText(data["location"])
        if data.get("job_description"):
            self._scope_edit.setText(data["job_description"])
        commencement = data.get("commencement_date")
        if commencement:
            try:
                self._date_edit.setText(commencement.strftime("%Y-%m-%d"))
            except AttributeError:
                self._date_edit.setText(str(commencement))

        # Populate the Manpower/Equipment/Consumables tables directly —
        # these tables ARE the CTR generation source, matched in place.
        self._req_manpower_tbl.setRowCount(0)
        for mp in data.get("manpower_rows", []):
            self._append_manpower_row_from_request(mp)

        self._req_equip_tbl.setRowCount(0)
        for eq in data.get("equipment_rows", []):
            self._append_equip_row_from_request(eq)

        self._req_cons_tbl.setRowCount(0)
        for cs in data.get("consumable_rows", []):
            self._append_cons_row_from_request(cs)

        self._recalc_azn_totals()
        self._recalc_usd_totals()

        n_mp = len(data.get("manpower_rows", []))
        n_eq = len(data.get("equipment_rows", []))
        n_cs = len(data.get("consumable_rows", []))
        log.info("CTR Request loaded: manpower=%d equipment=%d consumable=%d",
                  n_mp, n_eq, n_cs)
        self._ctr_req_status.setText(
            f"Loaded {n_mp} manpower, {n_eq} equipment, {n_cs} consumable "
            f"request(s) into the tables below. Exact matches on stock code "
            f"or description fill in the rate automatically and mark the "
            f"row \"✓ Matched\". Edit \"Match By\" to fix a row marked "
            f"\"✗ No match\", or load the Pricebook-Based files and click "
            f"\"Re-match All\". CTR Header Info fields in the Generate "
            f"section are pre-filled."
        )

    # ── Matching against pricebook/SAGE ─────────────────────────────────────────

    def _rematch_all_requests(self):
        for r in range(self._req_manpower_tbl.rowCount()):
            self._rematch_manpower_row(r)
        for r in range(self._req_equip_tbl.rowCount()):
            self._rematch_equip_row(r)
        for r in range(self._req_cons_tbl.rowCount()):
            self._rematch_cons_row(r)
        # Each _rematch_*_row call refreshes its own row's Total cell but not
        # the summary labels at the bottom of each table — refresh those too,
        # otherwise a bulk re-match looks like it did nothing.
        self._recalc_azn_totals()
        self._recalc_usd_totals()

    def _open_alias_manager(self):
        # Sweep every saved rename across the rows already on screen first —
        # a rename learned from fixing one row otherwise only applies to
        # *future* rows (the next CTR Request load), leaving sibling rows
        # with the same description still sitting unmatched.
        applied = self._apply_aliases_to_all_rows()
        if applied:
            self._recalc_azn_totals()
            self._recalc_usd_totals()

        # Note: deleting a rename here only stops it being applied as the
        # default for *future* request rows — it deliberately does not
        # touch "Match By" text already sitting in the tables below, since
        # re-matching would just re-learn the same rename from that text.
        dlg = AliasManagerDialog(self._aliases, parent=self, applied_count=applied)
        dlg.exec()

    def _apply_aliases_to_all_rows(self) -> int:
        """
        Overwrites "Match By" with the saved rename for every row whose
        Description (Manpower) or Description/Stock Code (Equipment,
        Consumables) has a known alias — even if Match By currently holds
        something else — since a saved rename represents the best known
        correct search term. Each change fires cellChanged, which re-runs
        matching for that row. Returns how many rows were updated.
        """
        applied = 0

        tbl = self._req_manpower_tbl
        for r in range(tbl.rowCount()):
            desc_item  = tbl.item(r, _MP_DESC)
            match_item = tbl.item(r, _MP_MATCH)
            if desc_item is None or match_item is None:
                continue
            alias = get_alias(self._aliases, "manpower", desc_item.text().strip())
            if alias and match_item.text().strip() != alias:
                match_item.setText(alias)
                applied += 1

        tbl = self._req_equip_tbl
        for r in range(tbl.rowCount()):
            desc_item  = tbl.item(r, _EQ_DESC)
            match_item = tbl.item(r, _EQ_MATCH)
            if desc_item is None or match_item is None:
                continue
            alias = get_alias(self._aliases, "equipment", desc_item.text().strip())
            if alias and match_item.text().strip() != alias:
                match_item.setText(alias)
                applied += 1

        tbl = self._req_cons_tbl
        for r in range(tbl.rowCount()):
            desc_item  = tbl.item(r, _CS_DESC)
            match_item = tbl.item(r, _CS_MATCH)
            if desc_item is None or match_item is None:
                continue
            alias = get_alias(self._aliases, "consumable", desc_item.text().strip())
            if alias and match_item.text().strip() != alias:
                match_item.setText(alias)
                applied += 1

        return applied

    # -- manpower --

    def _append_manpower_row_from_request(self, request_item: dict):
        tbl = self._req_manpower_tbl
        r = tbl.rowCount()
        tbl.insertRow(r)

        desc = request_item.get("description", "")
        default_key = get_alias(self._aliases, "manpower", desc) or desc
        row_type = "Onshore" if "(onshore)" in desc.lower() else "Offshore"
        shift = "Night" if "night" in (request_item.get("shift") or "").lower() else "Day"
        shift_type_raw = (request_item.get("weekend_shift") or "").strip()
        shift_type = shift_type_raw if shift_type_raw else "Normal"
        try:
            num_emp = float(request_item.get("quantity") or 1)
        except (TypeError, ValueError):
            num_emp = 1.0
        try:
            qty = float(request_item.get("working_days") or 0)
        except (TypeError, ValueError):
            qty = 0.0

        tbl.setItem(r, _MP_TYPE,      QTableWidgetItem(row_type))
        tbl.setItem(r, _MP_SHIFT,     QTableWidgetItem(shift))
        tbl.setItem(r, _MP_SHIFTTYPE, QTableWidgetItem(shift_type))
        tbl.setItem(r, _MP_DESC,      QTableWidgetItem(desc))
        tbl.setItem(r, _MP_MATCH,     QTableWidgetItem(default_key))
        tbl.setItem(r, _MP_MDESC,     _ro_item(""))
        tbl.setItem(r, _MP_NUMEMP,    QTableWidgetItem(str(num_emp)))
        tbl.setItem(r, _MP_QTY,       QTableWidgetItem(str(qty)))
        tbl.setItem(r, _MP_UOM,       QTableWidgetItem("Days"))
        tbl.setItem(r, _MP_RATE,      QTableWidgetItem("0.00"))
        tbl.setItem(r, _MP_TOTAL,     _ro_item("0.00"))
        tbl.setItem(r, _MP_STATUS,    _ro_item("✗ No match — edit Match By or Description"))
        tbl.setItem(r, _MP_NAT,       QTableWidgetItem("NAT"))

        self._rematch_manpower_row(r, learn_alias=False)

    def _add_manpower_row(self):
        tbl = self._req_manpower_tbl
        tbl.blockSignals(True)
        r = tbl.rowCount()
        tbl.insertRow(r)
        tbl.setItem(r, _MP_TYPE,      QTableWidgetItem("Offshore"))
        tbl.setItem(r, _MP_SHIFT,     QTableWidgetItem("Day"))
        tbl.setItem(r, _MP_SHIFTTYPE, QTableWidgetItem("Normal"))
        tbl.setItem(r, _MP_DESC,      QTableWidgetItem(""))
        tbl.setItem(r, _MP_MATCH,     QTableWidgetItem(""))
        tbl.setItem(r, _MP_MDESC,     _ro_item(""))
        tbl.setItem(r, _MP_NUMEMP,    QTableWidgetItem("1"))
        tbl.setItem(r, _MP_QTY,       QTableWidgetItem("0"))
        tbl.setItem(r, _MP_UOM,       QTableWidgetItem("Hours"))
        tbl.setItem(r, _MP_RATE,      QTableWidgetItem("0.00"))
        tbl.setItem(r, _MP_TOTAL,     _ro_item("0.00"))
        tbl.setItem(r, _MP_STATUS,    _ro_item("✓ Manual"))
        tbl.setItem(r, _MP_NAT,       QTableWidgetItem("NAT"))
        tbl.blockSignals(False)

    def _del_manpower_row(self):
        rows = sorted({i.row() for i in self._req_manpower_tbl.selectedItems()}, reverse=True)
        for r in rows:
            self._req_manpower_tbl.removeRow(r)
        self._recalc_azn_totals()

    def _on_manpower_changed(self, row: int, col: int):
        if col == _MP_DESC:
            self._apply_desc_alias_manpower(row)
        if col in (_MP_TYPE, _MP_SHIFT, _MP_SHIFTTYPE, _MP_MATCH):
            self._rematch_manpower_row(row)
        elif col in (_MP_NUMEMP, _MP_QTY, _MP_RATE):
            self._recalc_manpower_row(row)
        if col in (_MP_TYPE, _MP_SHIFT, _MP_SHIFTTYPE, _MP_MATCH, _MP_NUMEMP, _MP_QTY, _MP_RATE):
            self._recalc_azn_totals()

    def _apply_desc_alias_manpower(self, row: int):
        """
        Fires when a row's Description changes (typed by hand, or a fresh
        "+ Add Row" being filled in). If the new text has a saved rename,
        apply it to Match By immediately — same authority as the "Manage
        Saved Renames" sweep. Otherwise, give a still-blank Match By a
        sensible starting point so a brand-new row is searchable right away.
        """
        tbl = self._req_manpower_tbl
        desc_item  = tbl.item(row, _MP_DESC)
        match_item = tbl.item(row, _MP_MATCH)
        if desc_item is None or match_item is None:
            return
        desc  = desc_item.text().strip()
        alias = get_alias(self._aliases, "manpower", desc)
        if alias:
            if match_item.text().strip() != alias:
                match_item.setText(alias)   # cascades: rematch + totals
        elif not match_item.text().strip() and desc:
            match_item.setText(desc)        # cascades: rematch + totals

    def _recalc_manpower_row(self, row: int):
        tbl = self._req_manpower_tbl
        def _val(c):
            item = tbl.item(row, c)
            try:
                return float(item.text()) if item else 0.0
            except ValueError:
                return 0.0
        total = _val(_MP_NUMEMP) * _val(_MP_QTY) * _val(_MP_RATE)
        tbl.blockSignals(True)
        tbl.setItem(row, _MP_TOTAL, _ro_item(f"{total:.2f}"))
        tbl.blockSignals(False)

    def _rematch_manpower_row(self, row: int, learn_alias: bool = True):
        tbl = self._req_manpower_tbl
        desc_item   = tbl.item(row, _MP_DESC)
        match_item  = tbl.item(row, _MP_MATCH)
        type_item   = tbl.item(row, _MP_TYPE)
        shift_item  = tbl.item(row, _MP_SHIFT)
        stype_item  = tbl.item(row, _MP_SHIFTTYPE)
        status_item = tbl.item(row, _MP_STATUS)
        if desc_item is None or match_item is None:
            return

        key  = match_item.text().strip()
        desc = desc_item.text().strip()
        location   = type_item.text()  if type_item  else ""
        shift      = shift_item.text() if shift_item else ""
        shift_type = stype_item.text() if stype_item else ""
        was_includable = bool(status_item) and status_item.text().startswith("✓")
        match = _match_azn_labor(self._azn_df, key, location, shift, shift_type)

        tbl.blockSignals(True)
        if match:
            mdesc, rate, stock_code = match
            tbl.setItem(row, _MP_MDESC, _ro_item(f"{mdesc} ({stock_code})"))
            tbl.setItem(row, _MP_RATE, QTableWidgetItem(f"{rate:.2f}"))
            tbl.setItem(row, _MP_STATUS, _ro_item("✓ Matched"))
        else:
            tbl.setItem(row, _MP_MDESC, _ro_item(""))
            tbl.setItem(row, _MP_STATUS, _ro_item(
                "✓ Manual" if was_includable else "✗ No match — edit Match By or Description"))
        tbl.blockSignals(False)
        self._recalc_manpower_row(row)

        # Learn the rename if Match By differs from the requested description
        # — remembered so future CTR Requests auto-fill the same correction.
        if learn_alias and match and key and key.lower() != desc.lower():
            set_alias(self._aliases, "manpower", desc, key)

    # -- equipment --

    def _append_equip_row_from_request(self, request_item: dict):
        tbl = self._req_equip_tbl
        r = tbl.rowCount()
        tbl.insertRow(r)

        desc = request_item.get("description", "")
        code = request_item.get("stock_code", "")
        default_key = get_alias(self._aliases, "equipment", desc) or desc
        try:
            qty = float(request_item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1.0
        try:
            days = float(request_item.get("days") or 30)
        except (TypeError, ValueError):
            days = 30.0

        tbl.setItem(r, _EQ_DESC,   QTableWidgetItem(desc))
        tbl.setItem(r, _EQ_CODE,   QTableWidgetItem(code))
        tbl.setItem(r, _EQ_MATCH,  QTableWidgetItem(default_key))
        tbl.setItem(r, _EQ_MDESC,  _ro_item(""))
        tbl.setItem(r, _EQ_QTY,    QTableWidgetItem(str(qty)))
        tbl.setItem(r, _EQ_UNIT,   QTableWidgetItem(request_item.get("uom") or "DAY"))
        tbl.setItem(r, _EQ_RATE,   QTableWidgetItem("0.00"))
        tbl.setItem(r, _EQ_DAYS,   QTableWidgetItem(str(days)))
        tbl.setItem(r, _EQ_TOTAL,  _ro_item("0.00"))
        tbl.setItem(r, _EQ_STATUS, _ro_item("✗ No match — edit Match By or Description"))

        self._rematch_equip_row(r, learn_alias=False)

    def _add_equip_row(self):
        tbl = self._req_equip_tbl
        tbl.blockSignals(True)
        r = tbl.rowCount()
        tbl.insertRow(r)
        tbl.setItem(r, _EQ_DESC,   QTableWidgetItem(""))
        tbl.setItem(r, _EQ_CODE,   QTableWidgetItem(""))
        tbl.setItem(r, _EQ_MATCH,  QTableWidgetItem(""))
        tbl.setItem(r, _EQ_MDESC,  _ro_item(""))
        tbl.setItem(r, _EQ_QTY,    QTableWidgetItem("1"))
        tbl.setItem(r, _EQ_UNIT,   QTableWidgetItem("DAY"))
        tbl.setItem(r, _EQ_RATE,   QTableWidgetItem("0.00"))
        tbl.setItem(r, _EQ_DAYS,   QTableWidgetItem("30"))
        tbl.setItem(r, _EQ_TOTAL,  _ro_item("0.00"))
        tbl.setItem(r, _EQ_STATUS, _ro_item("✓ Manual"))
        tbl.blockSignals(False)

    def _del_equip_row(self):
        rows = sorted({i.row() for i in self._req_equip_tbl.selectedItems()}, reverse=True)
        for r in rows:
            self._req_equip_tbl.removeRow(r)
        self._recalc_usd_totals()

    def _on_equip_changed(self, row: int, col: int):
        if col in (_EQ_DESC, _EQ_CODE):
            self._apply_desc_alias_equip(row)
        if col == _EQ_MATCH:
            self._rematch_equip_row(row)
        elif col in (_EQ_QTY, _EQ_RATE, _EQ_DAYS):
            self._recalc_equip_row(row)
        if col in (_EQ_MATCH, _EQ_QTY, _EQ_RATE, _EQ_DAYS):
            self._recalc_usd_totals()

    def _apply_desc_alias_equip(self, row: int):
        """See _apply_desc_alias_manpower — same idea, keyed by Description."""
        tbl = self._req_equip_tbl
        desc_item  = tbl.item(row, _EQ_DESC)
        match_item = tbl.item(row, _EQ_MATCH)
        if desc_item is None or match_item is None:
            return
        desc  = desc_item.text().strip()
        alias = get_alias(self._aliases, "equipment", desc)
        if alias:
            if match_item.text().strip() != alias:
                match_item.setText(alias)
        elif not match_item.text().strip() and desc:
            match_item.setText(desc)

    def _recalc_equip_row(self, row: int):
        tbl = self._req_equip_tbl
        def _val(c):
            item = tbl.item(row, c)
            try:
                return float(item.text()) if item else 0.0
            except ValueError:
                return 0.0
        total = _val(_EQ_QTY) * _val(_EQ_RATE) * _val(_EQ_DAYS)
        tbl.blockSignals(True)
        tbl.setItem(row, _EQ_TOTAL, _ro_item(f"{total:.2f}"))
        tbl.blockSignals(False)

    def _rematch_equip_row(self, row: int, learn_alias: bool = True):
        tbl = self._req_equip_tbl
        desc_item  = tbl.item(row, _EQ_DESC)
        code_item  = tbl.item(row, _EQ_CODE)
        match_item = tbl.item(row, _EQ_MATCH)
        status_item = tbl.item(row, _EQ_STATUS)
        if desc_item is None or match_item is None:
            return

        key  = match_item.text().strip()
        desc = desc_item.text().strip()
        code = code_item.text().strip() if code_item else ""
        identity = (code or desc).lower()
        was_includable = bool(status_item) and status_item.text().startswith("✓")
        match = _match_lookup(self._usd_df, "stock_code", "supplier_desc", "unit_price", key)

        tbl.blockSignals(True)
        if match:
            mdesc, rate, _code = match
            tbl.setItem(row, _EQ_MDESC, _ro_item(mdesc))
            tbl.setItem(row, _EQ_RATE, QTableWidgetItem(f"{rate:.2f}"))
            tbl.setItem(row, _EQ_STATUS, _ro_item("✓ Matched"))
        else:
            tbl.setItem(row, _EQ_MDESC, _ro_item(""))
            tbl.setItem(row, _EQ_STATUS, _ro_item(
                "✓ Manual" if was_includable else "✗ No match — edit Match By or Description"))
        tbl.blockSignals(False)
        self._recalc_equip_row(row)

        if learn_alias and match and key and key.lower() != identity:
            set_alias(self._aliases, "equipment", desc, key)

    # -- consumables --

    def _append_cons_row_from_request(self, request_item: dict):
        tbl = self._req_cons_tbl
        r = tbl.rowCount()
        tbl.insertRow(r)

        desc = request_item.get("description", "")
        code = request_item.get("stock_code", "")
        default_key = get_alias(self._aliases, "consumable", desc) or code or desc
        try:
            qty = float(request_item.get("quantity") or 1)
        except (TypeError, ValueError):
            qty = 1.0

        tbl.setItem(r, _CS_DESC,   QTableWidgetItem(desc))
        tbl.setItem(r, _CS_CODE,   QTableWidgetItem(code))
        tbl.setItem(r, _CS_MATCH,  QTableWidgetItem(default_key))
        tbl.setItem(r, _CS_MDESC,  _ro_item(""))
        tbl.setItem(r, _CS_QTY,    QTableWidgetItem(str(qty)))
        tbl.setItem(r, _CS_UNIT,   QTableWidgetItem(request_item.get("uom") or "EA"))
        tbl.setItem(r, _CS_PRICE,  QTableWidgetItem("0.00"))
        tbl.setItem(r, _CS_TOTAL,  _ro_item("0.00"))
        tbl.setItem(r, _CS_STATUS, _ro_item("✗ No match — edit Match By or Description"))

        self._rematch_cons_row(r, learn_alias=False)

    def _add_cons_row(self):
        tbl = self._req_cons_tbl
        tbl.blockSignals(True)
        r = tbl.rowCount()
        tbl.insertRow(r)
        tbl.setItem(r, _CS_DESC,   QTableWidgetItem(""))
        tbl.setItem(r, _CS_CODE,   QTableWidgetItem(""))
        tbl.setItem(r, _CS_MATCH,  QTableWidgetItem(""))
        tbl.setItem(r, _CS_MDESC,  _ro_item(""))
        tbl.setItem(r, _CS_QTY,    QTableWidgetItem("1"))
        tbl.setItem(r, _CS_UNIT,   QTableWidgetItem("EA"))
        tbl.setItem(r, _CS_PRICE,  QTableWidgetItem("0.00"))
        tbl.setItem(r, _CS_TOTAL,  _ro_item("0.00"))
        tbl.setItem(r, _CS_STATUS, _ro_item("✓ Manual"))
        tbl.blockSignals(False)

    def _del_cons_row(self):
        rows = sorted({i.row() for i in self._req_cons_tbl.selectedItems()}, reverse=True)
        for r in rows:
            self._req_cons_tbl.removeRow(r)
        self._recalc_usd_totals()

    def _on_cons_changed(self, row: int, col: int):
        if col in (_CS_DESC, _CS_CODE):
            self._apply_desc_alias_cons(row)
        if col == _CS_MATCH:
            self._rematch_cons_row(row)
        elif col in (_CS_QTY, _CS_PRICE):
            self._recalc_cons_row(row)
        if col in (_CS_MATCH, _CS_QTY, _CS_PRICE):
            self._recalc_usd_totals()

    def _apply_desc_alias_cons(self, row: int):
        """See _apply_desc_alias_manpower — same idea, keyed by Description."""
        tbl = self._req_cons_tbl
        desc_item  = tbl.item(row, _CS_DESC)
        code_item  = tbl.item(row, _CS_CODE)
        match_item = tbl.item(row, _CS_MATCH)
        if desc_item is None or match_item is None:
            return
        desc  = desc_item.text().strip()
        code  = code_item.text().strip() if code_item else ""
        alias = get_alias(self._aliases, "consumable", desc)
        if alias:
            if match_item.text().strip() != alias:
                match_item.setText(alias)
        elif not match_item.text().strip() and (code or desc):
            match_item.setText(code or desc)

    def _recalc_cons_row(self, row: int):
        tbl = self._req_cons_tbl
        def _val(c):
            item = tbl.item(row, c)
            try:
                return float(item.text()) if item else 0.0
            except ValueError:
                return 0.0
        total = _val(_CS_QTY) * _val(_CS_PRICE)
        tbl.blockSignals(True)
        tbl.setItem(row, _CS_TOTAL, _ro_item(f"{total:.2f}"))
        tbl.blockSignals(False)

    def _rematch_cons_row(self, row: int, learn_alias: bool = True):
        tbl = self._req_cons_tbl
        desc_item  = tbl.item(row, _CS_DESC)
        code_item  = tbl.item(row, _CS_CODE)
        match_item = tbl.item(row, _CS_MATCH)
        status_item = tbl.item(row, _CS_STATUS)
        if desc_item is None or match_item is None:
            return

        key  = match_item.text().strip()
        desc = desc_item.text().strip()
        code = code_item.text().strip() if code_item else ""
        identity = (code or desc).lower()
        was_includable = bool(status_item) and status_item.text().startswith("✓")
        match = _match_lookup(self._sage_df, "product", "long_description", "local_expect_cost", key)

        tbl.blockSignals(True)
        if match:
            mdesc, price, _code = match
            tbl.setItem(row, _CS_MDESC, _ro_item(mdesc))
            tbl.setItem(row, _CS_PRICE, QTableWidgetItem(f"{price:.2f}"))
            tbl.setItem(row, _CS_STATUS, _ro_item("✓ Matched"))
        else:
            tbl.setItem(row, _CS_MDESC, _ro_item(""))
            tbl.setItem(row, _CS_STATUS, _ro_item(
                "✓ Manual" if was_includable else "✗ No match — edit Match By or Description"))
        tbl.blockSignals(False)
        self._recalc_cons_row(row)

        if learn_alias and match and key and key.lower() != identity:
            set_alias(self._aliases, "consumable", desc, key)

    # ── Totals ────────────────────────────────────────────────────────────────

    def _recalc_azn_totals(self):
        tbl = self._req_manpower_tbl
        onshore_total = 0.0
        offshore_total = 0.0
        for r in range(tbl.rowCount()):
            status_item = tbl.item(r, _MP_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                continue
            total_item = tbl.item(r, _MP_TOTAL)
            try:
                total = float(total_item.text()) if total_item else 0.0
            except ValueError:
                total = 0.0
            type_item = tbl.item(r, _MP_TYPE)
            row_type = type_item.text().strip().lower() if type_item else "offshore"
            if "onshore" in row_type:
                onshore_total += total
            else:
                offshore_total += total

        self._azn_onshore_lbl.setText(f"Project Support:  ₼ {onshore_total:,.2f}")
        self._azn_offshore_lbl.setText(f"Total Offshore:  ₼ {offshore_total:,.2f}")
        self._azn_total_lbl.setText(f"AZN CTR Total:  ₼ {onshore_total + offshore_total:,.2f}")

    def _recalc_usd_totals(self):
        equip_total = 0.0
        for r in range(self._req_equip_tbl.rowCount()):
            status_item = self._req_equip_tbl.item(r, _EQ_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                continue
            total_item = self._req_equip_tbl.item(r, _EQ_TOTAL)
            try:
                equip_total += float(total_item.text()) if total_item else 0.0
            except ValueError:
                pass

        cons_raw = 0.0
        for r in range(self._req_cons_tbl.rowCount()):
            status_item = self._req_cons_tbl.item(r, _CS_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                continue
            total_item = self._req_cons_tbl.item(r, _CS_TOTAL)
            try:
                cons_raw += float(total_item.text()) if total_item else 0.0
            except ValueError:
                pass

        cons_with_markup = cons_raw * (1 + _MARKUP)
        ctr_total        = equip_total + cons_with_markup

        self._usd_equip_lbl.setText(f"Equipment:  ${equip_total:,.2f}")
        self._usd_cons_lbl.setText(
            f"Consumables (incl. 6.5% markup):  ${cons_with_markup:,.2f}")
        self._usd_total_lbl.setText(f"USD CTR Total:  ${ctr_total:,.2f}")

    # ── Search filter ─────────────────────────────────────────────────────────

    def _filter_table(self, tbl: QTableWidget, text: str):
        text = text.strip().lower()
        for row in range(tbl.rowCount()):
            visible = not text or any(
                text in (tbl.item(row, col).text().lower() if tbl.item(row, col) else "")
                for col in range(tbl.columnCount())
            )
            tbl.setRowHidden(row, not visible)

    # ── Output folder ─────────────────────────────────────────────────────────

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "Select output folder")
        if path:
            self._out_dir_edit.setText(path)

    # ── Generate ──────────────────────────────────────────────────────────────

    def _generate(self):
        errors = []
        job_ref    = self._job_ref_edit.text().strip()
        azn_tpl    = self._azn_tpl_edit.text().strip()
        usd_tpl    = self._usd_tpl_edit.text().strip()
        output_dir = self._out_dir_edit.text().strip()

        if not job_ref:
            errors.append("Job Ref is required.")
        if not azn_tpl:
            errors.append("AZN Template not selected.")
        if not usd_tpl:
            errors.append("USD Template not selected.")
        if not output_dir:
            errors.append("Output folder not selected.")
        if self._req_manpower_tbl.rowCount() == 0:
            errors.append("Manpower table is empty — load a CTR Request or add rows first.")

        if errors:
            log.warning("Generate blocked: %s", "; ".join(errors))
            QMessageBox.warning(self, "Cannot generate", "\n".join(errors))
            return

        log.info("Generate clicked: job_ref=%s output_dir=%s", job_ref, output_dir)

        # Only rows with a "✓" status (matched against pricebook/SAGE, or
        # manually added) are included — "✗ No match" rows are skipped so
        # unmatched requests never silently appear in the output at rate 0.
        onshore_rows, offshore_rows = [], []
        mp_skipped = 0
        for r in range(self._req_manpower_tbl.rowCount()):
            status_item = self._req_manpower_tbl.item(r, _MP_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                mp_skipped += 1
                continue
            def _txt(c, _r=r):
                item = self._req_manpower_tbl.item(_r, c)
                return item.text().strip() if item else ""
            try:
                num_emp = float(_txt(_MP_NUMEMP) or 1)
            except ValueError:
                num_emp = 1.0
            try:
                qty = float(_txt(_MP_QTY) or 0)
            except ValueError:
                qty = 0.0
            try:
                rate = float(_txt(_MP_RATE) or 0)
            except ValueError:
                rate = 0.0
            row_dict = {
                "num_employees": num_emp,
                "description":   _txt(_MP_DESC),
                "quantity":      qty,
                "uom":           _txt(_MP_UOM) or "Hours",
                "rate_azn":      rate,
                "nationality":   _txt(_MP_NAT) or "NAT",
            }
            if "onshore" in _txt(_MP_TYPE).lower():
                onshore_rows.append(row_dict)
            else:
                offshore_rows.append(row_dict)

        equip_rows = []
        eq_skipped = 0
        for r in range(self._req_equip_tbl.rowCount()):
            status_item = self._req_equip_tbl.item(r, _EQ_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                eq_skipped += 1
                continue
            def _etxt(c, _r=r):
                item = self._req_equip_tbl.item(_r, c)
                return item.text().strip() if item else ""
            try:
                qty = float(_etxt(_EQ_QTY) or 1)
            except ValueError:
                qty = 1.0
            try:
                rate = float(_etxt(_EQ_RATE) or 0)
            except ValueError:
                rate = 0.0
            try:
                days = float(_etxt(_EQ_DAYS) or 30)
            except ValueError:
                days = 30.0
            equip_rows.append({
                "description":  _etxt(_EQ_DESC),
                "quantity":     qty,
                "unit":         _etxt(_EQ_UNIT) or "DAY",
                "rate_per_day": rate,
                "days":         days,
                "stock_code":   _etxt(_EQ_CODE),
            })

        consump_rows = []
        cs_skipped = 0
        for r in range(self._req_cons_tbl.rowCount()):
            status_item = self._req_cons_tbl.item(r, _CS_STATUS)
            if not (status_item and status_item.text().startswith("✓")):
                cs_skipped += 1
                continue
            def _ctxt(c, _r=r):
                item = self._req_cons_tbl.item(_r, c)
                return item.text().strip() if item else ""
            try:
                qty = float(_ctxt(_CS_QTY) or 1)
            except ValueError:
                qty = 1.0
            try:
                price = float(_ctxt(_CS_PRICE) or 0)
            except ValueError:
                price = 0.0
            consump_rows.append({
                "long_description":  _ctxt(_CS_DESC),
                "local_expect_cost": price,
                "unit_code":         _ctxt(_CS_UNIT) or "EA",
                "product":           _ctxt(_CS_CODE),
                "quantity":          qty,
            })

        total_skipped = mp_skipped + eq_skipped + cs_skipped
        if total_skipped:
            log.info(
                "Generate: skipping %d unmatched row(s) (manpower=%d equip=%d cons=%d)",
                total_skipped, mp_skipped, eq_skipped, cs_skipped,
            )

        # CTR header info — shared across both Source Files sub-tabs
        header = {
            "client":      self._client_edit.text().strip(),
            "sub_client":  self._sub_client_edit.text().strip(),
            "location":    self._location_edit.text().strip(),
            "scope":       self._scope_edit.text().strip(),
            "date":        self._date_edit.text().strip(),
            "contract_no": self._contract_no_edit.text().strip(),
            "revision":    self._revision_edit.text().strip(),
        }

        # Progress dialog
        dlg = QProgressDialog("Generating CTR documents…", None, 0, 0, self)
        dlg.setWindowTitle("Generating")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.show()

        worker = CTRWorker(
            onshore_rows  = onshore_rows,
            offshore_rows = offshore_rows,
            equip_rows    = equip_rows,
            consump_rows  = consump_rows,
            azn_tpl       = azn_tpl,
            usd_tpl       = usd_tpl,
            output_dir    = output_dir,
            job_ref       = job_ref,
            header        = header,
            parent        = self,
        )
        self._workers.append(worker)

        def _done(paths: list[str]):
            dlg.close()
            body = "\n".join(f"  • {p}" for p in paths)
            note = (
                f"\n\nSkipped {total_skipped} unmatched row(s) — not included "
                f"in the output." if total_skipped else ""
            )
            QMessageBox.information(
                self, "Generation complete",
                f"CTR documents saved:\n\n{body}{note}"
            )

        def _err(msg: str):
            dlg.close()
            QMessageBox.critical(self, "Generation failed", msg)

        worker.finished.connect(_done)
        worker.error.connect(_err)
        worker.start()
