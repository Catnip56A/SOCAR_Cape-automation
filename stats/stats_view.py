"""The "Statistics" tab of the MR vs CTR Comparator (task MA131).

`StatsPanel` shows, per MR / CTR document, how much of the SAGE expected cost
is rechargeable, non-rechargeable or unknown (see cost_stats.py for the rules).
`StatsWorker` does the slow part (reading the SAGE export and costing every
row) on a background thread, registered in TASKS so it shows in the F12
Inspector with the other jobs.

No stylesheet is set on any container here: a per-widget stylesheet on a
parent turns every descendant tooltip solid black (see the QSS note in
app.py's main()). Colours come from item brushes and the one leaf QLabel.
"""
from __future__ import annotations

import pandas as pd
from PySide6.QtCore import QPoint, QRect, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLayout, QMenu,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from stats import cost_stats as cs
from common.attention import AttentionDelegate, mark_attention
from common.find_bar import install_find
from common.hover_text import install_elided_tooltips
from common.formula import FormulaError, evaluate as evaluate_formula, format_number, is_formula
from ctr_tools.parser import parse_equip_names_db, parse_sage, parse_usd_pricebook
from ctr_tools.stockcode_types import confirm_types, current_types
from ctr_tools.tasks import TASKS

STOPPED = "stopped"          # StatsWorker's error text when the user stopped it

_RED = QColor("#C62828")
_MANUAL = QColor("#E65100")
_MUTED = QColor("#757575")
_NOT_COSTED_BG = QColor("#FFEBEE")
_DAYS_NEEDED_BG = QColor("#FFE0B2")           # the Days cell that waits for the user's input

# Tree columns.
(C_NAME, C_DESC, C_TYPE, C_QTY, C_UNIT, C_DAYS, C_RECH, C_RECH_P, C_NON, C_NON_P, C_UNK, C_UNK_P,
 C_TOTAL, C_RATIO, C_NOTE) = range(15)
_HEADERS = ["Document / Stock Code", "Description", "Type", "Qty", "Unit cost", "Days",
            "Rechargeable", "%", "Non-Rechargeable", "%", "Unknown", "%",
            "Total cost", "Non ÷ Rech", "Items / note"]
MODE_COST = "cost"           # Document costs: SAGE's expected unit cost × quantity
MODE_PRICE = "price"         # Document price: what the documents bill (rate × quantity × days)

# What the Settings window can preset (QSettings keys; the values are read when a panel is built).
_SETTINGS_ORG = ("SOCAR", "CTRGenerator")
KEY_DEFAULT_MODE = "stats/default_mode"
KEY_DEFAULT_SCAFFOLD = "stats/include_scaffold"
KEY_DEFAULT_MARKUP = "stats/with_markup"


def stats_defaults() -> dict:
    """{"mode", "scaffold", "markup"} as set in Settings (mode: MODE_COST / MODE_PRICE;
    scaffold: include scaffold, off by default; markup: price with markup, on by default)."""
    s = QSettings(*_SETTINGS_ORG)
    def flag(key, default):
        v = s.value(key, default)
        return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")
    mode = str(s.value(KEY_DEFAULT_MODE, MODE_COST) or MODE_COST)
    return {"mode": mode if mode in (MODE_COST, MODE_PRICE) else MODE_COST,
            "scaffold": flag(KEY_DEFAULT_SCAFFOLD, False), "markup": flag(KEY_DEFAULT_MARKUP, True)}


def save_stats_defaults(mode: str | None = None, scaffold: bool | None = None,
                        markup: bool | None = None) -> None:
    s = QSettings(*_SETTINGS_ORG)
    if mode is not None:
        s.setValue(KEY_DEFAULT_MODE, mode)
    if scaffold is not None:
        s.setValue(KEY_DEFAULT_SCAFFOLD, bool(scaffold))
    if markup is not None:
        s.setValue(KEY_DEFAULT_MARKUP, bool(markup))
_BUCKET_COLS = {cs.RECHARGEABLE: (C_RECH, C_RECH_P),
                cs.NON_RECHARGEABLE: (C_NON, C_NON_P),
                cs.UNKNOWN: (C_UNK, C_UNK_P)}

_ROLE_KIND = Qt.ItemDataRole.UserRole          # "group" | "doc" | "item" | "more"
_ROLE_KEY = Qt.ItemDataRole.UserRole + 1       # (side, doc) for a doc, stock code for an item
_ROLE_SIDE = Qt.ItemDataRole.UserRole + 2
_ROLE_EDIT = Qt.ItemDataRole.UserRole + 3      # the column of an item row the user may type into
_ROLE_DAYS_OPTIONS = Qt.ItemDataRole.UserRole + 4   # JSON: the days several CTRs give for this Stock Code

_MAX_ITEMS_PER_DOC = 2000

_SORTS = [
    ("Highest cost first", "cost", False),
    ("Lowest cost first", "cost", True),
    ("Highest unit cost first", "unit_cost", False),
    ("Lowest unit cost first", "unit_cost", True),
    ("Stock Code (A–Z)", "key", True),
]


def _money(v) -> str:
    return "" if v is None or pd.isna(v) else f"{v:,.2f}"


def _pct(v) -> str:
    return "" if v is None or pd.isna(v) else f"{v:.1f}%"


def _qty(v) -> str:
    if v is None or pd.isna(v):
        return ""
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


