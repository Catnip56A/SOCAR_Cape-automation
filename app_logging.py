"""
app_logging.py

Centralized logging setup for the SOCAR Cape desktop app. Call
setup_logging() once, as early as possible in the entry point — before
QApplication is created — so startup issues are captured too.

Why this exists: when a PySide6 app "just closes" with no dialog and no
console traceback, it's almost always one of three things, and each needs a
different capture mechanism:

  1. An uncaught Python exception inside a Qt slot (a button's .connect()
     target, a signal handler). PySide6 reports these through
     sys.excepthook, but if nothing is installed there the traceback is
     easy to miss or just isn't kept anywhere — installing our own hook
     logs it to a file before the process goes down.
  2. A Qt-level fatal error (qFatal), e.g. from a bad widget/paint call.
     These go through Qt's own message system, not Python exceptions, so
     they need qInstallMessageHandler to be captured at all.
  3. A genuine native crash (segfault, abort signal) in a C extension —
     openpyxl/pandas/Qt itself. Python's own exception machinery never
     runs for these. faulthandler is the standard tool: it installs signal
     handlers that dump a C-level stack trace to a file when the process
     is about to die this way.

Together, whichever of the three happens, there will be a line in
logs/app.log or logs/crash.log showing what was happening right before.
"""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import sys
import tempfile
import traceback
from pathlib import Path

from PySide6.QtCore import QtMsgType, qInstallMessageHandler

_QT_LEVELS = {
    QtMsgType.QtDebugMsg:    logging.DEBUG,
    QtMsgType.QtInfoMsg:     logging.INFO,
    QtMsgType.QtWarningMsg:  logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg:    logging.CRITICAL,
}

_crash_file = None  # kept open for the lifetime of the process — faulthandler needs a live fd


def _resolve_log_dir() -> Path:
    """
    logs/ next to the running program, falling back to the system temp dir
    if that's not writable (e.g. installed under a read-only Program Files
    folder).

    When frozen by PyInstaller into a single-file .exe, __file__ resolves
    to the transient extraction folder under sys._MEIPASS — which Windows
    wipes the moment the process exits, taking any logs written there with
    it. sys.executable instead points at the actual .exe on disk, so use
    that as the anchor whenever running frozen.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent
    candidate = base / "logs"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_test"
        probe.touch()
        probe.unlink()
        return candidate
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "socar_cape_automation_logs"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _qt_message_handler(msg_type, context, message):
    logger = logging.getLogger("qt")
    level = _QT_LEVELS.get(msg_type, logging.WARNING)
    where = f"{context.file}:{context.line}" if context.file else ""
    logger.log(level, "%s  %s", message, where)


def _excepthook(exc_type, exc_value, exc_tb):
    logging.getLogger("uncaught").critical(
        "Unhandled exception — app may be about to close:\n%s",
        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
    )
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def setup_logging(level: int = logging.INFO) -> Path:
    """Configure rotating file + console logging, plus crash capture. Returns the log dir."""
    global _crash_file

    log_dir = _resolve_log_dir()

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "app.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)

    sys.excepthook = _excepthook
    qInstallMessageHandler(_qt_message_handler)

    _crash_file = open(log_dir / "crash.log", "a", encoding="utf-8")
    faulthandler.enable(file=_crash_file)

    logging.getLogger(__name__).info("Logging initialized — log directory: %s", log_dir)
    return log_dir
