"""Shared set-up for the offscreen tests (plain Python — no pytest needed).

Safety (see CLAUDE.md, "Testing Safety"): everything the app persists — the data
folder, QSettings, the aliases / renames / presets files — is redirected into a
fresh temporary folder BEFORE the app is imported, and generated CTRs go to a
temporary output folder. The files under TEST_Files/CTR creator are only read.
The sandbox path is printed when the harness is imported and removed on exit.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = Path(tempfile.mkdtemp(prefix="ctr_tests_"))
for _env, _dir in (("XDG_CONFIG_HOME", "cfg"), ("XDG_DATA_HOME", "data"),
                   ("XDG_CACHE_HOME", "cache"), ("HOME", "home")):
    (SANDBOX / _dir).mkdir()
    os.environ[_env] = str(SANDBOX / _dir)
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(ROOT))
atexit.register(shutil.rmtree, SANDBOX, ignore_errors=True)
print(f"[tests] sandbox (all writes go here): {SANDBOX}", file=sys.stderr)

import ctr_tools.aliases as _A          # noqa: E402
import ctr_tools.desc_renames as _D     # noqa: E402
import ctr_tools.presets as _P          # noqa: E402

_A._ALIASES_PATH = SANDBOX / "match_aliases.json"
_A._BUNDLED_DEFAULT_PATH = SANDBOX / "no_such_default.json"
_D._DESC_RENAMES_PATH = SANDBOX / "desc_renames.json"
_P._PRESETS_PATH = SANDBOX / "ctr_presets.json"
_P._BUNDLED_DEFAULT_PATH = SANDBOX / "no_such_default.json"

import ctr_tools.stockcode_types as _ST   # noqa: E402

_ST._TYPES_PATH = SANDBOX / "stockcode_types.json"                  # the user's list: a fixture per test
_ST._BUNDLED_DEFAULT_PATH = SANDBOX / "no_such_shipped_types.json"  # never the real shipped (private) list

from PySide6.QtWidgets import QApplication, QMessageBox   # noqa: E402

app = QApplication.instance() or QApplication([])
from ctr_tools import window as W   # noqa: E402

INPUTS = ROOT / "TEST_Files" / "CTR creator"
msgs: list[tuple] = []              # every dialog the code under test raised: (kind, title, text)


class Skip(Exception):
    """Raise from a test that cannot run here (the runner reports it as SKIP)."""


def _record(kind):
    def fn(parent, title, text, *a, **k):
        msgs.append((kind, title, text))
        return QMessageBox.StandardButton.Yes
    return staticmethod(fn)


for _kind in ("warning", "critical", "information", "question"):
    setattr(QMessageBox, _kind, _record(_kind))
# The red "missing values" box: record it and press its first button ("Generate without them").
QMessageBox.exec = lambda self: (msgs.append(("BOX", "", self.text())), 0)[1]
QMessageBox.clickedButton = lambda self: self.buttons()[0]


def pump(n: int = 10) -> None:
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def wait(cond, secs: float = 120.0) -> bool:
    t0 = time.time()
    while not cond() and time.time() - t0 < secs:
        app.processEvents()
        time.sleep(0.03)
    return cond()


def _new_generator() -> "W.CTRGeneratorWidget":
    w = W.CTRGeneratorWidget()
    w._azn_pb_edit.setText(str(INPUTS / "4410030127 - 12.02.2026.xlsx"))
    w._usd_pb_edit.setText(str(INPUTS / "4410030190 - 12.02.2026.xlsx"))
    w._sage_edit.setText(str(INPUTS / "FROM SAGE 10.06.26.xlsm"))
    w._combined_db_edit.setText(str(INPUTS / "CTR_NAMES_DB & CTR_REQUEST 263.xlsx"))
    return w


_GEN: "W.CTRGeneratorWidget | None" = None


def load_request(w) -> None:
    """(Re)load the test CTR Request into `w` — clears the tables first."""
    w._pdms_check.setChecked(False)         # a test that ticked "PDMS proposal" must not leak into the next
    w._hotbed_days_edit.setText("")
    w._hotbed_rate_edit.setText(f"{float(W.pdms_defaults()['hotbed_rate']):g}")
    w._hotbed_incl_chk.setChecked(True)
    w._hotbed_cur_combo.setCurrentText(W.pdms_defaults()["category_currencies"]["manpower"])
    for key, combo in w._cat_combos.items():
        combo.setCurrentText(W.pdms_defaults()["category_currencies"][key])
    for t in (w._req_manpower_tbl, w._req_equip_tbl, w._req_cons_tbl, w._transport_tbl):
        t.setRowCount(0)
    w._stats_panel._manual.clear()
    w._stats_panel.set_choices({})          # no remembered source choices carried over between tests
    w._set_jump_target(None)
    w._load_ctr_request()
    assert wait(lambda: w._req_manpower_tbl.rowCount() > 0), "CTR Request did not load"
    pump(20)
    w._refresh_stats()                  # the statistics box refreshes on a short timer; do it now
    pump(5)


def generator() -> "W.CTRGeneratorWidget":
    """The Generator with the pricebooks and SAGE loaded (once per run) and the test
    request freshly loaded. Do not keep the object across tests: each call resets it."""
    global _GEN
    if _GEN is None:
        _GEN = _new_generator()
        _GEN._load_files()
        assert wait(lambda: _GEN._pending_loads == 0), "reference files did not load"
        assert not _GEN._load_errors, _GEN._load_errors
        _GEN.resize(1300, 800)
        _GEN.show()                      # a hidden widget has no scroll range / geometry to test against
        pump(20)
    load_request(_GEN)
    return _GEN


def generator_without_files() -> "W.CTRGeneratorWidget":
    """A Generator that has loaded only the request (Load Files never pressed)."""
    w = _new_generator()
    w._load_ctr_request()
    assert wait(lambda: w._req_manpower_tbl.rowCount() > 0)
    pump(20)
    return w


def resolve_disparities(w) -> None:
    """Choose 'the document is right' for every rechargeability disparity, so Generate is not
    held back by it (a test about the blocking itself does not call this)."""
    panel = w._stats_panel
    for key in sorted(set(panel.disparity_table()["Stock Code"])):
        panel.set_choice(key, "DOC")


def generate(w, name: str, resolve: bool = True) -> tuple[Path, list[tuple]]:
    """Press Generate into a fresh temp folder (the red box is answered 'Generate
    without them', other warnings 'Yes'). Disparities are resolved first unless
    `resolve=False`. Returns (output folder, dialogs raised)."""
    if resolve:
        resolve_disparities(w)
    out = SANDBOX / "out" / name
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    w._azn_tpl_edit.setText(str(INPUTS / "AZN_TEMPLATE.xlsx"))
    w._usd_tpl_edit.setText(str(INPUTS / "USD_TEMPLATE.xlsx"))
    w._out_dir_edit.setText(str(out))
    w._job_ref_edit.setText("999")
    msgs.clear()
    w._generate()
    assert wait(lambda: any(m[0] in ("information", "critical") for m in msgs), 120), "Generate did not finish"
    assert not any(m[0] == "critical" for m in msgs), [m for m in msgs if m[0] == "critical"]
    return out, list(msgs)


def output_file(out: Path, kind: str) -> Path:
    """The generated workbook: kind 'AZN' or 'USD'."""
    return next(p for p in out.glob("*.xlsx") if f"_{kind}_" in p.name)


def all_text(msgs_) -> str:
    return "\n".join(str(m[-1]) for m in msgs_)
