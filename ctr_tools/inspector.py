"""
ctr_tools/inspector.py

The F12 Inspector: a dockable, read-only panel that shows what the app is
doing right now — logs, what is loaded in memory, which files/folders it is
connected to (as paths only), running threads, variables, the ordered
background tasks (with "stop after the current task"), and usage charts.

Everything here only *looks*. It never opens, parses or writes the app's
data files — the Storage tab only stats paths (exists / size / date), and the
Data tab shows tables the app already holds. The one control is the Tasks
tab's Stop button, which asks a job to stop *between* its tasks (see
ctr_tools/tasks.py).

The collect_*() functions take the main window and return plain data, so
they can be exercised without showing any UI.
"""

from __future__ import annotations

import getpass
import logging
import os
import platform
import re
import sys
import tempfile
import threading
import time
import traceback
import weakref
from collections import deque
from html import escape
from pathlib import Path

import pandas as pd
from find_bar import install_find
from PySide6.QtCore import QSettings, QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDockWidget, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextEdit, QToolButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ctr_tools import __version__
from ctr_tools.tasks import DONE, FAILED, QUEUED, RUNNING, SKIPPED, TASKS

_PROCESS_STARTED = time.time()

try:                                    # Qt Charts ships with PySide6, but a
    from PySide6.QtCharts import (      # frozen build must list it explicitly
        QBarCategoryAxis, QBarSeries, QBarSet, QChart, QChartView,
        QLineSeries, QValueAxis,
    )
    _HAVE_CHARTS = True
except ImportError:                     # pragma: no cover
    _HAVE_CHARTS = False

log = logging.getLogger(__name__)

MUTED = "#757575"
_LEVELS = ["All", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
_LEVEL_RANK = {name: i for i, name in enumerate(_LEVELS[1:])}
_LEVEL_COLOR = {"WARNING": "#E65100", "ERROR": "#C62828", "CRITICAL": "#B71C1C",
                "DEBUG": "#757575"}
_RECORD = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(\S+)\s+(.*)$"
)
_TAIL_BYTES = 400_000


# ═════════════════════════════════════════════════════════════════════════════
# Small helpers
# ═════════════════════════════════════════════════════════════════════════════

def fmt_bytes(n: float | None) -> str:
    if n is None:
        return "–"
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def fmt_seconds(s: float | None) -> str:
    if s is None:
        return "–"
    if s < 1:
        return f"{s * 1000:.0f} ms"
    if s < 90:
        return f"{s:.1f} s"
    return f"{int(s // 60)} min {int(s % 60)} s"


def age_text(ts: float) -> str:
    s = max(0, time.time() - ts)
    if s < 90:
        return f"{int(s)} s ago"
    if s < 5400:
        return f"{int(s // 60)} min ago"
    if s < 172800:
        return f"{s / 3600:.0f} h ago"
    return f"{s / 86400:.0f} days ago"


def _short(text: object, n: int = 160) -> str:
    t = str(text).replace("\n", " ⏎ ")
    return t if len(t) <= n else t[: n - 1] + "…"


