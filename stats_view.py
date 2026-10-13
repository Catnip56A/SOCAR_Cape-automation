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
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLayout, QMenu, QPushButton,
    QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import cost_stats as cs
from find_bar import install_find
from hover_text import install_elided_tooltips
from ctr_tools.parser import parse_sage
from ctr_tools.tasks import TASKS

STOPPED = "stopped"          # StatsWorker's error text when the user stopped it

_RED = QColor("#C62828")
_MANUAL = QColor("#E65100")
_MUTED = QColor("#757575")
_NOT_COSTED_BG = QColor("#FFEBEE")

# Tree columns.
C_NAME, C_TYPE, C_QTY, C_UNIT, C_RECH, C_RECH_P, C_NON, C_NON_P, C_UNK, C_UNK_P, C_TOTAL, C_RATIO, C_NOTE = range(13)
_HEADERS = ["Document / Stock Code", "Type", "Qty", "Unit cost",
            "Rechargeable", "%", "Non-Rechargeable", "%", "Unknown", "%",
            "Total cost", "Non ÷ Rech", "Items / note"]
_BUCKET_COLS = {cs.RECHARGEABLE: (C_RECH, C_RECH_P),
                cs.NON_RECHARGEABLE: (C_NON, C_NON_P),
                cs.UNKNOWN: (C_UNK, C_UNK_P)}

_ROLE_KIND = Qt.ItemDataRole.UserRole          # "group" | "doc" | "item" | "more"
_ROLE_KEY = Qt.ItemDataRole.UserRole + 1       # (side, doc) for a doc, stock code for an item
_ROLE_SIDE = Qt.ItemDataRole.UserRole + 2

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
        self.table = QTableWidget(0, len(cs.DISPARITY_COLUMNS) + 1)
        self.table.setHorizontalHeaderLabels(cs.DISPARITY_COLUMNS + [""])
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
                self.table.setItem(r, c, QTableWidgetItem("" if v is None else str(v)))
            btn = QPushButton("Choose source…")
            btn.clicked.connect(lambda _=False, b=btn, key=str(rec[2]), nm=str(rec[3]), d=str(rec[4]),
                                s=str(rec[5]), ch=str(rec[6]): self._choose(b, key, nm, d, s, ch))
            self.table.setCellWidget(r, len(cs.DISPARITY_COLUMNS), btn)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setStretchLastSection(False)
        item_col = cs.DISPARITY_COLUMNS.index("Item")
        if self.table.columnWidth(item_col) > 300:                       # long names are cut (hover shows them all)
            self.table.setColumnWidth(item_col, 300)
        self.table.setColumnWidth(len(cs.DISPARITY_COLUMNS), 130)     # the button column: not stretched
        open_n = len(self._panel.disparity_table())
        self.count.setText(f"{open_n} Stock Code(s) still need a choice" if open_n
                           else "Nothing left to choose — all disparities are resolved")

    def _choose(self, button, key: str, name: str, doc_says: str, sage_says: str, chosen: str) -> None:
        menu = QMenu(self)
        title = menu.addAction(f"{key}" + (f" — {name}" if name else ""))     # which item this is about
        title.setEnabled(False)
        menu.addSeparator()
        a_doc = menu.addAction(f"Use the document: {doc_says}" + ("   ✓" if chosen == "Document" else ""))
        a_sage = menu.addAction(f"Use SAGE: {sage_says}" + ("   ✓" if chosen == "SAGE" else ""))
        a_clear = menu.addAction("Clear my choice") if chosen else None
        act = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if act is None:
            return
        self._panel.set_choice(key, cs.CHOICE_DOC if act is a_doc else
                               cs.CHOICE_SAGE if act is a_sage else None)
        self._fill()


