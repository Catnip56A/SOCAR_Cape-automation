"""template_config.json: the app reads the user's own copy in the data folder (created from the shipped file on first use),
so an edit applies to the installed program too; what differs, Merge shipped, Reset, a damaged copy, and the Settings row.
Everything runs on fixtures in the sandbox; the real shipped file is only read."""
import json

from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

import harness as H
import ctr_tools.config as C

SHIPPED = {"pdms": {"hotbed_rate": 59.0, "customer_name": "Shipped Customer"}, "transport_rates": {"_default": [{"rate_azn": 1}]},
           "scaffold": {"label": "Conventional Scaffold"}}


def _setup(tag):
    d = H.SANDBOX / f"cfg_{tag}"
    d.mkdir(exist_ok=True)
    ship, mine = d / "shipped.json", d / "user" / "template_config.json"
    ship.write_text(json.dumps(SHIPPED, indent=2), encoding="utf-8")
    old = (C._SHIPPED_CONFIG_PATH, C._CONFIG_PATH, dict(C.CFG["pdms"]))
    C._SHIPPED_CONFIG_PATH, C._CONFIG_PATH = ship, mine
    return ship, mine, old


def _restore(old):
    C._SHIPPED_CONFIG_PATH, C._CONFIG_PATH = old[0], old[1]
    C.CFG["pdms"] = old[2]


def test_first_use_copies_the_shipped_file_and_the_app_reads_the_users_copy():
    ship, mine, old = _setup("first")
    try:
        assert not mine.exists()
        cfg = C.load_config()
        assert mine.exists() and mine.read_bytes() == ship.read_bytes()      # an exact copy, created on first use
        assert cfg["pdms"]["hotbed_rate"] == 59.0
        data = json.loads(mine.read_text(encoding="utf-8"))                   # the user edits THEIR copy ...
        data["pdms"]["hotbed_rate"] = 70.0
        mine.write_text(json.dumps(data), encoding="utf-8")
        assert C.load_config()["pdms"]["hotbed_rate"] == 70.0                 # ... and that is what is read (no rebuild)
        assert json.loads(ship.read_text(encoding="utf-8"))["pdms"]["hotbed_rate"] == 59.0   # the shipped file is untouched
    finally:
        _restore(old)


def test_a_damaged_copy_falls_back_to_the_shipped_file_and_is_reported():
    ship, mine, old = _setup("damaged")
    try:
        C.load_config()
        assert C.check_user_config() is None
        mine.write_text("{ not json", encoding="utf-8")
        assert C.load_config()["pdms"]["customer_name"] == "Shipped Customer"
        assert C.check_user_config()                                          # says why (shown in Settings)
        assert C.differs_from_shipped() == (0, 0)
    finally:
        _restore(old)


def test_what_differs_merge_shipped_and_reset():
    ship, mine, old = _setup("merge")
    try:
        C.load_config()
        assert C.differs_from_shipped() == (0, 0)
        data = json.loads(mine.read_text(encoding="utf-8"))
        data["pdms"]["hotbed_rate"] = 70.0                                    # my edit
        del data["scaffold"]                                                  # a setting my copy lacks (as after an upgrade)
        mine.write_text(json.dumps(data), encoding="utf-8")
        assert C.differs_from_shipped() == (1, 1)
        assert C.merge_shipped_config() == 1                                  # adds what is missing, keeps my value
        after = json.loads(mine.read_text(encoding="utf-8"))
        assert after["pdms"]["hotbed_rate"] == 70.0 and after["scaffold"]["label"] == "Conventional Scaffold"
        assert C.differs_from_shipped() == (1, 0) and C.merge_shipped_config() == 0
        C.reset_to_shipped_config()
        assert mine.read_bytes() == ship.read_bytes() and C.differs_from_shipped() == (0, 0)
    finally:
        _restore(old)


def test_the_hotbed_button_reads_the_users_copy():
    ship, mine, old = _setup("hotbed")
    try:
        C.load_config()
        data = json.loads(mine.read_text(encoding="utf-8"))
        data["pdms"]["hotbed_rate"] = 66.0
        mine.write_text(json.dumps(data), encoding="utf-8")
        assert C.reload_pdms_config()["hotbed_rate"] == 66.0
    finally:
        _restore(old)


def test_the_settings_row_shows_the_state_opens_the_file_merges_and_resets():
    from common import settings_view as SVW
    ship, mine, old = _setup("settings")
    real_open, real_q = QDesktopServices.openUrl, QMessageBox.question
    opened = []
    try:
        C.load_config()
        w = H.generator()
        tab = SVW.SettingsTab(w)
        assert tab._counts["config"].text() == "same as the shipped file"
        data = json.loads(mine.read_text(encoding="utf-8"))
        data["pdms"]["customer_name"] = "My Customer"
        del data["scaffold"]
        mine.write_text(json.dumps(data), encoding="utf-8")
        tab.refresh()
        assert tab._counts["config"].text() == "1 value(s) differ from the shipped file · 1 new shipped value(s) to merge"
        QDesktopServices.openUrl = staticmethod(lambda url: (opened.append(url.toLocalFile()), True)[1])
        tab._open_config()
        assert opened == [str(mine)]                                           # it opens the user's copy
        tab._merge_config()                                                    # (the information box is answered by the harness)
        assert json.loads(mine.read_text(encoding="utf-8"))["scaffold"]["label"] == "Conventional Scaffold"
        assert "differ from the shipped file" in tab._counts["config"].text() and "to merge" not in tab._counts["config"].text()
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel)
        tab._reset_config()
        assert json.loads(mine.read_text(encoding="utf-8"))["pdms"]["customer_name"] == "My Customer"   # Cancel: kept
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        tab._reset_config()
        assert mine.read_bytes() == ship.read_bytes() and tab._counts["config"].text() == "same as the shipped file"
        assert SVW.backups_for(mine)                                           # a backup was made first
        mine.write_text("{ broken", encoding="utf-8")
        tab.refresh()
        assert tab._counts["config"].text().startswith("⚠ cannot be read")
    finally:
        QDesktopServices.openUrl, QMessageBox.question = real_open, real_q
        _restore(old)