def process_memory_bytes() -> int | None:
    """Memory the app currently uses (working set). No third-party package:
    Windows via psapi, Linux via /proc; None where neither is available."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class _PMC(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            counters = _PMC()
            counters.cb = ctypes.sizeof(_PMC)
            if psapi.GetProcessMemoryInfo(
                    k32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return int(counters.WorkingSetSize)
            return None
        statm = Path("/proc/self/statm")
        if statm.exists():
            return int(statm.read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return None
    return None


def _stat(path: Path | None) -> tuple[bool, int | None, float | None]:
    """(exists, size for files, modified) — a plain stat, never opens the file."""
    if path is None:
        return False, None, None
    try:
        st = path.stat()
    except OSError:
        return False, None, None
    return True, (None if path.is_dir() else st.st_size), st.st_mtime


def _dir_size(path: Path, limit: int = 50_000) -> int:
    total = seen = 0
    stack = [path]
    while stack and seen < limit:
        try:
            with os.scandir(stack.pop()) as it:
                for entry in it:
                    seen += 1
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return total


def _open_folder(path: Path | None) -> None:
    if path is None:
        return
    target = path if path.is_dir() else path.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


def _log_dir() -> Path | None:
    from app_logging import get_log_dir
    return get_log_dir()


# ═════════════════════════════════════════════════════════════════════════════
# Collectors (no UI) — also used by the charts and by tests
# ═════════════════════════════════════════════════════════════════════════════

_BYTES_CACHE: dict[int, tuple[weakref.ref, int]] = {}


def df_bytes(df: pd.DataFrame) -> int:
    """Deep memory of a dataframe, cached per object (deep=True is not free).
    The cache holds the table weakly: measuring a table must never keep it alive
    after the app has replaced it."""
    key = id(df)
    hit = _BYTES_CACHE.get(key)
    if hit is None or hit[0]() is not df:
        def _forget(ref, key=key):
            current = _BYTES_CACHE.get(key)
            if current is not None and current[0] is ref:
                del _BYTES_CACHE[key]
        hit = _BYTES_CACHE[key] = (weakref.ref(df, _forget), int(df.memory_usage(deep=True).sum()))
    return hit[1]


def _edit_text(owner: object, attr: str) -> str:
    widget = getattr(owner, attr, None)
    try:
        return widget.text().strip() if widget is not None else ""
    except Exception:
        return ""


def collect_data(win) -> list[dict]:
    """What the app holds in memory right now. A source that isn't loaded is
    listed with the path it is connected to (or '(not selected)')."""
    rows: list[dict] = []

    def add(name, df, source, extra_rows=None):
        loaded = df is not None and not getattr(df, "empty", True)
        rows.append({
            "name": name,
            "loaded": loaded,
            "rows": (len(df) if loaded else None) if extra_rows is None else extra_rows,
            "bytes": df_bytes(df) if loaded else None,
            "source": source or "(not selected)",
            "df": df if loaded else None,
        })

    gen = getattr(win, "_ctr_gen", None)
    if gen is not None:
        add("AZN Pricebook", getattr(gen, "_azn_df", None), _edit_text(gen, "_azn_pb_edit"))
        add("SAGE Export", getattr(gen, "_sage_df", None), _edit_text(gen, "_sage_edit"))
        add("USD Pricebook", getattr(gen, "_usd_pb_df", None), _edit_text(gen, "_usd_pb_edit"))
        add("Equipment Names DB", getattr(gen, "_names_db_df", None),
            _edit_text(gen, "_combined_db_edit"))
        for label, attr in (("CTR Request — manpower lines", "_req_manpower_tbl"),
                            ("CTR Request — equipment lines", "_req_equip_tbl"),
                            ("CTR Request — consumable lines", "_req_cons_tbl"),
                            ("CTR Request — transport lines", "_transport_tbl")):
            tbl = getattr(gen, attr, None)
            n = tbl.rowCount() if tbl is not None else 0
            rows.append({"name": label, "loaded": n > 0, "rows": n or None, "bytes": None,
                         "source": _edit_text(gen, "_combined_db_edit") or "(not selected)",
                         "df": None})

    for side, tables_attr, df_attr in (("MR", "_mr_tables", "_mr_df"),
                                       ("CTR", "_ctr_tables", "_ctr_df")):
        tables = getattr(win, tables_attr, None) or []
        total_rows = sum(len(t["data"]) for t in tables)
        total_bytes = sum(df_bytes(t["data"]) for t in tables)
        files = sorted({Path(t["source_file"]).name for t in tables})
        rows.append({
            "name": f"{side} tables ({len(tables)})", "loaded": bool(tables),
            "rows": total_rows or None, "bytes": total_bytes or None,
            "source": ", ".join(files) if files else "(no files added)", "df": None,
        })
        add(f"{side} — ticked tables combined", getattr(win, df_attr, None),
            "built from the ticked tables above")

    add("Compare result (displayed)", getattr(win, "_display_df", None),
        "built by Compare")

    trk = getattr(win, "_ctr_tracker", None)
    if trk is not None:
        batch = getattr(trk, "_batch", None) or []
        rows.append({"name": "CTR Tracker batch", "loaded": bool(batch),
                     "rows": len(batch) or None, "bytes": None,
                     "source": _edit_text(trk, "_tracker_edit") or "(not selected)",
                     "df": None})
    return rows


def collect_storage(win) -> list[dict]:
    """Every file/folder the app is connected to, as a path. Nothing here is
    opened — `loaded` says whether the app itself holds the contents."""
    import ctr_tools.aliases as _aliases
    import ctr_tools.desc_renames as _renames
    import ctr_tools.presets as _presets
    from ctr_tools.paths import _socar_data_root

    out: list[dict] = []

    def add(group, label, path, kind="file", loaded="no"):
        out.append({"group": group, "label": label, "kind": kind, "loaded": loaded,
                    "path": Path(path) if path else None})

    # Logs
    log_dir = _log_dir()
    add("Logs", "Log folder", log_dir, "dir")
    if log_dir is not None and log_dir.is_dir():
        for p in sorted(log_dir.glob("app.log*")) + sorted(log_dir.glob("crash.log*")):
            add("Logs", p.name, p)

    # The app's own saved data (aliases, renames, presets are loaded by the app)
    add("App data", "Data folder", _socar_data_root() / "CTRGenerator", "dir")
    add("App data", "Match aliases", _aliases._ALIASES_PATH, loaded="yes (alias table)")
    add("App data", "Description renames", _renames._DESC_RENAMES_PATH, loaded="yes (rename table)")
    add("App data", "CTR presets", _presets._PRESETS_PATH, loaded="yes (preset list)")
    add("App data", "Saved comparisons folder", _socar_data_root() / "Comparisons", "dir")
    loaded_cmp = getattr(win, "_loaded_comparison_path", None)
    if loaded_cmp:
        add("App data", "Open comparison", loaded_cmp, loaded="yes (MR/CTR results)")
    try:
        add("App data", "Window settings", QSettings("SOCAR", "CTRGenerator").fileName(),
            "registry")
    except Exception:
        pass

    # Files chosen in the CTR Generator
    gen = getattr(win, "_ctr_gen", None)
    if gen is not None:
        def src(label, attr, df_attr=None, kind="file"):
            text = _edit_text(gen, attr)
            held = "no"
            if df_attr is not None:
                df = getattr(gen, df_attr, None)
                held = "yes (Data tab)" if df is not None and not df.empty else "no"
            add("Selected in CTR Generator", label, text or None, kind, held)

        src("AZN Pricebook", "_azn_pb_edit", "_azn_df")
        src("SAGE Export", "_sage_edit", "_sage_df")
        src("USD Pricebook", "_usd_pb_edit", "_usd_pb_df")
        src("Combined DB (names + request)", "_combined_db_edit", "_names_db_df")
        src("AZN Template", "_azn_tpl_edit")
        src("USD Template", "_usd_tpl_edit")
        src("Output folder", "_out_dir_edit", kind="dir")

    # CTR Tracker
    trk = getattr(win, "_ctr_tracker", None)
    if trk is not None:
        tracker = _edit_text(trk, "_tracker_edit")
        add("CTR Tracker", "Tracker workbook", tracker or None)
        if tracker:
            tp = Path(tracker)
            backups = sorted(tp.parent.glob(f"{tp.stem}.backup-*")) if tp.parent.is_dir() else []
            add("CTR Tracker", f"Backups next to it ({len(backups)})", tp.parent, "dir")
        add("CTR Tracker", "CTR file to load", _edit_text(trk, "_ctr_file_edit") or None)
    return out


_SIMPLE = (str, int, float, bool, type(None), Path)


def _describe(value: object) -> tuple[str, str] | None:
    """(type, value) for something worth listing, None to skip it."""
    from PySide6.QtWidgets import QTableView

    if isinstance(value, _SIMPLE):
        return type(value).__name__, _short(value)
    if isinstance(value, pd.DataFrame):
        return "DataFrame", f"{value.shape[0]} rows × {value.shape[1]} cols"
    if isinstance(value, (dict, list, tuple, set)):
        return type(value).__name__, f"{len(value)} items — {_short(value, 100)}"
    if isinstance(value, QLineEdit):
        return "text box", _short(value.text())
    if isinstance(value, QComboBox):
        return "dropdown", _short(value.currentText())
    if isinstance(value, QCheckBox):
        return "checkbox", "checked" if value.isChecked() else "unchecked"
    if isinstance(value, QTableWidget):
        return "table", f"{value.rowCount()} rows × {value.columnCount()} cols"
    if isinstance(value, QTableView):
        model = value.model()
        return "table view", f"{model.rowCount() if model else 0} rows"
    return None


def collect_variables(win) -> list[tuple[str, str, str]]:
    """Flat, read-only (name, type, value) list of the app's state."""
    owners = [("window", win), ("generator", getattr(win, "_ctr_gen", None)),
              ("tracker", getattr(win, "_ctr_tracker", None))]
    rows: list[tuple[str, str, str]] = []
    for owner_name, owner in owners:
        if owner is None:
            continue
        for attr, value in sorted(vars(owner).items()):
            if attr.startswith("__"):
                continue
            try:
                described = _describe(value)
            except Exception:
                described = None
            if described:
                rows.append((f"{owner_name}.{attr}", described[0], described[1]))
    return rows


def parse_log(text: str) -> list[tuple[str | None, str | None, str]]:
    """Splits log text into (level, timestamp, full record text); continuation
    lines (tracebacks) stay attached to the record they belong to."""
    records: list[list] = []
    for line in text.splitlines():
        m = _RECORD.match(line)
        if m:
            records.append([m.group(2), m.group(1), line])
        elif records:
            records[-1][2] += "\n" + line
        else:
            records.append([None, None, line])
    return [(r[0], r[1], r[2]) for r in records]


def read_tail(path: Path, limit: int = _TAIL_BYTES) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > limit:
                fh.seek(size - limit)
                fh.readline()          # drop the partial first line
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def last_error(log_dir: Path | None) -> tuple[str, float, str] | None:
    """(timestamp text, epoch, first line) of the newest ERROR/CRITICAL in app.log."""
    if log_dir is None:
        return None
    for level, ts, text in reversed(parse_log(read_tail(log_dir / "app.log"))):
        if level in ("ERROR", "CRITICAL") and ts:
            try:
                epoch = time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                epoch = time.time()
            first = _RECORD.match(text.splitlines()[0])
            return ts, epoch, (first.group(4) if first else text.splitlines()[0])
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Tabs
# ═════════════════════════════════════════════════════════════════════════════