class StatsWorker(QThread):
    """Reads SAGE and costs every row. Emits done(token, rows | None, error);
    `token` lets the window ignore a result that a newer run has replaced."""
    done = Signal(int, object, str)

    def __init__(self, token: int, items: pd.DataFrame, sage_path: str,
                 sage_df: pd.DataFrame | None, parent=None):
        super().__init__(parent)
        self._token = token
        self._items = items
        self._path = sage_path
        self._sage_df = sage_df            # already parsed by the Generator, or None

    def run(self) -> None:
        job = TASKS.begin_job("Cost statistics",
                              ["Read SAGE Export", "Calculate costs"], parallel=True)
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
            # Checked between tasks only, like every other job.
            if job.should_stop():
                job.skip_remaining(1)
                job.finish()
                self.done.emit(self._token, None, STOPPED)
                return
            job.start(1)
            rows = cs.cost_rows(self._items, cost_map, True, sage_tags=sage_tags, sage_names=sage_names)
            job.done(1, f"{len(rows)} rows")
            job.finish()
            self.done.emit(self._token, rows, "")
        except Exception as exc:               # noqa: BLE001 — shown to the user
            job.failed(0 if job.steps[0].status != "done" else 1, str(exc))
            job.finish()
            self.done.emit(self._token, None, str(exc))