class _FlowLayout(QLayout):
    """A row of widgets that wraps onto further lines when the window is too
    narrow (the Qt "flow layout" example). The toolbar used to be a single
    HBox whose right end — the dropdowns and Reload — was cut off in a small
    window."""

    def __init__(self, hspacing: int = 8, vspacing: int = 4):
        super().__init__()
        self._items: list = []
        self._h, self._v = hspacing, vspacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._lay_out(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._lay_out(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _lay_out(self, rect, apply: bool) -> int:
        m = self.contentsMargins()
        x, y = rect.x() + m.left(), rect.y() + m.top()
        right = rect.right() - m.right()
        line_h = 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > right + 1 and line_h > 0:      # wrap
                x = rect.x() + m.left()
                y += line_h + self._v
                line_h = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._h
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y() + m.bottom()


def alert_icon(px: int = 16) -> QIcon:
    """A red circle with a white exclamation mark, drawn in code (no font or image
    file, so it looks the same everywhere)."""
    scale = 2
    pm = QPixmap(px * scale, px * scale)
    pm.setDevicePixelRatio(scale)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#D32F2F"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(0, 0, px, px)
    pen = QPen(QColor("white"))
    pen.setWidthF(px * 0.14)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    cx = px / 2
    p.drawLine(QPoint(round(cx), round(px * 0.22)), QPoint(round(cx), round(px * 0.60)))
    p.drawPoint(QPoint(round(cx), round(px * 0.78)))
    p.end()
    return QIcon(pm)


class DisparitiesDialog(QDialog):
    """The Stock Codes whose rechargeability in the document differs from SAGE, and
    a button per code to choose which source is right. The choice is kept per
    Stock Code, shared by every statistics list in the app, and remembered."""

    def __init__(self, parent, panel: "StatsPanel"):
        super().__init__(parent)
        self._panel = panel
        self.setWindowTitle("Disparities between the documents and SAGE")
        self.setMinimumSize(900, 420)
        self.resize(1100, 480)
        lay = QVBoxLayout(self)
        note = QLabel(
            "These Stock Codes are Rechargeable in one place and Non-Rechargeable in the other "
            "(the document against SAGE's own tag). Until a source is chosen they are NOT counted in "
            "the statistics, and the CTR Generator will not generate. Choose, for each code, which "
            "source is right: the document, or SAGE.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self._show_resolved = QCheckBox("Also show the ones I have already chosen a source for")
        self._show_resolved.toggled.connect(lambda _=False: self._fill())
        lay.addWidget(self._show_resolved)
        # The "Choose source…" button is the FIRST column, so it is always in view — however wide the
        # Item text gets, nobody has to scroll to the right to decide.
        self.table = QTableWidget(0, len(cs.DISPARITY_COLUMNS) + 1)
        self.table.setHorizontalHeaderLabels([""] + cs.DISPARITY_COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        install_elided_tooltips(self.table)
        install_find(self.table)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.count = QLabel("")
        self.count.setStyleSheet("color: #757575; font-size: 11px;")
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(self.count, 1)
        row.addWidget(close)
        lay.addLayout(row)
        self._fill()

    def _fill(self) -> None:
        table = self._panel.disparity_table(include_resolved=self._show_resolved.isChecked())
        self.table.setRowCount(0)                     # drops the old rows' buttons too
        self.table.setRowCount(len(table))
        for r, rec in enumerate(table.itertuples(index=False)):
            for c, v in enumerate(rec):
                self.table.setItem(r, c + 1, QTableWidgetItem("" if v is None else str(v)))
            btn = QPushButton("Choose source…")
            btn.clicked.connect(lambda _=False, b=btn, key=str(rec[2]), nm=str(rec[3]), d=str(rec[4]),
                                s=str(rec[5]), ch=str(rec[6]): self._choose(b, key, nm, d, s, ch))
            self.table.setCellWidget(r, 0, btn)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(False)
        item_col = cs.DISPARITY_COLUMNS.index("Item") + 1
        if self.table.columnWidth(item_col) > 300:                       # long names are cut (hover shows them all)
            self.table.setColumnWidth(item_col, 300)
        self.table.setColumnWidth(0, 130)                                 # the button column: fixed, always visible
        open_n = len(self._panel.disparity_table())
        self.count.setText(f"{open_n} Stock Code(s) still need a choice" if open_n
                           else "Nothing left to choose — all disparities are resolved")

    def _choose(self, button, key: str, name: str, doc_says: str, sage_says: str, chosen: str) -> None:
        menu = QMenu(self)
        title = menu.addAction(f"{key}" + (f" — {name}" if name else ""))     # which item this is about
        title.setEnabled(False)
        menu.addSeparator()
        a_doc = menu.addAction(f"Use the document: {doc_says}" + ("   ✓" if chosen.startswith("Document") else ""))
        a_sage = menu.addAction(f"Use SAGE: {sage_says}" + ("   ✓" if chosen.startswith("SAGE") else ""))
        a_clear = menu.addAction("Clear my choice") if chosen else None
        act = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if act is None:
            return
        self._panel.set_choice(key, cs.CHOICE_DOC if act is a_doc else
                               cs.CHOICE_SAGE if act is a_sage else None)
        self._fill()


class TypeDecisionsDialog(QDialog):
    """The Stock Codes whose Equipment / Consumable type needs the user — the sources disagree (a
    proposal is shown: accept it or choose) or nothing knows the code — each with its description so
    it is easy to decide. A decision goes into the Stock Code types list (Settings) and is remembered."""

    _COLS = cs.TYPE_ISSUE_COLUMNS

    def __init__(self, parent, panel: "StatsPanel"):
        super().__init__(parent)
        self._panel = panel
        self.setWindowTitle("Stock Code types — Equipment or Consumable")
        self.setMinimumSize(1000, 420)
        self.resize(1200, 500)
        lay = QVBoxLayout(self)
        note = QLabel(
            "Document price needs to know whether a Stock Code is Equipment (priced from the USD Pricebook, "
            "per day) or a Consumable (priced from SAGE). The lines below are NOT priced until you decide. "
            "“Sources disagree” means the Stock Code types list and the CTR section differ (or the code is in both "
            "lists): the proposal is shown — accept it, or choose yourself. “Unknown” means nothing knows the code.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.table = QTableWidget(0, len(self._COLS) + 1)
        self.table.setHorizontalHeaderLabels([""] + self._COLS)       # the Decide… button first: always in view
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        install_elided_tooltips(self.table)
        install_find(self.table)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.count = QLabel("")
        self.count.setStyleSheet("color: #757575; font-size: 11px;")
        self.accept_all = QPushButton("Accept all proposals")
        self.accept_all.setToolTip("Accept the proposed type for every line marked “Sources disagree”. "
                                   "Unknown ones still need a choice.")
        self.accept_all.clicked.connect(self._accept_all)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(self.count, 1)
        row.addWidget(self.accept_all)
        row.addWidget(close)
        lay.addLayout(row)
        self._fill()

    def _fill(self) -> None:
        t = self._panel.type_issue_table()
        self.table.setRowCount(0)
        self.table.setRowCount(len(t))
        for r, rec in enumerate(t.itertuples(index=False)):
            for c, v in enumerate(rec):
                self.table.setItem(r, c + 1, QTableWidgetItem("" if v is None else str(v)))
            btn = QPushButton("Decide…")
            btn.clicked.connect(lambda _=False, b=btn, code=str(rec[0]), desc=str(rec[1]), prop=str(rec[4]):
                                self._decide(b, code, desc, prop))
            self.table.setCellWidget(r, 0, btn)
        self.table.resizeColumnsToContents()
        desc_col = self._COLS.index("Description") + 1
        if self.table.columnWidth(desc_col) > 340:
            self.table.setColumnWidth(desc_col, 340)
        why_col = self._COLS.index("Why") + 1
        if self.table.columnWidth(why_col) > 300:
            self.table.setColumnWidth(why_col, 300)
        self.table.setColumnWidth(0, 110)
        n = len(t)
        n_clash = int((t["Status"] == "Sources disagree").sum()) if n else 0
        self.accept_all.setEnabled(n_clash > 0)
        self.count.setText(f"{n} Stock Code(s) need a decision" if n else "Nothing left to decide")

    def _decide(self, button, code: str, desc: str, proposed: str) -> None:
        menu = QMenu(self)
        title = menu.addAction(f"{code}" + (f" — {desc}" if desc else ""))
        title.setEnabled(False)
        menu.addSeparator()
        a_accept = menu.addAction(f"Accept the proposal: {proposed}") if proposed else None
        a_eq = menu.addAction("It is Equipment (priced from the USD Pricebook, per day)")
        a_cons = menu.addAction("It is a Consumable (priced from SAGE)")
        act = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if act is None:
            return
        kind = proposed if act is a_accept else cs.EQUIPMENT if act is a_eq else cs.CONSUMABLE
        self._panel.decide_types({code: kind})
        self._fill()

    def _accept_all(self) -> None:
        t = self._panel.type_issue_table()
        clash = t[t["Status"] == "Sources disagree"]
        if clash.empty:
            return
        if QMessageBox.question(
                self, "Accept all proposals",
                f"Accept the proposed type for {len(clash)} Stock Code(s)?\n\nThey are added to the Stock Code "
                "types list as confirmed (you can change any of them later in Settings).",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        self._panel.decide_types({str(c): str(k) for c, k in zip(clash["Stock Code"], clash["Proposed"])})
        self._fill()


class StatsWorker(QThread):
    """Reads SAGE (and, for the price view, the USD Pricebook and the Equipment
    Names bridge) and costs every row. Emits done(token, rows | None, error);
    `token` lets the window ignore a result that a newer run has replaced."""
    done = Signal(int, object, str)

    def __init__(self, token: int, items: pd.DataFrame, sage_path: str,
                 sage_df: pd.DataFrame | None, parent=None, *,
                 usd_path: str = "", usd_df: pd.DataFrame | None = None,
                 names_path: str = "", names_df: pd.DataFrame | None = None):
        super().__init__(parent)
        self._token = token
        self._items = items
        self._path = sage_path
        self._sage_df = sage_df            # already parsed by the Generator, or None
        self._usd_path, self._usd_df = usd_path, usd_df
        self._names_path, self._names_df = names_path, names_df

    def run(self) -> None:
        job = TASKS.begin_job("Cost statistics",
                              ["Read SAGE Export", "Read pricebook", "Calculate costs"], parallel=True)
        try:
            if job.should_stop():
                job.skip_remaining(0)
                job.finish()
                self.done.emit(self._token, None, STOPPED)
                return
            job.start(0)
            df = self._sage_df if self._sage_df is not None else parse_sage(self._path)
            cost_map = cs.sage_cost_map(df)
            sage_tags = cs.sage_tag_map(df)
            sage_names = cs.sage_name_map(df)
            job.done(0, f"{len(cost_map)} products"
                        + (" (reused)" if self._sage_df is not None else ""))
            if job.should_stop():
                job.skip_remaining(1)
                job.finish()
                self.done.emit(self._token, None, STOPPED)
                return
            job.start(1)
            usd = self._usd_df
            if usd is None and self._usd_path:
                usd = parse_usd_pricebook(self._usd_path)
            names = self._names_df
            if names is None and self._names_path:
                names = parse_equip_names_db(self._names_path)
            equip_rates = cs.equip_rate_map(self._items["key"].tolist(), names, usd)
            job.done(1, f"{len(equip_rates)} equipment rates" if usd is not None or names is not None
                        else "no USD Pricebook set")
            # Checked between tasks only, like every other job.
            if job.should_stop():
                job.skip_remaining(2)
                job.finish()
                self.done.emit(self._token, None, STOPPED)
                return
            job.start(2)
            rows = cs.cost_rows(self._items, cost_map, True, sage_tags=sage_tags,
                                sage_names=sage_names, equip_rates=equip_rates)
            job.done(2, f"{len(rows)} rows")
            job.finish()
            self.done.emit(self._token, rows, "")
        except Exception as exc:               # noqa: BLE001 — shown to the user
            done_n = sum(1 for st in job.steps if st.status == "done")
            job.failed(min(done_n, len(job.steps) - 1), str(exc))
            job.finish()
            self.done.emit(self._token, None, str(exc))


class _FormulaTreeDelegate(AttentionDelegate):
    """The statistics list's editable cells (Unit cost, Days) take formulas; a cell that holds the value
    a formula gave opens on that formula."""

    def __init__(self, tree, panel):
        super().__init__(tree)
        self._panel = panel

    def setEditorData(self, editor, index):
        super().setEditorData(editor, index)
        item = self._panel._tree.itemFromIndex(index)
        f = self._panel._formulas.get((index.column(), item.data(0, _ROLE_KEY), item.data(0, _ROLE_SIDE))) if item else None
        if f and hasattr(editor, "setText") and item.text(index.column()).strip().replace(",", ".") == f[1]:
            editor.setText(f[0])
            editor.selectAll()


class StatsPanel(QWidget):
    reload_requested = Signal()
    # A row was double-clicked: (side, document, Stock Code) — the Stock Code is "" for a
    # document row. The CTR Generator uses it to show that line in its own table.
    row_clicked = Signal(str, str, str)
    # The user chose (or cleared) which source is right for a Stock Code's
    # rechargeability; payload = the whole {STOCK CODE: "SAGE" | "DOC"} dict.
    choices_changed = Signal(object)
    # The user typed (or cleared) the days of a Stock Code in the price view;
    # payload = the whole {STOCK CODE: days} dict. The window saves it with the comparison.
    days_changed = Signal(object)
    # Mode or toggles changed: the window refreshes anything that mirrors them (e.g. the totals box).
    view_changed = Signal()
    # The user decided or accepted Stock Code types here; the window refreshes what shows the type.
    types_changed = Signal()

    def __init__(self, parent=None, *, group_labels: dict[str, str] | None = None,
                 show_type_filter: bool = True, min_tree_height: int | None = None,
                 no_sage_note: str = "", show_markup: bool = False,
                 use_stock_types: bool = False, show_scaffold: bool = True):
        """group_labels: top-level row text per side (default "MR — all documents
        shown" / "CTR — all documents shown"). The CTR Generator embeds this
        panel with one "CTR Request" group and no Type filter, and with the
        markup option (`show_markup`): the MR vs CTR tab prices without markup, and without
        the scaffold option (`show_scaffold`): it says there that scaffold is left out."""
        super().__init__(parent)
        self._show_markup = show_markup
        self._show_scaffold = show_scaffold
        # The MR vs CTR statistics decide Equipment / Consumable from the Stock Code types list;
        # the CTR Generator's lines already know theirs (its own tables).
        self._use_stock_types = use_stock_types
        self._type_rows: pd.DataFrame | None = None     # rows with their type resolved (for the decisions window)
        self._mode = MODE_COST
        self._days: dict[str, float] = {}           # typed-in days by Stock Code (price view)
        self._formulas: dict = {}                    # (column, key, side) -> (formula, the value text it gave)
        self._days_editable: set[str] = set()       # Stock Codes whose days the user may type
        self._markup_rate = 0.0                     # fraction, set by the window (Generator spinbox)
        self._price_note = ""                       # e.g. "USD Pricebook not set", shown in the price view
        self._group_labels = group_labels or {}
        self._no_sage_note = no_sage_note        # extra sentence on the "not in SAGE" banner
        self._base: pd.DataFrame | None = None     # rows as costed from SAGE (use_qty on)
        self._manual: dict[str, float] = {}        # typed-in unit costs, by Stock Code
        self._choices: dict[str, str] = {}         # which source is right per Stock Code: "SAGE" | "DOC"
        self._pred = None                          # rows -> list[bool], or None
        self._view: pd.DataFrame = pd.DataFrame()  # rows after manual costs + filters
        self._summary: pd.DataFrame = pd.DataFrame()
        self._expanded: set[tuple[str, str]] = set()
        self._building = False
        self._state_kind: str | None = None        # banner set by the window, if any
        self._costed_all: pd.DataFrame | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        # Not closable on purpose: it only goes away when the problem does.
        self._banner = QLabel()
        self._banner.setWordWrap(True)
        self._banner.setTextFormat(Qt.TextFormat.PlainText)
        self._banner.setVisible(False)
        lay.addWidget(self._banner)

        bar = _FlowLayout(hspacing=8, vspacing=4)
        self._cost_btn = QPushButton("Document costs")
        self._price_btn = QPushButton("Document price")
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        for b, tip in ((self._cost_btn, "SAGE's expected unit cost × quantity — what the documents' items cost us."),
                       (self._price_btn, "What the documents bill: rate × quantity (× days for equipment and "
                                         "scaffold). Equipment rates come from the USD Pricebook, consumables from "
                                         "SAGE. Non-rechargeable items are 0.")):
            b.setCheckable(True)
            b.setToolTip(tip)
            self._mode_group.addButton(b)
            bar.addWidget(b)
        self._cost_btn.setChecked(True)
        self._cost_btn.clicked.connect(lambda _=False: self.set_mode(MODE_COST))
        self._price_btn.clicked.connect(lambda _=False: self.set_mode(MODE_PRICE))

        self._scaffold_chk = QCheckBox("Include scaffold")
        self._scaffold_chk.setToolTip(
            "Scaffold is equipment billed per ton (quantity = tonnage) × rate/day × days.\n"
            "Off by default; the default can be changed in Settings. It has no SAGE cost, so it\n"
            "only appears in Document price.")
        self._scaffold_chk.toggled.connect(lambda _=False: self._on_toggle())
        self._no_scaffold_lbl = QLabel("Scaffold: not included here")
        self._no_scaffold_lbl.setStyleSheet("color: #757575; font-size: 11px;")
        self._no_scaffold_lbl.setToolTip(
            "Scaffold is left out of the MR vs CTR statistics: a scaffold line in a document is not\n"
            "priced here. The CTR Generator's statistics have the Include scaffold option.")
        self._markup_chk = QCheckBox("With markup")
        self._markup_chk.setToolTip(
            "Adds the CTR Generator's Markup Rate to consumables (not to equipment or labour).")
        self._markup_chk.toggled.connect(lambda _=False: self._on_toggle())

        self._qty_chk = QCheckBox("Multiply cost by quantity")
        self._qty_chk.setChecked(True)
        self._qty_chk.setToolTip(
            "On: cost = SAGE expected unit cost × quantity (× days for equipment).\n"
            "Off: cost = unit cost alone, one per row.")
        self._qty_chk.toggled.connect(lambda _=False: self.refresh())

        self._cat_combo = QComboBox()
        self._cat_combo.addItems(["All types", cs.EQUIPMENT, cs.CONSUMABLE, cs.UNCATEGORISED])
        self._cat_combo.setToolTip(
            "Equipment / Consumable comes from the CTR's own section for that Stock Code.\n"
            "Stock Codes that are not in any CTR are Uncategorised.")
        self._cat_combo.currentIndexChanged.connect(lambda _=0: self.refresh())

        self._bucket_combo = QComboBox()
        self._bucket_combo.addItems(["All items", cs.RECHARGEABLE, cs.NON_RECHARGEABLE,
                                     cs.UNKNOWN, "Not counted"])
        self._bucket_combo.currentIndexChanged.connect(lambda _=0: self.refresh())

        self._sort_combo = QComboBox()
        self._sort_combo.addItems([label for label, _c, _a in _SORTS])
        self._sort_combo.setToolTip("Order of the Stock Codes inside each document.")
        self._sort_combo.currentIndexChanged.connect(lambda _=0: self.refresh())

        self._reload_btn = QPushButton("Reload statistics")
        self._reload_btn.setToolTip(
            "Read the SAGE Export again and recalculate.\n"
            "Use it after updating SAGE.")
        self._reload_btn.clicked.connect(self.reload_requested)

        self._dispar_btn = QPushButton("Disparities")
        self._dispar_btn.setIcon(alert_icon(16))
        self._dispar_btn.setToolTip(
            "Stock Codes whose rechargeability in the document differs from SAGE's own tag.\n"
            "They are not counted in the statistics. Click to see the list.")
        self._dispar_btn.setVisible(False)
        self._dispar_btn.clicked.connect(self._show_disparities)

        self._types_btn = QPushButton("Stock Code types")
        self._types_btn.setIcon(alert_icon(16))
        self._types_btn.setToolTip(
            "Stock Codes whose Equipment / Consumable type needs a decision: the list and the CTR section\n"
            "disagree (accept the proposal or choose), or nothing knows the code. They are not priced in\n"
            "Document price until then. Click to decide, with each item's description.")
        self._types_btn.setVisible(False)
        self._types_btn.clicked.connect(self._show_type_decisions)

        self._counts = QLabel("")
        self._counts.setStyleSheet("color: #757575; font-size: 11px;")

        bar.addWidget(self._qty_chk)
        bar.addWidget(self._scaffold_chk)
        bar.addWidget(self._no_scaffold_lbl)
        bar.addWidget(self._markup_chk)
        self._cat_combo.setVisible(show_type_filter)
        bar.addWidget(self._cat_combo)
        bar.addWidget(self._bucket_combo)
        bar.addWidget(self._sort_combo)
        bar.addWidget(self._reload_btn)
        bar.addWidget(self._dispar_btn)
        bar.addWidget(self._types_btn)
        bar.addWidget(self._counts)
        lay.addLayout(bar)

        self._tree = QTreeWidget()
        install_elided_tooltips(self._tree)
        install_find(self._tree)
        self._tree.setColumnCount(len(_HEADERS))
        self._tree.setHeaderLabels(_HEADERS)
        self._tree.setUniformRowHeights(True)
        self._tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Red / orange cells keep their colours when their row is selected (see common/attention.py).
        self._tree.setItemDelegate(_FormulaTreeDelegate(self._tree, self))
        # Opening a cell for editing blanks its warning text ("type days", "not in SAGE"); if the editor is
        # closed without writing anything the list is rebuilt, so the warning (text, orange / red) is still there.
        self._tree.itemDelegate().closeEditor.connect(lambda *_a: QTimer.singleShot(0, self.refresh))
        self._tree.setAlternatingRowColors(True)
        self._tree.header().setStretchLastSection(True)
        self._tree.setColumnWidth(C_NAME, 300)
        self._tree.setColumnWidth(C_DESC, 220)
        for c in (C_TYPE, C_QTY, C_UNIT, C_DAYS):
            self._tree.setColumnWidth(c, 95)
        for c in (C_RECH, C_NON, C_UNK, C_TOTAL):
            self._tree.setColumnWidth(c, 120)
        self._tree.setColumnWidth(C_RATIO, 90)
        self._tree.headerItem().setToolTip(
            C_RATIO, "Non-Rechargeable cost ÷ Rechargeable cost, in money "
                     "(follows “Multiply cost by quantity”). n/a = no rechargeable cost.")
        for c in (C_RECH_P, C_NON_P, C_UNK_P):
            self._tree.setColumnWidth(c, 62)
        self._tree.setColumnWidth(C_NOTE, 300)
        self._tree.itemExpanded.connect(self._on_expanded)
        self._tree.itemDoubleClicked.connect(self._on_row_double_clicked)
        self._tree.itemCollapsed.connect(self._on_collapsed)
        self._tree.itemDoubleClicked.connect(self._on_double_clicked)
        self._tree.itemChanged.connect(self._on_item_changed)
        if min_tree_height:
            self._tree.setMinimumHeight(min_tree_height)
        lay.addWidget(self._tree, 1)
        self.apply_defaults()

    # ── view: Document costs / Document price ────────────────────────────────

    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        mode = MODE_PRICE if mode == MODE_PRICE else MODE_COST
        changed = mode != self._mode
        self._mode = mode
        for b, m in ((self._cost_btn, MODE_COST), (self._price_btn, MODE_PRICE)):
            b.blockSignals(True)
            b.setChecked(mode == m)
            b.blockSignals(False)
        self._sync_mode_ui()
        if changed:
            self.refresh()
            self.view_changed.emit()

    def _on_toggle(self) -> None:
        self.refresh()
        self.view_changed.emit()

    def _sync_mode_ui(self) -> None:
        price = self._mode == MODE_PRICE
        self._qty_chk.setVisible(not price)
        self._scaffold_chk.setVisible(price and self._show_scaffold)
        self._no_scaffold_lbl.setVisible(price and not self._show_scaffold)
        self._markup_chk.setVisible(price and self._show_markup)
        self._tree.setColumnHidden(C_DAYS, False)               # costs use days too (equipment = days × cost × quantity)
        self._tree.setColumnHidden(C_RATIO, price)          # non-rechargeable is billed at 0: the ratio says nothing
        h = self._tree.headerItem()
        h.setText(C_UNIT, "Rate" if price else "Unit cost")
        h.setText(C_TOTAL, "Total price" if price else "Total cost")
        h.setToolTip(C_UNIT, "The rate × quantity (× days) is billed." if price else "")
        h.setToolTip(C_DAYS, "Days the equipment is costed and billed for (equipment = days × cost × quantity; a "
                             "consumable has none). Orange = missing: double-click to type or pick it.")
        self._qty_chk.setText("Multiply cost by quantity")

    def apply_defaults(self) -> None:
        """Set the mode and toggles to the defaults chosen in Settings."""
        d = stats_defaults()
        self._scaffold_chk.blockSignals(True)
        self._scaffold_chk.setChecked(d["scaffold"])
        self._scaffold_chk.blockSignals(False)
        self._markup_chk.blockSignals(True)
        self._markup_chk.setChecked(d["markup"])
        self._markup_chk.blockSignals(False)
        self.set_mode(d["mode"])
        self._sync_mode_ui()
        if self._base is not None:
            self.refresh()
        self.view_changed.emit()

    def include_scaffold(self) -> bool:
        return self._show_scaffold and self._scaffold_chk.isChecked()

    def markup_on(self) -> bool:
        """True when the price view adds the markup (only the Generator's panel has the option)."""
        return self._show_markup and self._markup_chk.isChecked()

    def set_markup_rate(self, rate: float) -> None:
        """The Generator's Markup Rate as a fraction (0.065 = 6.5 %)."""
        self._markup_rate = float(rate or 0.0)
        self._markup_chk.setText(f"With markup ({self._markup_rate * 100:g}%)")
        if self._mode == MODE_PRICE and self._base is not None:
            self.refresh()
            self.view_changed.emit()

    def markup_rate(self) -> float | None:
        """The markup the price view applies, or None when it applies none."""
        return self._markup_rate if self.markup_on() and self._markup_rate else None

    def set_price_note(self, text: str) -> None:
        """A sentence shown (in the price view) above the list, e.g. that the USD Pricebook is not set."""
        self._price_note = text or ""
        self._update_note_banner()

    def days(self) -> dict[str, float]:
        return dict(self._days)

    def set_days(self, days: dict[str, float]) -> None:
        self._days = {str(k): float(v) for k, v in (days or {}).items()}
        if self._base is not None:
            self.refresh()

    # ── state from the window ────────────────────────────────────────────────

    def show_state(self, kind: str, text: str) -> None:
        """A message that stays until set_rows()/clear() — "warn" and "info" are
        yellow, "error" red. There is deliberately no close button."""
        self._state_kind = kind
        self._style_banner(kind)
        self._banner.setText(text)
        self._banner.setVisible(True)

    def clear(self, text: str | None = None, kind: str = "warn") -> None:
        """Drop the results (no statistics yet), optionally saying why."""
        self._base = None
        self._state_kind = None
        self._banner.setVisible(False)
        self.refresh()
        if text:
            self.show_state(kind, text)

    def set_rows(self, rows: pd.DataFrame) -> None:
        self._base = rows
        self._state_kind = None
        self.refresh()

    def set_doc_filter(self, pred) -> None:
        """pred(rows) -> one bool per row for the document filter, or None."""
        self._pred = pred
        self.refresh()

    def has_rows(self) -> bool:
        return self._base is not None

    def use_qty(self) -> bool:
        return self._qty_chk.isChecked()

    def base_rows(self) -> pd.DataFrame | None:
        """The rows as costed from SAGE (before manual costs and filters)."""
        return self._base

    def manual_costs(self) -> dict[str, float]:
        return dict(self._manual)

    def choices(self) -> dict[str, str]:
        return dict(self._choices)

    def set_choices(self, choices: dict[str, str]) -> None:
        """Replace the source choices (from another list, or from disk) without
        announcing it, and recalculate."""
        self._choices = {str(k): v for k, v in (choices or {}).items() if v in (cs.CHOICE_SAGE, cs.CHOICE_DOC)}
        if self._base is not None:
            self.refresh()

    def set_choice(self, key: str, choice: str | None) -> None:
        """The user chose which source is right for `key` (None = clear it)."""
        if choice is None:
            self._choices.pop(key, None)
        else:
            self._choices[key] = choice
        self.refresh()
        self.choices_changed.emit(self.choices())

    def restore(self, rows: pd.DataFrame, manual: dict[str, float], use_qty: bool = True,
                choices: dict[str, str] | None = None, days: dict[str, float] | None = None) -> None:
        """Show statistics that were saved with a comparison."""
        self._manual = dict(manual)
        if choices is not None:
            self._choices = dict(choices)
        self._days = {str(k): float(v) for k, v in (days or {}).items()}
        self._qty_chk.blockSignals(True)
        self._qty_chk.setChecked(use_qty)
        self._qty_chk.blockSignals(False)
        self.set_rows(rows)

    def report_frames(self) -> tuple[pd.DataFrame, pd.DataFrame] | None:
        """(summary, items) for the Excel report, or None when there are no
        statistics. Every row — the panel's filters do not apply."""
        if self._base is None or self._base.empty:
            return None
        return cs.report_frames(self._base, self.use_qty(), self._manual, self._choices, self._stock_types(),
                                self._days)

    def price_report_frames(self) -> tuple[pd.DataFrame, pd.DataFrame] | None:
        """(summary, items) of the price view for the Excel report, or None when
        there are no statistics. Every row — the panel's filters do not apply; the
        scaffold and markup toggles do."""
        if self._base is None or self._base.empty:
            return None
        return cs.price_report_frames(self._base, self._manual, self._choices, self._stock_types(),
                                      days=self._days, markup_rate=self.markup_rate(),
                                      include_scaffold=self.include_scaffold())

    def _style_banner(self, kind: str) -> None:
        bg, fg, border = {
            "error": ("#FFEBEE", "#B71C1C", "#EF9A9A"),
        }.get(kind, ("#FFF8E1", "#6D4C00", "#FFD54F"))
        self._banner.setStyleSheet(
            f"background: {bg}; color: {fg}; border: 1px solid {border};"
            "border-radius: 4px; padding: 6px 10px; font-size: 12px;")

    # ── data → tree ──────────────────────────────────────────────────────────

    def _stock_types(self) -> dict | None:
        """The Stock Code types list (re-read when its file changed), or None for a panel that
        does not use it."""
        return current_types() if self._use_stock_types else None

    def _filtered(self) -> pd.DataFrame:
        if self._base is None or self._base.empty:
            self._type_rows = None
            return pd.DataFrame(columns=cs.ROW_COLUMNS)
        types = self._stock_types()
        rows = cs.apply_manual(self._base, self._manual, self._qty_chk.isChecked(), self._choices, types, self._days)
        self._type_rows = rows
        is_equip = rows["kind"].isin([cs.EQUIPMENT, cs.SCAFFOLD])
        if self._use_stock_types:
            needs = is_equip                                          # MR vs CTR: every equipment line can be edited
        else:                                                         # Generator: its own Days cells are the source
            needs = is_equip & (rows["days"].isna() | rows["days_note"].str.startswith("from"))
        self._days_editable = set(rows.loc[needs, "key"])
        # Scaffold has no SAGE cost: it only exists in the price view.
        self._costed_all = rows = rows[~rows["scaffold"]]
        if self._mode == MODE_PRICE:
            full = cs.apply_manual(self._base, self._manual, True, self._choices, types, self._days)
            rows = self._priced_all = cs.price_view(full, self._days, self.markup_rate(),
                                                    self.include_scaffold())
        if self._pred is not None:
            rows = rows[self._pred(rows)]
        cat = self._cat_combo.currentText()
        if cat != "All types":
            rows = rows[rows["category"] == cat]
        b = self._bucket_combo.currentText()
        if b == "Not counted":
            rows = rows[rows["problem"] != ""]
        elif b != "All items":
            rows = rows[(rows["bucket"] == b) & (rows["problem"] == "")]
        return rows

    def refresh(self) -> None:
        self._building = True
        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            self._view = self._filtered()
            self._summary = cs.summarize(self._view)
            self._fill_groups()
        finally:
            self._tree.blockSignals(False)
            self._building = False
        self._update_note_banner()

    def _fill_groups(self) -> None:
        s = self._summary
        if s.empty:
            self._counts.setText("")
            return
        bold = QFont()
        bold.setBold(True)
        n_docs = 0
        for side in ("MR", "CTR"):
            part = s[s["side"] == side]
            if part.empty:
                continue
            total = part[part["doc"] == cs.TOTAL_LABEL].iloc[0]
            docs = part[part["doc"] != cs.TOTAL_LABEL]
            n_docs += len(docs)
            g = QTreeWidgetItem(self._tree)
            g.setData(0, _ROLE_KIND, "group")
            self._fill_summary_item(
                g, self._group_labels.get(side, f"{side} — all documents shown"), total, bold)
            for _, row in docs.iterrows():
                d = QTreeWidgetItem(g)
                d.setData(0, _ROLE_KIND, "doc")
                d.setData(0, _ROLE_KEY, (side, row["doc"]))
                self._fill_summary_item(d, row["doc"], row, None)
                d.setToolTip(C_NAME, row["doc"])
                QTreeWidgetItem(d)                 # placeholder so it can expand
                d.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            g.setExpanded(True)
        self._counts.setText(f"{n_docs} document(s)")
        # Re-open documents that were open before this rebuild. Signals are
        # blocked here, so fill them in directly rather than via itemExpanded.
        for i in range(self._tree.topLevelItemCount()):
            g = self._tree.topLevelItem(i)
            for j in range(g.childCount()):
                d = g.child(j)
                if d.data(0, _ROLE_KEY) in self._expanded:
                    d.setExpanded(True)
                    self._populate_doc(d)

    def _fill_summary_item(self, item: QTreeWidgetItem, label: str, row, font) -> None:
        item.setText(C_NAME, label)
        for b, (cv, cp) in _BUCKET_COLS.items():
            item.setText(cv, _money(row[b]))
            item.setText(cp, _pct(row[f"{b} %"]))
        item.setText(C_TOTAL, _money(row["Total cost"]))
        ratio = row["Non/Rech"]
        if ratio is None or pd.isna(ratio):
            # No rechargeable cost to divide by: "n/a" when there is non-rechargeable cost.
            item.setText(C_RATIO, "n/a" if row[cs.NON_RECHARGEABLE] else "")
        else:
            item.setText(C_RATIO, f"{ratio:.2f}")
        note = f"{int(row['items'])} counted"
        if int(row["no_cost"]):
            note += f" · {int(row['no_cost'])} not counted"
        item.setText(C_NOTE, note)
        for c in range(len(_HEADERS)):
            if C_RECH <= c <= C_RATIO:
                item.setTextAlignment(c, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if font is not None:
                item.setFont(c, font)
        if int(row["no_cost"]):
            mark_attention(item, C_NOTE, None, _RED)       # red text: rows not counted

    # ── expanding a document ─────────────────────────────────────────────────

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _ROLE_KIND) != "doc":
            return
        self._expanded.add(item.data(0, _ROLE_KEY))
        if item.childCount() == 1 and item.child(0).data(0, _ROLE_KIND) is None:
            self._populate_doc(item)

    def _on_row_double_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        """Double-click on a row (not on the Unit cost cell: double-clicking that
        types a cost instead). A document row still opens/closes as well."""
        kind = item.data(0, _ROLE_KIND)
        if kind == "doc":
            side, doc = item.data(0, _ROLE_KEY)
            self.row_clicked.emit(side, doc, "")
        elif kind == "item" and col not in (C_UNIT, C_DAYS):
            parent = item.parent()
            if parent is not None and parent.data(0, _ROLE_KIND) == "doc":
                side, doc = parent.data(0, _ROLE_KEY)
                self.row_clicked.emit(side, doc, str(item.data(0, _ROLE_KEY)))

    def _on_collapsed(self, item: QTreeWidgetItem) -> None:
        if item.data(0, _ROLE_KIND) == "doc":
            self._expanded.discard(item.data(0, _ROLE_KEY))

    def _sorted(self, rows: pd.DataFrame) -> pd.DataFrame:
        _label, col, asc = _SORTS[self._sort_combo.currentIndex()]
        key = rows[col]
        if col != "key":
            return rows.assign(_s=key).sort_values("_s", ascending=asc, na_position="last",
                                                   kind="mergesort").drop(columns="_s")
        return rows.sort_values("key", ascending=True, kind="mergesort")

    def _populate_doc(self, doc_item: QTreeWidgetItem) -> None:
        side, doc = doc_item.data(0, _ROLE_KEY)
        v = self._view
        rows = self._sorted(v[(v["side"] == side) & (v["doc"] == doc)])
        total = float(rows.loc[rows["problem"] == "", "cost"].sum())
        was_building, was_blocked = self._building, self._tree.signalsBlocked()
        self._building = True
        self._tree.blockSignals(True)
        try:
            doc_item.takeChildren()
            for n, r in enumerate(rows.itertuples(index=False)):
                if n >= _MAX_ITEMS_PER_DOC:
                    more = QTreeWidgetItem(doc_item)
                    more.setData(0, _ROLE_KIND, "more")
                    more.setText(C_NAME, f"… {len(rows) - n} more — narrow it with the filters above")
                    more.setForeground(C_NAME, QBrush(_MUTED))
                    break
                self._add_item(doc_item, side, r, total)
        finally:
            self._tree.blockSignals(was_blocked)
            self._building = was_building

    def _add_item(self, parent: QTreeWidgetItem, side: str, r, doc_total: float) -> None:
        price = self._mode == MODE_PRICE
        it = QTreeWidgetItem(parent)
        it.setData(0, _ROLE_KIND, "item")
        it.setData(0, _ROLE_KEY, r.key)
        it.setData(0, _ROLE_SIDE, side)
        counted = r.problem == ""
        it.setText(C_NAME, r.key)
        desc = r.description or r.name
        it.setText(C_DESC, desc)
        it.setToolTip(C_DESC, desc)
        it.setText(C_TYPE, (r.kind or r.category) if price else r.category)
        it.setText(C_QTY, _qty(r.qty))
        notes = [r.sheet] if r.sheet else []
        editable: list[int] = []

        # Days — costs use them as well as prices: equipment = days × cost × quantity, a consumable has none.
        if r.kind in (cs.EQUIPMENT, cs.SCAFFOLD):
            it.setText(C_DAYS, _qty(r.days))
            if r.days_note:
                notes.append("days: " + r.days_note)
            if r.days_options:
                it.setData(0, _ROLE_DAYS_OPTIONS, r.days_options)
                it.setToolTip(C_DAYS, "Two or more CTRs give different days for this Stock Code.\n"
                                      "Double-click to pick one of them, or type your own number.")
            if r.problem == cs.NO_DAYS:
                it.setText(C_DAYS, "choose…" if r.days_options else "type days")
                mark_attention(it, C_DAYS, _DAYS_NEEDED_BG, _MANUAL)     # highlighted: waiting for the user
                notes.append("not counted — " + cs.how_to_fix(r.problem))
                if not r.days_options:
                    it.setToolTip(C_DAYS, "Equipment is costed and priced × days, and this line has none yet.\n"
                                          "Double-click and type them (used for every row with this Stock Code).")
            if r.key in self._days_editable:
                editable.append(C_DAYS)
                if not it.toolTip(C_DAYS):
                    it.setToolTip(C_DAYS, "Double-click to type the days (used for every row with this "
                                          "Stock Code). Clear it to remove.")
        elif r.kind == cs.CONSUMABLE:
            it.setText(C_DAYS, "—")
            it.setToolTip(C_DAYS, "No days: a consumable is not billed per day — its cost and price are "
                                  "cost × quantity.")
        else:
            it.setText(C_DAYS, "?")
            it.setToolTip(C_DAYS, "No days shown: whether days apply depends on the type (Equipment or "
                                  "Consumable), which is not decided yet — press Stock Code types.")

        if price:
            it.setText(C_UNIT, _money(r.unit_cost) if pd.notna(r.unit_cost) else "")
            if r.problem == cs.NO_RATE:
                it.setText(C_UNIT, "no rate")
                notes.append("not counted — no rate. " + cs.how_to_fix(r.problem, r.kind))
            elif r.problem in (cs.NO_TYPE, cs.TYPE_CONFLICT):
                notes.append("not counted — " + r.problem.lower() + ". " + cs.how_to_fix(r.problem, r.kind, r.type_note))
            elif r.problem == cs.NO_QTY:
                notes.append("not counted — no quantity. " + cs.how_to_fix(r.problem))
            elif r.problem == cs.DISPARITY:
                notes.append(f"not counted — rechargeability differs from SAGE "
                             f"(document: {r.bucket}, SAGE: {r.sage_bucket})")
        else:
            if r.source == cs.SOURCE_MANUAL:
                notes.append("unit cost entered manually")
            if r.problem == cs.NO_SAGE:
                it.setText(C_UNIT, "not in SAGE")
                notes.append("not counted — double-click Unit cost to enter it, or update SAGE and reload")
            else:
                it.setText(C_UNIT, _money(r.unit_cost))
                if r.problem in (cs.NO_TYPE, cs.TYPE_CONFLICT):
                    notes.append("not counted — " + r.problem.lower() + ". "
                                 + cs.how_to_fix(r.problem, r.kind, r.type_note))
                elif r.problem == cs.NO_QTY:
                    notes.append("not counted — no quantity")
                elif r.problem == cs.DISPARITY:
                    notes.append(f"not counted — rechargeability differs from SAGE "
                                 f"(document: {r.bucket}, SAGE: {r.sage_bucket})")
        if r.choice:
            notes.append("rechargeability: " + ("SAGE's value" if r.choice == cs.CHOICE_SAGE
                                                else "the document's value")
                         + " chosen")
        it.setText(C_NOTE, " · ".join(notes))
        if counted:
            cv, cp = _BUCKET_COLS[r.bucket]
            it.setText(cv, _money(r.cost))
            it.setText(cp, _pct(r.cost / doc_total * 100.0) if doc_total else "")
            it.setText(C_TOTAL, _money(r.cost))
        for c in range(len(_HEADERS)):
            if c >= C_QTY and c != C_NOTE:
                it.setTextAlignment(c, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tip = (f"{r.key}\nType: {r.category}"
               + ("\nCategory comes from the CTR section for this Stock Code."
                  if r.category != cs.UNCATEGORISED else
                  "\nNot found in any CTR section, so its type is unknown."))
        it.setToolTip(C_NAME, tip)
        if not counted:
            for c in range(len(_HEADERS)):
                if c == C_DAYS and r.problem == cs.NO_DAYS:
                    continue                      # keeps its orange "type days" highlight
                mark_attention(it, c, _NOT_COSTED_BG, _RED)               # red: not counted
        elif not price and r.source == cs.SOURCE_MANUAL:
            mark_attention(it, C_UNIT, None, _MANUAL)                     # orange: a unit cost typed by hand
        if not price and (r.problem == cs.NO_SAGE or r.source == cs.SOURCE_MANUAL):
            it.setToolTip(C_UNIT, "Double-click to type a unit cost for this Stock Code "
                                  "(used for every row with this code). Clear it to remove.")
            editable.append(C_UNIT)
        if editable:
            it.setData(0, _ROLE_EDIT, tuple(editable))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)

    # ── manual unit cost ─────────────────────────────────────────────────────

    def _editable_cols(self, item: QTreeWidgetItem) -> tuple[int, ...]:
        """The cells of an item row the user may type into: Unit cost for a code SAGE lacks,
        Days for equipment (in both views), or none."""
        if item.data(0, _ROLE_KIND) != "item" or not (item.flags() & Qt.ItemFlag.ItemIsEditable):
            return ()
        cols = item.data(0, _ROLE_EDIT)
        return tuple(cols) if cols else ()

    @staticmethod
    def days_choices(item: QTreeWidgetItem) -> list[tuple[str, float]]:
        """[(menu text, days)] for a line several CTRs give different days for: each CTR's value with the
        CTR(s) it comes from. Empty when the line has no such choice."""
        import json
        raw = item.data(0, _ROLE_DAYS_OPTIONS)
        if not raw:
            return []
        try:
            options = json.loads(raw)
        except ValueError:
            return []
        return [(f"{o['days']:g} days — " + (", ".join(o["docs"]) or "a CTR"), float(o["days"])) for o in options]

    def set_chosen_days(self, key: str, days: float | None) -> None:
        """The user picked (or cleared, with None) the days of a Stock Code."""
        if days is None:
            self._days.pop(key, None)
        else:
            self._days[key] = float(days)
        self.days_changed.emit(self.days())
        QTimer.singleShot(0, self.refresh)

    def _choose_days(self, item: QTreeWidgetItem) -> bool:
        """Several CTRs give different days for this Stock Code: let the user pick one of them (each shown
        with the CTR it comes from) or type their own. Returns True when this handled the click."""
        choices = self.days_choices(item)
        if not choices:
            return False
        key = item.data(0, _ROLE_KEY)
        menu = QMenu(self)
        menu.addAction(f"{key} — which days are right?").setEnabled(False)
        menu.addSeparator()
        acts = {menu.addAction(text): days for text, days in choices}
        menu.addSeparator()
        own = menu.addAction("Type my own number…")
        clear = menu.addAction("Clear my choice") if key in self._days else None
        rect = self._tree.visualItemRect(item)
        act = menu.exec(self._tree.viewport().mapToGlobal(rect.bottomLeft()))
        if act is None:
            return True
        if act in acts:
            self.set_chosen_days(key, acts[act])
        elif act is clear:
            self.set_chosen_days(key, None)
        elif act is own:
            self._building = True
            try:
                item.setText(C_DAYS, "" if key not in self._days else f"{self._days[key]}")
            finally:
                self._building = False
            self._tree.editItem(item, C_DAYS)
        return True

    def _on_double_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        if col not in self._editable_cols(item):
            return
        if col == C_DAYS and self._choose_days(item):
            return
        key = item.data(0, _ROLE_KEY)
        current = self._days if col == C_DAYS else self._manual
        self._building = True
        try:
            item.setText(col, "" if key not in current else f"{current[key]}")
        finally:
            self._building = False
        self._tree.editItem(item, col)

    @staticmethod
    def parse_days(text: str):
        """(ok, value): "" clears (ok, None); a non-negative number is (ok, value); anything else is not ok."""
        t = str(text).strip().replace(",", ".")
        if not t:
            return True, None
        try:
            v = evaluate_formula(t) if is_formula(t) else float(t)      # "=2*3.5" is worked out
        except (ValueError, FormulaError):
            return False, None
        return (v >= 0 and v == v), (v if v >= 0 and v == v else None)

    def other_side_has_line(self, key: str, side: str) -> bool:
        """True when the OTHER side (MR for a CTR line, CTR for an MR line) has an equipment line of this
        Stock Code — then changing days on one side can be synced to the other."""
        rows = self._type_rows
        if rows is None or rows.empty:
            return False
        other = "CTR" if side == "MR" else "MR"
        hit = rows[(rows["key"] == key) & (rows["side"] == other) & rows["kind"].isin([cs.EQUIPMENT, cs.SCAFFOLD])]
        return not hit.empty

    def ask_days_sync(self, key: str, side: str) -> str | None:
        """The pop-up: also apply these days to the other side's line? Returns "sync", "only" (this side)
        or None (cancel). `days_sync_chooser` replaces the dialog (tests)."""
        other = "CTR" if side == "MR" else "MR"
        if getattr(self, "days_sync_chooser", None) is not None:
            return self.days_sync_chooser(key, side, other)
        box = QMessageBox(self)
        box.setWindowTitle("Sync the days?")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"<b>{key}</b> has an {other} line as well as this {side} line.<br><br>"
                    f"Apply these days to the {other} line too?")
        sync = box.addButton(f"Sync with {other}", QMessageBox.ButtonRole.AcceptRole)
        only = box.addButton(f"Only {side}", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(sync)
        box.exec()
        clicked = box.clickedButton()
        box.deleteLater()
        return "sync" if clicked is sync else "only" if clicked is only else None

    def edit_days(self, key: str, side: str, value: float | None) -> bool:
        """The user typed the days (None = cleared) of one side's line of a Stock Code — in the Statistics list
        or in the Days cells of the results tables. In the MR vs CTR statistics the days are kept per side
        ("CODE@MR" / "CODE@CTR", winning over the lines' own days); when the other side has a line too, a
        pop-up asks whether to sync it. The CTR Generator's panel keeps one value per Stock Code (its own
        Days cells are the source). Returns False when the user cancelled."""
        if not self._use_stock_types:
            if value is None:
                self._days.pop(key, None)
            else:
                self._days[key] = value
        else:
            sides = [side]
            if self.other_side_has_line(key, side):
                choice = self.ask_days_sync(key, side)
                if choice is None:
                    QTimer.singleShot(0, self.refresh)           # put the cell back as it was
                    return False
                if choice == "sync":
                    sides.append("CTR" if side == "MR" else "MR")
            for sd in sides:
                if value is None:
                    self._days.pop(f"{key}@{sd}", None)
                else:
                    self._days[f"{key}@{sd}"] = value
        self.days_changed.emit(self.days())
        QTimer.singleShot(0, self.refresh)
        return True

    def _on_item_changed(self, item: QTreeWidgetItem, col: int) -> None:
        if self._building or col not in self._editable_cols(item):
            return
        key = item.data(0, _ROLE_KEY)
        typed = item.text(col)
        ok, value = self.parse_days(typed) if col == C_DAYS else self._parse_cost(typed)
        fkey = (col, key, item.data(0, _ROLE_SIDE))
        if ok and value is not None and is_formula(typed):
            self._formulas[fkey] = (typed.strip(), format_number(value))        # shown again when the cell is opened
        else:
            self._formulas.pop(fkey, None)
        if col == C_DAYS:
            if ok:
                self.edit_days(key, item.data(0, _ROLE_SIDE), value)
            else:
                QTimer.singleShot(0, self.refresh)               # not a number: back to what it was
            return
        if ok:
            if value is None:
                self._manual.pop(key, None)
            else:
                self._manual[key] = value
        # Rebuild after the editor has finished committing.
        QTimer.singleShot(0, self.refresh)

    @staticmethod
    def _parse_cost(text: str):
        return StatsPanel.parse_days(text)                       # same rule: blank clears, a non-negative number sets

    # ── Stock Codes SAGE does not have ──────────────────────────────────────

    def no_sage_keys(self) -> list[tuple[str, str, str]]:
        """(side, document, Stock Code) of every code SAGE does not know and no
        cost was typed in for, in a stable order."""
        costed = getattr(self, "_costed_all", None)
        if costed is None or costed.empty:
            return []
        m = costed.loc[costed["problem"] == cs.NO_SAGE, ["side", "doc", "key"]]
        return sorted({(str(s), str(d), str(k)) for s, d, k in m.itertuples(index=False)})

    def focus_key(self, side: str, doc: str, key: str) -> bool:
        """Show a Stock Code's row: open its document, select the row, scroll to
        it and open its Unit cost for typing. Filters that hide it are reset
        first. Returns False when the row cannot be shown."""
        for attempt in range(2):
            top = None
            for i in range(self._tree.topLevelItemCount()):
                g = self._tree.topLevelItem(i)
                for j in range(g.childCount()):
                    d = g.child(j)
                    if d.data(0, _ROLE_KEY) == (side, doc):
                        top = d
            if top is not None:
                top.setExpanded(True)                    # fills its rows (itemExpanded)
                if top.childCount() and top.child(0).data(0, _ROLE_KIND) != "item":
                    self._populate_doc(top)
                for n in range(top.childCount()):
                    it = top.child(n)
                    if it.data(0, _ROLE_KIND) == "item" and it.data(0, _ROLE_KEY) == key:
                        self._tree.setCurrentItem(it)
                        self._tree.scrollToItem(it, QAbstractItemView.ScrollHint.PositionAtCenter)
                        cols = self._editable_cols(it)
                        if cols:
                            self._tree.editItem(it, cols[0])
                        return True
            if attempt == 0:                             # a filter may hide it: show everything
                self._cat_combo.setCurrentIndex(0)
                self._bucket_combo.setCurrentIndex(max(0, self._bucket_combo.findText("Not counted")))
                self.refresh()
        return False

    # ── banner about uncosted rows ───────────────────────────────────────────

    def disparity_table(self, include_resolved: bool = False) -> pd.DataFrame:
        """The Stock Codes whose rechargeability differs from SAGE (see cost_stats.disparities)."""
        return cs.disparities(getattr(self, "_costed_all", None), include_resolved)

    def _show_disparities(self) -> None:
        dlg = DisparitiesDialog(self, self)
        dlg.exec()
        dlg.deleteLater()               # one dialog per click, not a hidden one left on the panel for every click

    def effective_rows(self) -> pd.DataFrame | None:
        """Every row as the statistics see it now — kind (Equipment / Consumable), days (own, from the
        matching CTR, or typed / picked by the user), type status — before any filter. The results tables
        use it for their Days and Local cost total columns. None before the statistics exist."""
        if self._base is None or self._base.empty:
            return None
        return cs.apply_manual(self._base, self._manual, True, self._choices, self._stock_types(), self._days)

    def type_issue_table(self) -> pd.DataFrame:
        """The Stock Codes whose type needs a decision (see cost_stats.type_issue_table)."""
        return cs.type_issue_table(self._type_rows)

    def decide_types(self, decisions: dict[str, str]) -> None:
        """The user decided / accepted types ({code: Equipment | Consumable}): they go into the
        Stock Code types list (and are remembered as confirmed), then everything is recalculated."""
        confirm_types(decisions)
        self.refresh()
        self.types_changed.emit()

    def _show_type_decisions(self) -> None:
        dlg = TypeDecisionsDialog(self, self)
        dlg.exec()
        dlg.deleteLater()

    def _update_note_banner(self) -> None:
        table = self.disparity_table() if self._state_kind is None else pd.DataFrame()
        n_disp = int(table["Stock Code"].nunique()) if len(table) else 0
        n_all = (int(self.disparity_table(True)["Stock Code"].nunique())
                 if self._state_kind is None and getattr(self, "_costed_all", None) is not None else 0)
        self._dispar_btn.setVisible(n_all > 0)             # also when all are resolved: to change a choice
        self._dispar_btn.setIcon(alert_icon(16) if n_disp else QIcon())
        if n_all:
            self._dispar_btn.setText(f"Disparities ({n_disp})" if n_disp else "Disparities (resolved)")
        issues = self.type_issue_table() if self._use_stock_types and self._state_kind is None else None
        n_types = len(issues) if issues is not None else 0
        self._types_btn.setVisible(n_types > 0)
        if n_types:
            self._types_btn.setText(f"Stock Code types ({n_types})")
        if self._state_kind is not None:        # a state from the window wins
            return
        n = 0
        price = self._mode == MODE_PRICE
        if self._base is not None and not self._base.empty and not price:
            costed = getattr(self, "_costed_all", None)
            if costed is not None:
                n = costed.loc[costed["problem"] == cs.NO_SAGE, "key"].nunique()
        parts = []
        if price and self._base is not None and not self._base.empty:
            if self._price_note:
                parts.append(self._price_note)
            priced = getattr(self, "_priced_all", None)
            if priced is not None:
                n_unknown = priced.loc[priced["problem"] == cs.NO_TYPE, "key"].nunique()
                n_clash = priced.loc[priced["problem"] == cs.TYPE_CONFLICT, "key"].nunique()
                if n_unknown:
                    parts.append(f"{n_unknown} Stock Code(s) have an unknown type (Equipment or Consumable) and are "
                                 "NOT priced. Add them in Settings → Stock Code types, or press “Stock Code types” "
                                 "here to choose each one.")
                if n_clash:
                    parts.append(f"{n_clash} Stock Code(s) have a type the sources disagree about and are NOT priced "
                                 "until you accept the proposal or choose — press “Stock Code types”.")
                n_rate = priced.loc[priced["problem"] == cs.NO_RATE, "key"].nunique()
                n_days = priced.loc[priced["problem"] == cs.NO_DAYS, "key"].nunique()
                if n_rate:
                    parts.append(f"{n_rate} Stock Code(s) have no rate (equipment: not in the USD "
                                 "Pricebook; consumables: not in SAGE) and are NOT counted (shown in red).")
                if n_days:
                    parts.append(f"{n_days} Stock Code(s) need their Days — double-click the Days cell "
                                 "of a red row to type it. They are NOT counted until then.")
        if n:
            parts.append(
                f"{n} Stock Code(s) are not in the SAGE Export and are NOT counted (shown in red). "
                "Double-click a red row's Unit cost to type it in, or update SAGE and press "
                "Reload statistics." + (f" {self._no_sage_note}" if self._no_sage_note else ""))
        if n_disp:
            parts.append(
                f"{n_disp} Stock Code(s) have a rechargeability that differs from SAGE and are NOT "
                "counted (shown in red) — press Disparities to see them.")
        if parts:
            self._style_banner("warn")
            self._banner.setText("\n".join(parts))
            self._banner.setVisible(True)
        else:
            self._banner.setVisible(False)