def _table(headers: list[str], stretch_col: int | None = None) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setAlternatingRowColors(True)
    t.verticalHeader().setVisible(False)
    t.setWordWrap(False)
    t.setTextElideMode(Qt.TextElideMode.ElideMiddle)   # keep both ends of a long path visible
    t.horizontalHeader().setHighlightSections(False)
    if stretch_col is not None:
        t.horizontalHeader().setSectionResizeMode(stretch_col, QHeaderView.ResizeMode.Stretch)
    install_find(t)
    return t


def _item(text: str, align_right: bool = False, tip: str = "") -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    if align_right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    it.setToolTip(tip or text)
    return it


class OverviewTab(QWidget):
    def __init__(self, win, dock):
        super().__init__()
        self._win, self._dock = win, dock
        self._started = time.time()
        self._last_cpu = (time.process_time(), time.time())
        form = QFormLayout()
        self._labels: dict[str, QLabel] = {}
        for key in ("App", "Python", "Process", "Memory", "CPU", "Threads", "Background jobs",
                    "Log folder", "Data folder", "Last error"):
            lbl = QLabel("–")
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lbl.setWordWrap(True)
            self._labels[key] = lbl
            form.addRow(f"{key}:", lbl)
        self._err_btn = QPushButton("Show in Logs")
        self._err_btn.clicked.connect(dock.show_errors)
        lay = QVBoxLayout(self)
        lay.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(self._err_btn)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        note = QLabel("Read-only. This panel never opens or changes the app's data files — Snapshot only writes a text file into the log folder.")
        note.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        note.setWordWrap(True)
        lay.addWidget(note)

    def refresh(self):
        from ctr_tools.paths import _socar_data_root
        L = self._labels
        L["App"].setText(f"CTR Generator v{__version__}"
                         + ("  (installed build)" if getattr(sys, "frozen", False) else "  (from source)"))
        L["Python"].setText(f"{platform.python_version()} on {platform.system()} {platform.release()}")
        L["Process"].setText(f"PID {os.getpid()} — running for {fmt_seconds(time.time() - self._started)}")
        mem = process_memory_bytes()
        L["Memory"].setText(fmt_bytes(mem) if mem is not None else "not available on this system")
        cpu_now, wall_now = time.process_time(), time.time()
        cpu_prev, wall_prev = self._last_cpu
        self._last_cpu = (cpu_now, wall_now)
        if wall_now > wall_prev:
            L["CPU"].setText(f"{(cpu_now - cpu_prev) / (wall_now - wall_prev) * 100:.0f}% of one core")
        frames = sys._current_frames()
        L["Threads"].setText(f"{len(frames)} Python threads")
        jobs = TASKS.jobs()
        active = [j for j in jobs if j.active]
        L["Background jobs"].setText(
            f"{len(active)} running/queued, {len(jobs) - len(active)} finished"
            + (f" — {', '.join(j.label for j in active)}" if active else ""))
        log_dir = _log_dir()
        L["Log folder"].setText(str(log_dir) if log_dir else "(logging not started)")
        L["Data folder"].setText(str(_socar_data_root() / "CTRGenerator"))
        err = last_error(log_dir)
        if err:
            ts, epoch, msg = err
            L["Last error"].setText(f"{ts}  ({age_text(epoch)})\n{msg}")
            L["Last error"].setStyleSheet("color: #C62828;")
        else:
            L["Last error"].setText("none in the current log")
            L["Last error"].setStyleSheet("")
        self._err_btn.setEnabled(bool(err))


class LogsTab(QWidget):
    def __init__(self):
        super().__init__()
        self._key = None
        self._file = QComboBox()
        self._file.currentIndexChanged.connect(lambda _i: self.refresh(force=True))
        self._level = QComboBox()
        self._level.addItems(_LEVELS)
        self._level.currentIndexChanged.connect(lambda _i: self.refresh(force=True))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(lambda _t: self.refresh(force=True))
        self._follow = QCheckBox("Follow")
        self._follow.setChecked(True)
        self._follow.setToolTip("Keep scrolling to the newest line as the log grows. "
                                "Untick to stay where you are while reading.")
        self._open = QPushButton("Open folder")
        self._open.clicked.connect(lambda: _open_folder(self._current_path()))
        self._copy = QPushButton("Copy path")
        self._copy.clicked.connect(self._copy_path)
        self._view = QTextEdit()
        self._view.setReadOnly(True)
        mono = QFont("Consolas" if sys.platform == "win32" else "Monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(9)
        self._view.setFont(mono)
        self._view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self._status = QLabel("")
        self._status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")

        top = QHBoxLayout()
        top.addWidget(QLabel("File:"))
        top.addWidget(self._file, 1)
        top.addWidget(QLabel("Level:"))
        top.addWidget(self._level)
        top2 = QHBoxLayout()
        top2.addWidget(self._search, 1)
        top2.addWidget(self._follow)
        top2.addWidget(self._open)
        top2.addWidget(self._copy)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addLayout(top2)
        lay.addWidget(self._view, 1)
        lay.addWidget(self._status)

    def set_level(self, level: str):
        self._level.setCurrentText(level)

    def _files(self) -> list[Path]:
        d = _log_dir()
        if d is None or not d.is_dir():
            return []
        return sorted(d.glob("app.log*")) + sorted(d.glob("crash.log*"))

    def _current_path(self) -> Path | None:
        data = self._file.currentData()
        return Path(data) if data else _log_dir()

    def _copy_path(self):
        p = self._current_path()
        if p is not None:
            QGuiApplication.clipboard().setText(str(p))

    def refresh(self, force: bool = False):
        files = self._files()
        names = [str(p) for p in files]
        have = [self._file.itemData(i) for i in range(self._file.count())]
        if names != have:
            self._file.blockSignals(True)
            current = self._file.currentData()
            self._file.clear()
            for p in files:
                self._file.addItem(p.name, str(p))
            idx = self._file.findData(current) if current else 0
            self._file.setCurrentIndex(max(idx, 0))
            self._file.blockSignals(False)
            force = True
        path = self._current_path()
        if path is None or not path.is_file():
            self._view.setPlainText("(no log file yet)")
            return
        st = path.stat()
        needle = self._search.text().strip().lower()
        key = (str(path), st.st_size, st.st_mtime, self._level.currentText(), needle)
        if key == self._key and not force:
            return
        self._key = key

        min_rank = _LEVEL_RANK.get(self._level.currentText(), -1)
        records = parse_log(read_tail(path))
        shown: list[str] = []
        for level, _ts, text in records:
            if min_rank >= 0 and (level is None or _LEVEL_RANK[level] < min_rank):
                continue
            if needle and needle not in text.lower():
                continue
            color = _LEVEL_COLOR.get(level or "", "")
            body = escape(text)
            shown.append(f'<span style="color:{color}">{body}</span>' if color else body)
        shown = shown[-2000:]

        bar = self._view.verticalScrollBar()
        keep = bar.value()
        self._view.setHtml('<pre style="margin:0">' + "\n".join(shown) + "</pre>")
        if self._follow.isChecked():
            bar.setValue(bar.maximum())
        else:
            bar.setValue(keep)
        more = " (showing the last part)" if st.st_size > _TAIL_BYTES else ""
        self._status.setText(f"{len(shown)} of {len(records)} records{more} — {fmt_bytes(st.st_size)}")


class DataTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self._win = win
        self._rows: list[dict] = []
        self._tbl = _table(["Name", "State", "Rows", "Memory", "Source"], stretch_col=4)
        self._tbl.itemSelectionChanged.connect(self._show_preview)
        self._summary = QLabel("")
        self._summary.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._preview_label = QLabel("Select a loaded table to preview its first rows (read-only).")
        self._preview_label.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._preview = _table([])
        btn = QPushButton("Refresh")
        btn.clicked.connect(self.refresh)
        top = QHBoxLayout()
        top.addWidget(self._summary, 1)
        top.addWidget(btn)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self._tbl, 3)
        lay.addWidget(self._preview_label)
        lay.addWidget(self._preview, 2)

    def refresh(self):
        keep = self._tbl.currentRow()
        self._rows = collect_data(self._win)
        self._tbl.setRowCount(len(self._rows))
        total = 0
        for r, row in enumerate(self._rows):
            self._tbl.setItem(r, 0, _item(row["name"]))
            state = _item("● loaded" if row["loaded"] else "○ not loaded")
            state.setForeground(Qt.GlobalColor.darkGreen if row["loaded"] else Qt.GlobalColor.gray)
            self._tbl.setItem(r, 1, state)
            self._tbl.setItem(r, 2, _item("" if row["rows"] is None else f"{row['rows']:,}", True))
            self._tbl.setItem(r, 3, _item(fmt_bytes(row["bytes"]) if row["bytes"] else "", True))
            self._tbl.setItem(r, 4, _item(row["source"]))
            total += row["bytes"] or 0
        self._tbl.resizeColumnsToContents()
        self._tbl.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        loaded = sum(1 for r in self._rows if r["loaded"])
        self._summary.setText(f"{loaded} of {len(self._rows)} sources loaded — about {fmt_bytes(total)} of tables")
        if 0 <= keep < len(self._rows):
            self._tbl.selectRow(keep)

    def _show_preview(self):
        r = self._tbl.currentRow()
        df = self._rows[r]["df"] if 0 <= r < len(self._rows) else None
        if df is None:
            self._preview.setRowCount(0)
            self._preview.setColumnCount(0)
            self._preview_label.setText("Select a loaded table to preview its first rows (read-only).")
            return
        head = df.head(20)
        self._preview.setColumnCount(len(head.columns))
        self._preview.setHorizontalHeaderLabels([str(c) for c in head.columns])
        self._preview.setRowCount(len(head))
        for i in range(len(head)):
            for j in range(len(head.columns)):
                v = head.iat[i, j]
                text = _short(v, 80) if isinstance(v, (list, dict)) or not pd.isna(v) else ""
                self._preview.setItem(i, j, _item(text))
        self._preview.resizeColumnsToContents()
        self._preview_label.setText(f"First {len(head)} of {len(df):,} rows — {self._rows[r]['name']}")


class StorageTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self._win = win
        self._tree = QTreeWidget()
        install_find(self._tree)
        self._tree.setHeaderLabels(["Item", "Path", "✓", "Size", "Modified", "In app"])
        self._tree.headerItem().setToolTip(2, "Does the path exist?")
        self._tree.headerItem().setToolTip(5, "Does the app itself hold this file's contents in memory? (hover a row for details)")
        self._tree.setRootIsDecorated(True)
        self._tree.setAlternatingRowColors(True)
        self._tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)   # keeps the start and the file name
        self._tree.header().setStretchLastSection(False)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._tree.header().setMinimumSectionSize(28)
        open_btn = QPushButton("Open folder")
        open_btn.clicked.connect(lambda: _open_folder(self._selected_path()))
        copy_btn = QPushButton("Copy path")
        copy_btn.clicked.connect(self._copy)
        ref_btn = QPushButton("Refresh")
        ref_btn.clicked.connect(self.refresh)
        note = QLabel("Paths only — nothing here is opened or read. "
                      "“Loaded by app” says whether the app itself holds the contents.")
        note.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        note.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(open_btn)
        row.addWidget(copy_btn)
        row.addStretch(1)
        row.addWidget(ref_btn)
        lay = QVBoxLayout(self)
        lay.addWidget(note)
        lay.addWidget(self._tree, 1)
        lay.addLayout(row)

    def _selected_path(self) -> Path | None:
        it = self._tree.currentItem()
        data = it.data(1, Qt.ItemDataRole.UserRole) if it else None
        return Path(data) if data else None

    def _copy(self):
        p = self._selected_path()
        it = self._tree.currentItem()
        text = str(p) if p else (it.text(1) if it else "")
        if text:
            QGuiApplication.clipboard().setText(text)

    def refresh(self):
        entries = collect_storage(self._win)
        self._tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        for e in entries:
            parent = groups.get(e["group"])
            if parent is None:
                parent = QTreeWidgetItem([e["group"]])
                f = parent.font(0)
                f.setBold(True)
                parent.setFont(0, f)
                self._tree.addTopLevelItem(parent)
                groups[e["group"]] = parent
            path = e["path"]
            tip = ""
            if e["kind"] == "registry":
                shown, exists = str(path), "–"
                size = mod = ""
            elif path is None:
                shown, exists, size, mod = "(not selected)", "–", "", ""
            else:
                ok, nbytes, mtime = _stat(path)
                shown = str(path)
                exists = "✓" if ok else "✗"
                tip = "" if ok else "This path does not exist"
                size = fmt_bytes(nbytes) if nbytes is not None else ("folder" if ok else "")
                mod = time.strftime("%m-%d %H:%M", time.localtime(mtime)) if mtime else ""
            loaded = e["loaded"]
            it = QTreeWidgetItem([e["label"], shown, exists, size, mod, loaded.split(" ")[0]])
            it.setToolTip(1, shown)
            it.setToolTip(2, tip)
            it.setToolTip(5, "App holds its contents: " + loaded)
            if path is not None and e["kind"] != "registry":
                it.setData(1, Qt.ItemDataRole.UserRole, str(path))
            if exists == "✗":
                it.setForeground(2, Qt.GlobalColor.red)
            parent.addChild(it)
        self._tree.expandAll()
        for c in (0, 2, 3, 4, 5):
            self._tree.resizeColumnToContents(c)
        self._tree.setColumnWidth(0, min(self._tree.columnWidth(0), 190))