class StatsPanel(QWidget):
    reload_requested = Signal()
    # A row was double-clicked: (side, document, Stock Code) — the Stock Code is "" for a
    # document row. The CTR Generator uses it to show that line in its own table.
    row_clicked = Signal(str, str, str)
    # The user chose (or cleared) which source is right for a Stock Code's
    # rechargeability; payload = the whole {STOCK CODE: "SAGE" | "DOC"} dict.
    choices_changed = Signal(object)

    def __init__(self, parent=None, *, group_labels: dict[str, str] | None = None,
                 show_type_filter: bool = True, min_tree_height: int | None = None,
                 no_sage_note: str = ""):
        """group_labels: top-level row text per side (default "MR — all documents
        shown" / "CTR — all documents shown"). The CTR Generator embeds this
        panel with one "CTR Request" group and no Type filter."""
        super().__init__(parent)
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
        self._qty_chk = QCheckBox("Multiply cost by quantity")
        self._qty_chk.setChecked(True)
        self._qty_chk.setToolTip(
            "On: cost = SAGE expected unit cost × quantity.\n"
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

        self._counts = QLabel("")
        self._counts.setStyleSheet("color: #757575; font-size: 11px;")

        bar.addWidget(self._qty_chk)
        self._cat_combo.setVisible(show_type_filter)
        bar.addWidget(self._cat_combo)
        bar.addWidget(self._bucket_combo)
        bar.addWidget(self._sort_combo)
        bar.addWidget(self._reload_btn)
        bar.addWidget(self._dispar_btn)
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
        self._tree.setAlternatingRowColors(True)
        self._tree.header().setStretchLastSection(True)
        self._tree.setColumnWidth(C_NAME, 340)
        for c in (C_TYPE, C_QTY, C_UNIT):
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
                choices: dict[str, str] | None = None) -> None:
        """Show statistics that were saved with a comparison."""
        self._manual = dict(manual)
        if choices is not None:
            self._choices = dict(choices)
        self._qty_chk.blockSignals(True)
        self._qty_chk.setChecked(use_qty)
        self._qty_chk.blockSignals(False)
        self.set_rows(rows)

    def report_frames(self) -> tuple[pd.DataFrame, pd.DataFrame] | None:
        """(summary, items) for the Excel report, or None when there are no
        statistics. Every row — the panel's filters do not apply."""
        if self._base is None or self._base.empty:
            return None
        return cs.report_frames(self._base, self.use_qty(), self._manual, self._choices)

    def _style_banner(self, kind: str) -> None:
        bg, fg, border = {
            "error": ("#FFEBEE", "#B71C1C", "#EF9A9A"),
        }.get(kind, ("#FFF8E1", "#6D4C00", "#FFD54F"))
        self._banner.setStyleSheet(
            f"background: {bg}; color: {fg}; border: 1px solid {border};"
            "border-radius: 4px; padding: 6px 10px; font-size: 12px;")

    # ── data → tree ──────────────────────────────────────────────────────────

    def _filtered(self) -> pd.DataFrame:
        if self._base is None or self._base.empty:
            return pd.DataFrame(columns=cs.ROW_COLUMNS)
        rows = cs.apply_manual(self._base, self._manual, self._qty_chk.isChecked(), self._choices)
        self._costed_all = rows
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
            item.setForeground(C_NOTE, QBrush(_RED))

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
        elif kind == "item" and col != C_UNIT:
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
        it = QTreeWidgetItem(parent)
        it.setData(0, _ROLE_KIND, "item")
        it.setData(0, _ROLE_KEY, r.key)
        it.setData(0, _ROLE_SIDE, side)
        counted = r.problem == ""
        it.setText(C_NAME, r.key)
        it.setText(C_TYPE, r.category)
        it.setText(C_QTY, _qty(r.qty))
        notes = [r.sheet] if r.sheet else []
        if r.source == cs.SOURCE_MANUAL:
            notes.append("unit cost entered manually")
        if r.choice:
            notes.append("rechargeability: " + ("SAGE's value chosen" if r.choice == cs.CHOICE_SAGE
                                                else "the document's value chosen"))
        if r.problem == cs.NO_SAGE:
            it.setText(C_UNIT, "not in SAGE")
            notes.append("not counted — double-click Unit cost to enter it, or update SAGE and reload")
        else:
            it.setText(C_UNIT, _money(r.unit_cost))
            if r.problem == cs.NO_QTY:
                notes.append("not counted — no quantity")
            elif r.problem == cs.DISPARITY:
                notes.append(f"not counted — rechargeability differs from SAGE "
                             f"(document: {r.bucket}, SAGE: {r.sage_bucket})")
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
                it.setForeground(c, QBrush(_RED))
                it.setBackground(c, QBrush(_NOT_COSTED_BG))
        elif r.source == cs.SOURCE_MANUAL:
            it.setForeground(C_UNIT, QBrush(_MANUAL))
        if r.problem == cs.NO_SAGE or r.source == cs.SOURCE_MANUAL:
            it.setToolTip(C_UNIT, "Double-click to type a unit cost for this Stock Code "
                                  "(used for every row with this code). Clear it to remove.")
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsEditable)

    # ── manual unit cost ─────────────────────────────────────────────────────

    def _on_double_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        if col != C_UNIT or item.data(0, _ROLE_KIND) != "item":
            return
        if not (item.flags() & Qt.ItemFlag.ItemIsEditable):
            return
        key = item.data(0, _ROLE_KEY)
        self._building = True
        try:
            item.setText(C_UNIT, "" if key not in self._manual else f"{self._manual[key]}")
        finally:
            self._building = False
        self._tree.editItem(item, C_UNIT)

    def _on_item_changed(self, item: QTreeWidgetItem, col: int) -> None:
        if self._building or col != C_UNIT or item.data(0, _ROLE_KIND) != "item":
            return
        key = item.data(0, _ROLE_KEY)
        text = item.text(C_UNIT).strip().replace(",", ".")
        if not text:
            self._manual.pop(key, None)
        else:
            try:
                value = float(text)
                if value < 0 or value != value:
                    raise ValueError
            except ValueError:
                value = None
            if value is not None:
                self._manual[key] = value
        # Rebuild after the editor has finished committing.
        QTimer.singleShot(0, self.refresh)

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
                        if it.flags() & Qt.ItemFlag.ItemIsEditable:
                            self._tree.editItem(it, C_UNIT)
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
        DisparitiesDialog(self, self).exec()

    def _update_note_banner(self) -> None:
        table = self.disparity_table() if self._state_kind is None else pd.DataFrame()
        n_disp = int(table["Stock Code"].nunique()) if len(table) else 0
        n_all = (int(self.disparity_table(True)["Stock Code"].nunique())
                 if self._state_kind is None and getattr(self, "_costed_all", None) is not None else 0)
        self._dispar_btn.setVisible(n_all > 0)             # also when all are resolved: to change a choice
        self._dispar_btn.setIcon(alert_icon(16) if n_disp else QIcon())
        if n_all:
            self._dispar_btn.setText(f"Disparities ({n_disp})" if n_disp else "Disparities (resolved)")
        if self._state_kind is not None:        # a state from the window wins
            return
        n = 0
        if self._base is not None and not self._base.empty:
            costed = getattr(self, "_costed_all", None)
            if costed is not None:
                n = costed.loc[costed["problem"] == cs.NO_SAGE, "key"].nunique()
        parts = []
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