class ThreadsTab(QWidget):
    def __init__(self):
        super().__init__()
        self._stacks: dict[int, str] = {}
        self._tbl = _table(["Thread", "ID", "Role"], stretch_col=0)
        self._tbl.itemSelectionChanged.connect(self._show_stack)
        self._stack = QTextEdit()
        self._stack.setReadOnly(True)
        mono = QFont("Consolas" if sys.platform == "win32" else "Monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(9)
        self._stack.setFont(mono)
        self._stack.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        copy = QPushButton("Copy all stacks")
        copy.clicked.connect(self._copy_all)
        row = QHBoxLayout()
        row.addWidget(QLabel("What every thread is doing right now (oldest call first)."), 1)
        row.addWidget(copy)
        row.addWidget(refresh)
        lay = QVBoxLayout(self)
        lay.addLayout(row)
        lay.addWidget(self._tbl, 1)
        lay.addWidget(self._stack, 2)

    def refresh(self):
        names = {t.ident: t for t in threading.enumerate()}
        main_id = threading.main_thread().ident
        frames = sys._current_frames()
        selected = self._selected_id()
        self._stacks = {ident: "".join(traceback.format_stack(frame))
                        for ident, frame in frames.items()}
        self._tbl.setRowCount(len(frames))
        for r, ident in enumerate(sorted(frames, key=lambda i: (i != main_id, i))):
            t = names.get(ident)
            name = t.name if t else "Qt worker / pool thread"
            role = "UI thread (this panel runs here)" if ident == main_id else (
                "daemon" if t and t.daemon else "background")
            first = _item(name)
            first.setData(Qt.ItemDataRole.UserRole, ident)
            self._tbl.setItem(r, 0, first)
            self._tbl.setItem(r, 1, _item(str(ident)))
            self._tbl.setItem(r, 2, _item(role))
            if ident == selected:
                self._tbl.selectRow(r)
        self._show_stack()

    def _selected_id(self) -> int | None:
        r = self._tbl.currentRow()
        it = self._tbl.item(r, 0) if r >= 0 else None
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _show_stack(self):
        ident = self._selected_id()
        self._stack.setPlainText(self._stacks.get(ident, "Select a thread to see its call stack."))

    def _copy_all(self):
        text = "\n\n".join(f"── thread {i} ──\n{s}" for i, s in self._stacks.items())
        QGuiApplication.clipboard().setText(text)


class VariablesTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self._win = win
        self._all: list[tuple[str, str, str]] = []
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search variable name, type or value…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply)
        self._count = QLabel("")
        self._count.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._tbl = _table(["Variable", "Type", "Value"], stretch_col=2)
        btn = QPushButton("Refresh")
        btn.clicked.connect(self.refresh)
        top = QHBoxLayout()
        top.addWidget(self._search, 1)
        top.addWidget(btn)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self._count)
        lay.addWidget(self._tbl, 1)

    def refresh(self):
        self._all = collect_variables(self._win)
        self._apply()

    def _apply(self):
        needle = self._search.text().strip().lower()
        rows = [r for r in self._all if not needle or any(needle in c.lower() for c in r)]
        self._tbl.setRowCount(len(rows))
        for i, (name, typ, val) in enumerate(rows):
            self._tbl.setItem(i, 0, _item(name))
            self._tbl.setItem(i, 1, _item(typ))
            self._tbl.setItem(i, 2, _item(val, tip=val))
        self._tbl.resizeColumnToContents(0)
        self._count.setText(f"{len(rows)} of {len(self._all)} variables (read-only snapshot)")


class TasksTab(QWidget):
    _COLORS = {RUNNING: "#1976D2", DONE: "#2E7D32", FAILED: "#C62828",
               SKIPPED: "#757575", QUEUED: "#757575"}

    def __init__(self):
        super().__init__()
        self._tree = QTreeWidget()
        install_find(self._tree)
        self._tree.setHeaderLabels(["Task", "Status", "Time", "Detail"])
        self._tree.setRootIsDecorated(True)
        self._tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._stop = QPushButton("Stop after current task")
        self._stop.setToolTip("Lets the task that is running finish, and does not start the "
                              "ones after it. Nothing is ever cut off halfway.")
        self._stop.clicked.connect(self._on_stop)
        clear = QPushButton("Clear finished")
        clear.clicked.connect(lambda: (TASKS.clear_finished(), self.refresh()))
        note = QLabel("Tasks run in the order shown. Stop only takes effect between tasks — "
                      "one that has already started always finishes. Single-task jobs "
                      "(such as a tracker write) can't be stopped, because stopping half way "
                      "is exactly what could damage the file. Jobs whose tasks run at the same "
                      "time (such as loading pricebook files) have already started all of "
                      "them, so Stop has little or no effect on those.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        row = QHBoxLayout()
        row.addWidget(self._stop)
        row.addStretch(1)
        row.addWidget(clear)
        lay = QVBoxLayout(self)
        lay.addLayout(row)
        lay.addWidget(self._tree, 1)
        lay.addWidget(note)

    @staticmethod
    def _job_note(job) -> str:
        if not job.stoppable:
            return "can't be stopped"
        if job.parallel:
            return "runs in parallel — Stop has little effect"
        return ""

    def _on_stop(self):
        TASKS.stop_all()
        self.refresh()

    def refresh(self):
        self._tree.setUpdatesEnabled(False)
        self._tree.clear()
        for job in reversed(TASKS.jobs()):
            top = QTreeWidgetItem([job.label, job.status, fmt_seconds(job.seconds),
                                   self._job_note(job)])
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            self._tree.addTopLevelItem(top)
            for n, step in enumerate(job.steps, start=1):
                child = QTreeWidgetItem([f"{n}. {step.name}", step.status,
                                         fmt_seconds(step.seconds), step.detail])
                child.setForeground(1, self._color(step.status))
                top.addChild(child)
            top.setExpanded(True)
        self._tree.setUpdatesEnabled(True)
        for c in (1, 2):
            self._tree.resizeColumnToContents(c)
        self._stop.setEnabled(TASKS.can_stop_any())

    @classmethod
    def _color(cls, status: str):
        from PySide6.QtGui import QBrush, QColor
        return QBrush(QColor(cls._COLORS.get(status, "#212121")))


_CHART_CHOICES = ["Memory over time", "CPU over time", "Time per task",
                  "Memory per loaded table", "Space per folder"]


class ChartsTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self._win = win
        self._samples: deque[tuple[float, float | None, float]] = deque(maxlen=900)
        self._t0 = time.time()
        self._last_cpu = (time.process_time(), time.time())
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._sample)

        self._choice = QComboBox()
        self._choice.addItems(_CHART_CHOICES)
        self._choice.currentIndexChanged.connect(lambda _i: self.refresh())
        self._record = QCheckBox("Record usage (every second)")
        self._record.setToolTip("Memory and CPU are only sampled while this is ticked, "
                                "so there is no cost when it is off.")
        self._record.toggled.connect(self._on_record)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        self._note = QLabel("")
        self._note.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self._note.setWordWrap(True)

        top = QHBoxLayout()
        top.addWidget(self._choice, 1)
        top.addWidget(self._record)
        top.addWidget(refresh)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        if _HAVE_CHARTS:
            self._view = QChartView()
            self._view.setMinimumHeight(220)
            self._chart = None
            lay.addWidget(self._view, 1)
        else:
            lay.addWidget(QLabel("Charts are unavailable: PySide6.QtCharts is missing from this build."), 1)
        lay.addWidget(self._note)

    def _on_record(self, on: bool):
        if on:
            self._samples.clear()
            self._t0 = time.time()
            self._last_cpu = (time.process_time(), time.time())
            self._timer.start()
            self._sample()
        else:
            self._timer.stop()

    def _sample(self):
        cpu_now, wall_now = time.process_time(), time.time()
        cpu_prev, wall_prev = self._last_cpu
        self._last_cpu = (cpu_now, wall_now)
        cpu = (cpu_now - cpu_prev) / (wall_now - wall_prev) * 100 if wall_now > wall_prev else 0.0
        mem = process_memory_bytes()
        self._samples.append((wall_now - self._t0, None if mem is None else mem / 1048576, cpu))
        if self._choice.currentIndex() in (0, 1):
            self.refresh()

    # ── drawing ──────────────────────────────────────────────────────────────

    def _new_chart(self, title: str) -> QChart:
        """A fresh chart per draw — reusing one leaves the previous chart's
        axes behind, overlapping the new ones."""
        chart = QChart()
        chart.setAnimationOptions(QChart.AnimationOption.NoAnimation)
        chart.legend().hide()
        chart.setTitle(title)
        self._chart = chart             # keep a reference; the view doesn't own it
        self._view.setChart(chart)
        return chart

    def _line(self, title: str, ylabel: str, values: list[tuple[float, float]], zero_based: bool = True):
        chart = self._new_chart(title)
        series = QLineSeries()
        for x, y in values:
            series.append(x, y)
        chart.addSeries(series)
        ax, ay = QValueAxis(), QValueAxis()
        ax.setTitleText("seconds since recording started")
        ay.setTitleText(ylabel)
        xs = [v[0] for v in values] or [0, 1]
        ys = [v[1] for v in values] or [0, 1]
        ax.setRange(max(0, min(xs)), max(xs) + 1)
        if zero_based:
            ay.setRange(0, max(ys) * 1.15 + 1)
        else:   # a tight range, so a slow memory climb is visible
            lo, hi = min(ys), max(ys)
            pad = max((hi - lo) * 0.2, 2.0)
            ay.setRange(max(0, lo - pad), hi + pad)
        ay.setLabelFormat("%.0f")
        ax.setLabelFormat("%.0f")
        chart.addAxis(ax, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(ay, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(ax)
        series.attachAxis(ay)

    def _bars(self, title: str, ylabel: str, items: list[tuple[str, float]]):
        chart = self._new_chart(title)
        bar_set = QBarSet(ylabel)
        for _name, v in items:
            bar_set.append(round(v, 2))
        series = QBarSeries()
        series.append(bar_set)
        series.setLabelsVisible(True)
        series.setLabelsFormat("@value")
        chart.addSeries(series)
        ax = QBarCategoryAxis()
        ax.append([_short(n, 24) for n, _v in items] or [""])
        ax.setLabelsAngle(-40)
        ay = QValueAxis()
        ay.setTitleText(ylabel)
        ay.setRange(0, max([v for _n, v in items] or [1]) * 1.2 + 0.01)
        ay.setLabelFormat("%.1f")
        chart.addAxis(ax, Qt.AlignmentFlag.AlignBottom)
        chart.addAxis(ay, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(ax)
        series.attachAxis(ay)

    def refresh(self):
        if not _HAVE_CHARTS:
            return
        choice = self._choice.currentIndex()
        note = ""
        if choice == 0:
            pts = [(t, m) for t, m, _c in self._samples if m is not None]
            self._line("Memory used by the app", "MB", pts, zero_based=False)
            note = ("Tick “Record usage” to start sampling." if not self._record.isChecked() and not pts
                    else ("Memory is not available on this system." if not pts else ""))
        elif choice == 1:
            self._line("CPU used by the app", "% of one core", [(t, c) for t, _m, c in self._samples])
            note = "Tick “Record usage” to start sampling." if not self._record.isChecked() and not self._samples else ""
        elif choice == 2:
            steps = [(s.name, s.seconds)
                     for j in TASKS.jobs() for s in j.steps if s.seconds is not None]
            steps = steps[-12:]
            self._bars("Time per task (most recent)", "seconds", steps)
            note = "" if steps else "No tasks have run yet."
        elif choice == 3:
            items = [(r["name"], r["bytes"] / 1048576) for r in collect_data(self._win) if r["bytes"]]
            self._bars("Memory per loaded table", "MB", items)
            note = "" if items else "No tables are loaded."
        else:
            items = []
            seen: set[str] = set()
            for e in collect_storage(self._win):
                if e["kind"] == "dir" and e["path"] is not None and e["path"].is_dir():
                    key = str(e["path"])
                    if key not in seen:
                        seen.add(key)
                        items.append((e["label"], _dir_size(e["path"]) / 1048576))
            self._bars("Space used per folder", "MB", items)
            note = "Folder sizes are counted from the file system (capped at 50,000 files each)."
        self._note.setText(note)


# ═════════════════════════════════════════════════════════════════════════════
# The dock
# ═════════════════════════════════════════════════════════════════════════════

# ═════════════════════════════════════════════════════════════════════════════
# Snapshot — everything the tabs show, as one text file
# ═════════════════════════════════════════════════════════════════════════════

_SNAPSHOTS_KEPT = 30
# Variables whose names look like money are written as <hidden>.
_MONEY_NAME = re.compile(r"value|rate|price|cost|total|markup|contingency|amount", re.I)


def redact(text: str) -> str:
    """Replaces the user name in home/profile paths, in every spelling the
    logs use (C:\\Users\\Name, C:/Users/Name, /home/name, /Users/name)."""
    text = re.sub(r"(?i)([a-z]:[\\/]+users[\\/]+)[^\\/\"'\r\n]+", r"\1<user>", text)   # profile names may contain spaces
    text = re.sub(r"(/home/|/Users/)[^/\s\"']+", r"\1<user>", text)
    return text


def _text_table(headers: list[str], rows: list[list[str]], max_width: int = 46) -> list[str]:
    """Plain aligned columns; every column but the last is capped in width."""
    if not rows:
        return ["(none)"]
    cells = [[str(c).replace("\n", " ⏎ ") for c in r] for r in rows]
    widths = [min(max_width, max(len(h), *(len(r[i]) for r in cells)))
              for i, h in enumerate(headers[:-1])]
    def fmt(r):
        left = "  ".join((r[i] if len(r[i]) <= widths[i] else r[i][: widths[i] - 1] + "…").ljust(widths[i])
                         for i in range(len(widths)))
        return (left + "  " + r[-1]).rstrip()
    return [fmt(headers), fmt(["-" * len(h) for h in headers])] + [fmt(r) for r in cells]


def _section(title: str, build) -> list[str]:
    """One section of the snapshot; a failing section can't lose the others."""
    try:
        body = build()
    except Exception as exc:                       # noqa: BLE001 — report, don't crash
        body = [f"[this section could not be written: {type(exc).__name__}: {exc}]"]
    return ["", "=" * 78, title, "=" * 78, *body]


def build_snapshot(win, dock=None) -> str:
    """The text of a snapshot: every Inspector tab's content at this moment."""
    now = time.time()
    lines = [
        "SOCAR Cape automation — Inspector snapshot",
        f"Taken: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}",
        "Personal info: the user name in paths is replaced with <user>, and variables named like",
        "value/rate/price/cost/total/markup/contingency/amount are shown as <hidden>.",
        "Table contents are not included.",
    ]

    def overview():
        from ctr_tools.paths import _socar_data_root
        mem = process_memory_bytes()
        err = last_error(_log_dir())
        up = now - _PROCESS_STARTED
        cpu = f"{time.process_time() / up * 100:.0f}% of one core on average" if up > 0 else "–"
        rows = [
            ["App", f"CTR Generator v{__version__} "
                    f"({'installed build' if getattr(sys, 'frozen', False) else 'from source'})"],
            ["Python", f"{platform.python_version()} on {platform.system()} {platform.release()}"],
            ["Process", f"PID {os.getpid()}, running for {fmt_seconds(up)}"],
            ["Memory", fmt_bytes(mem) if mem is not None else "not available on this system"],
            ["CPU", cpu],
            ["Python threads", str(len(sys._current_frames()))],
            ["Log folder", str(_log_dir() or "(logging not started)")],
            ["Data folder", str(_socar_data_root() / "CTRGenerator")],
            ["Last error", f"{err[0]} ({age_text(err[1])}) — {err[2]}" if err else "none in the current log"],
        ]
        return _text_table(["Item", "Value"], rows)

    def logs():
        out: list[str] = []
        d = _log_dir()
        if d is None:
            return ["(logging not started)"]
        recs = parse_log(read_tail(d / "app.log"))[-300:]
        out.append(f"app.log — last {len(recs)} records:")
        out += [text for _lvl, _ts, text in recs] or ["(empty)"]
        crash = d / "crash.log"
        if crash.is_file() and crash.stat().st_size:
            out += ["", f"crash.log ({fmt_bytes(crash.stat().st_size)}, tail):", read_tail(crash, 100_000)]
        else:
            out += ["", "crash.log: empty"]
        return out

    def data():
        rows = collect_data(win)
        out = _text_table(["Name", "State", "Rows", "Memory", "Source"], [
            [r["name"], "loaded" if r["loaded"] else "not loaded",
             "" if r["rows"] is None else f"{r['rows']:,}",
             fmt_bytes(r["bytes"]) if r["bytes"] else "", r["source"]] for r in rows])
        for r in rows:
            if r["df"] is not None:
                out.append(f"  columns of {r['name']}: " + ", ".join(map(str, r["df"].columns)))
        return out

    def storage():
        rows = []
        for e in collect_storage(win):
            ok, size, mtime = _stat(e["path"]) if e["path"] and e["kind"] != "registry" else (False, None, None)
            exists = "–" if e["path"] is None or e["kind"] == "registry" else ("yes" if ok else "MISSING")
            rows.append([e["group"], e["label"], exists,
                         fmt_bytes(size) if size is not None else ("folder" if ok else ""),
                         time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)) if mtime else "",
                         e["loaded"], str(e["path"] or "(not selected)")])
        return _text_table(["Group", "Item", "Exists", "Size", "Modified", "App holds contents", "Path"], rows)

    def threads():
        names = {t.ident: t for t in threading.enumerate()}
        main_id = threading.main_thread().ident
        frames = sys._current_frames()
        out: list[str] = []
        for ident in sorted(frames, key=lambda i: (i != main_id, i)):
            t = names.get(ident)
            role = "UI thread" if ident == main_id else ("daemon" if t and t.daemon else "background")
            out.append(f"--- thread {ident} — {t.name if t else 'Qt worker / pool thread'} ({role}) ---")
            out.append("".join(traceback.format_stack(frames[ident])).rstrip())
            out.append("")
        return out

    def variables():
        rows = []
        for name, typ, val in collect_variables(win):
            rows.append([name, typ, "<hidden>" if _MONEY_NAME.search(name) else val])
        return _text_table(["Variable", "Type", "Value"], rows)

    def tasks():
        out: list[str] = []
        jobs = TASKS.jobs()
        for job in jobs:
            out.append(f"{job.label} — {job.status}, {fmt_seconds(job.seconds)}"
                       + (f" ({TasksTab._job_note(job)})" if TasksTab._job_note(job) else ""))
            for n, st in enumerate(job.steps, start=1):
                out.append(f"    {n}. {st.name} — {st.status}, {fmt_seconds(st.seconds)}"
                           + (f" ({st.detail})" if st.detail else ""))
        return out or ["(no background jobs yet)"]

    def charts():
        out: list[str] = []
        samples = list(getattr(getattr(dock, "charts", None), "_samples", []) or [])
        if samples:
            out.append(f"Recorded usage ({len(samples)} samples, seconds since recording started):")
            out += [f"    t={t:6.0f}s  memory={'n/a' if m is None else f'{m:7.1f} MB'}  cpu={c:5.0f}%"
                    for t, m, c in samples]
        else:
            out.append("Recorded usage: none (\"Record usage\" was not on)")
        out += ["", "Memory per loaded table:"]
        items = [(r["name"], r["bytes"]) for r in collect_data(win) if r["bytes"]]
        out += [f"    {n}: {fmt_bytes(b)}" for n, b in items] or ["    (no tables loaded)"]
        out += ["", "Space used per folder:"]
        seen: set[str] = set()
        for e in collect_storage(win):
            if e["kind"] == "dir" and e["path"] is not None and e["path"].is_dir() and str(e["path"]) not in seen:
                seen.add(str(e["path"]))
                out.append(f"    {e['label']}: {fmt_bytes(_dir_size(e['path']))}  ({e['path']})")
        return out

    for title, fn in (("OVERVIEW", overview), ("LOGS", logs), ("DATA IN MEMORY", data),
                      ("STORAGE (paths only)", storage), ("THREADS", threads),
                      ("VARIABLES", variables), ("TASKS", tasks), ("CHARTS (data)", charts)):
        lines += _section(title, fn)
    return redact("\n".join(lines)) + "\n"


def snapshot_dir() -> Path:
    base = _log_dir() or (Path(tempfile.gettempdir()) / "socar_cape_automation_logs")
    return base / "snapshots"


def write_snapshot(win, dock=None) -> Path:
    """Writes a snapshot into <log folder>/snapshots and prunes the old ones."""
    folder = snapshot_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"snapshot_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    path.write_text(build_snapshot(win, dock), encoding="utf-8")
    for old in sorted(folder.glob("snapshot_*.txt"))[:-_SNAPSHOTS_KEPT]:
        try:
            old.unlink()
        except OSError:
            pass
    return path


class _FloatingWindow(QWidget):
    """The floating inspector: an ordinary top-level window with the operating
    system's own title bar and borders (drag to move, drag any edge or corner
    to resize, minimise/maximise). Qt's built-in floating dock draws its own
    thin title bar and hairline borders, which are hard to grab."""

    def __init__(self, parent, on_visible):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Inspector — F12")
        self.setMinimumSize(520, 360)
        self.resize(820, 720)
        self._on_visible = on_visible
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

    def showEvent(self, event):
        super().showEvent(event)
        self._on_visible(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self._on_visible(False)


class InspectorDock(QDockWidget):
    """Read-only inspector. Docked on any side of the window by default; the
    Float button moves it into its own resizable window, and Dock moves it
    back."""

    _TABS_REFRESHED_AUTOMATICALLY = ("Overview", "Logs", "Tasks")

    def __init__(self, win):
        super().__init__("Inspector — F12", win)
        self.setObjectName("InspectorDock")
        self._main = win
        self.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        # Qt's own float is off: floating is the separate window below.
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.setMinimumWidth(460)

        self._tabs = QTabWidget()
        self.overview = OverviewTab(win, self)
        self.logs = LogsTab()
        self.data = DataTab(win)
        self.storage = StorageTab(win)
        self.threads = ThreadsTab()
        self.variables = VariablesTab(win)
        self.tasks = TasksTab()
        self.charts = ChartsTab(win)
        for tab, name in ((self.overview, "Overview"), (self.logs, "Logs"), (self.data, "Data"),
                          (self.storage, "Storage"), (self.threads, "Threads"),
                          (self.variables, "Variables"), (self.tasks, "Tasks"),
                          (self.charts, "Charts")):
            self._tabs.addTab(tab, name)

        self._float_btn = QToolButton()
        self._float_btn.setText("Float")
        self._float_btn.setToolTip("Move the inspector into its own window (easy to move and resize), "
                                   "or dock it back into the main window")
        self._float_btn.clicked.connect(lambda: self.set_floating_mode(not self._floating))
        self._snapshot_btn = QToolButton()
        self._snapshot_btn.setText("Snapshot")
        self._snapshot_btn.setToolTip("Save everything these tabs show, as of now, into one text file")
        self._snapshot_btn.clicked.connect(lambda: self.snapshot())
        # A slim button row above the tabs (in a tab-bar corner the buttons
        # would squeeze the 8 tab labels).
        self._content = QWidget()
        content_lay = QVBoxLayout(self._content)
        content_lay.setContentsMargins(0, 0, 0, 0)
        content_lay.setSpacing(2)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(4, 2, 4, 0)
        button_row.addStretch(1)
        button_row.addWidget(self._snapshot_btn)
        button_row.addWidget(self._float_btn)
        content_lay.addLayout(button_row)
        content_lay.addWidget(self._tabs, 1)
        self.setWidget(self._content)

        self._floating = False
        self._placeholder = None
        self._switching = False     # True while this class itself moves between dock and window
        # The floating window only exists while floating: Dock destroys it
        # (rather than hiding it) so nothing of it can linger on screen.
        self._window: _FloatingWindow | None = None
        self._window_geometry = None
        # The dock itself must never float (floating is the separate window
        # above) — a layout saved by an older build, or restored by Qt, could
        # otherwise bring back a natively floating dock next to it.
        self.topLevelChanged.connect(self._keep_docked)

        self._tabs.currentChanged.connect(lambda _i: self.refresh_current())
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.visibilityChanged.connect(self._on_visibility)

    def _keep_docked(self, floating: bool) -> None:
        if floating:
            QTimer.singleShot(0, lambda: self.setFloating(False))

    # ── dock <-> own window ──────────────────────────────────────────────────

    @property
    def floating_mode(self) -> bool:
        return self._floating

    def _window_open(self) -> bool:
        return self._window is not None and self._window.isVisible()

    def is_open(self) -> bool:
        return self._window_open() if self._floating else self.isVisible()

    def _make_window(self) -> _FloatingWindow:
        window = _FloatingWindow(self.parent(), self._on_visibility)
        if self._window_geometry:
            window.restoreGeometry(self._window_geometry)
        return window

    def _destroy_window(self) -> None:
        window, self._window = self._window, None
        if window is None:
            return
        self._window_geometry = window.saveGeometry()
        window.hide()
        window.setParent(None)
        window.deleteLater()

    def set_floating_mode(self, on: bool, show: bool = True) -> None:
        if on == self._floating:
            # Already in this mode — just make sure the other view is gone.
            if on:
                self.hide()
            else:
                self._destroy_window()
            return
        log.info("Inspector: %s (dock visible=%s, window visible=%s)",
                 "dock -> own window" if on else "own window -> dock",
                 self.isVisible(), self._window_open())
        self._switching = True
        try:
            self._move_to(on, show)
        finally:
            self._switching = False

    def _move_to(self, on: bool, show: bool) -> None:
        # QDockWidget keeps pointing at its content widget; swap in a
        # placeholder first so the tabs can be moved out cleanly.
        if on:
            self._placeholder = QWidget()
            self.setWidget(self._placeholder)
            self._window = self._make_window()
            self._window.layout().addWidget(self._content)
            self._content.show()
            self.hide()
        else:
            self._window.layout().removeWidget(self._content)
            self.setWidget(self._content)         # the panel returns to the dock...
            placeholder, self._placeholder = self._placeholder, None
            if placeholder is not None:
                placeholder.deleteLater()         # ...and the stand-in does not pile up
            self._destroy_window()
            if self.isFloating():
                self.setFloating(False)
        self._floating = on
        self._float_btn.setText("Dock" if on else "Float")
        if show:
            (self._window if on else self).show()

    def toggle(self) -> None:
        target = self._window if self._floating else self
        target.setVisible(not target.isVisible())
        if target.isVisible():
            target.raise_()
            target.activateWindow()

    def save(self, settings) -> None:
        geometry = self._window.saveGeometry() if self._window is not None else self._window_geometry
        settings.setValue("inspector/floating", self._floating)
        if geometry:
            settings.setValue("inspector/window_geometry", geometry)

    def restore(self, settings) -> None:
        self._window_geometry = settings.value("inspector/window_geometry")
        if str(settings.value("inspector/floating", "false")).lower() == "true":
            self.set_floating_mode(True, show=False)

    # ── content ──────────────────────────────────────────────────────────────

    def snapshot(self, notify: bool = True) -> Path:
        """Saves a snapshot of every tab to a text file and tells the user where."""
        path = write_snapshot(self._main, self)
        log.info("Inspector: snapshot saved to %s", path)
        if notify:
            self._notify_snapshot(path)
        return path

    def _notify_snapshot(self, path: Path) -> None:
        parent = self._window if self._floating and self._window is not None else self._main
        box = QMessageBox(parent)
        box.setWindowTitle("Snapshot saved")
        box.setText("The snapshot was saved to:")
        box.setInformativeText(str(path))
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_btn = box.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
        copy_btn = box.addButton("Copy path", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Ok)
        box.exec()
        if box.clickedButton() is open_btn:
            _open_folder(path)
        elif box.clickedButton() is copy_btn:
            QGuiApplication.clipboard().setText(str(path))

    def show_errors(self):
        self._tabs.setCurrentWidget(self.logs)
        self.logs.set_level("ERROR")

    def _on_visibility(self, visible: bool):
        # Fired by the dock *and* by the floating window.
        # Only one view may exist: whichever one is opened last — by the
        # Float/Dock button, F12, the main window's panel menu, anything —
        # wins, and the other one is closed.
        if visible and not self._switching:
            if self._floating and self.isVisible():
                log.info("Inspector: dock opened while floating -> closing the window")
                QTimer.singleShot(0, lambda: self.set_floating_mode(False))
            elif not self._floating and self._window_open():
                log.info("Inspector: window opened while docked -> closing the dock")
                QTimer.singleShot(0, lambda: self.set_floating_mode(True))
        # Only the view that is in use drives the refresh timer.
        if not visible and self.is_open():
            return
        if visible:
            self.refresh_current()
            self._timer.start()
        else:
            self._timer.stop()

    def _tick(self):
        if self._tabs.tabText(self._tabs.currentIndex()) in self._TABS_REFRESHED_AUTOMATICALLY:
            self.refresh_current()

    def refresh_current(self):
        tab = self._tabs.currentWidget()
        if hasattr(tab, "refresh"):
            tab.refresh()
