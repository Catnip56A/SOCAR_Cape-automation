"""CTR Tracker company -> Project Code list: a company's code wins over the Location's in either order, and the list is
edited in Settings (saved into the user's template_config.json, applied at once). Sandbox fixtures only."""
import json

from PySide6.QtWidgets import QMessageBox

import harness as H
import ctr_tools.config as C

SHIPPED = {"ctr_tracker_companies": {"Turan Drilling": "AZE0104", "CDC": "AZE0129"}, "pdms": {"hotbed_rate": 59.0}}


def _setup(tag, user=None):
    d = H.SANDBOX / f"cmp_{tag}"
    d.mkdir(exist_ok=True)
    ship, mine = d / "shipped.json", d / "user" / "template_config.json"
    ship.write_text(json.dumps(SHIPPED, indent=2), encoding="utf-8")
    mine.parent.mkdir(exist_ok=True)
    mine.write_text(json.dumps(user if user is not None else SHIPPED, indent=2), encoding="utf-8")
    old = (C._SHIPPED_CONFIG_PATH, C._CONFIG_PATH, dict(C.CFG.get("ctr_tracker_companies", {})))
    C._SHIPPED_CONFIG_PATH, C._CONFIG_PATH = ship, mine
    C.reload_tracker_companies()
    return ship, mine, old


def _restore(old):
    C._SHIPPED_CONFIG_PATH, C._CONFIG_PATH = old[0], old[1]
    C.CFG["ctr_tracker_companies"] = old[2]


def _two_sites(widget):
    sites = [(n, v) for n, v in widget._locations.items()]
    first = sites[0]
    other = next(s for s in sites if s[1]["project_code"] != first[1]["project_code"])
    return first, other


def test_a_company_code_wins_over_the_location_whichever_is_set_first():
    from ctr_tools.tracker_window import CTRTrackerWidget
    ship, mine, old = _setup("order")
    try:
        w = CTRTrackerWidget()
        (s1, l1), (s2, l2) = _two_sites(w)
        code = lambda: w._project_code_edit.text()
        # location first, then the company
        w._site_combo.setCurrentText(s1)
        assert code() == l1["project_code"]
        w._client_edit.setText("Turan Drilling")
        assert code() == "AZE0104"
        # changing the location afterwards leaves the company's code alone (both directions)
        w._site_combo.setCurrentText(s2)
        assert code() == "AZE0104"
        w._site_combo.setCurrentText(s1)
        assert code() == "AZE0104"
        # another company: the location's code comes back; the first company again: its code again
        w._client_edit.setText("Somebody else")
        assert code() == l1["project_code"]
        w._client_edit.setText("turan drilling")                       # capitals do not matter
        assert code() == "AZE0104"
        # company first, then the location
        w._client_edit.setText("")
        w._site_combo.setCurrentIndex(0)
        w._client_edit.setText("CDC")
        assert code() == "AZE0129"
        w._site_combo.setCurrentText(s2)
        assert code() == "AZE0129"
        w.deleteLater()
    finally:
        _restore(old)


def test_loading_a_ctr_of_a_company_gives_its_code_even_though_the_site_sets_one_too():
    from ctr_tools.tracker import CTREntry
    from ctr_tools.tracker_window import CTRTrackerWidget
    ship, mine, old = _setup("load")
    try:
        w = CTRTrackerWidget()
        (s1, _), _other = _two_sites(w)
        w._apply_loaded_entry(CTREntry(client="Turan Drilling", ctr_number="CTR-26-1", site_hint=s1, currency="USD"))
        assert w._project_code_edit.text() == "AZE0104"
        w.deleteLater()
    finally:
        _restore(old)


def test_saving_the_list_keeps_other_settings_and_applies_at_once():
    ship, mine, old = _setup("save", user={"ctr_tracker_companies": {"Old Co": "X1"}, "pdms": {"hotbed_rate": 77.0}})
    try:
        from ctr_tools.tracker import company_project_code
        assert company_project_code("old co") == "X1"
        C.save_tracker_companies({"New Co": "N9"})
        data = json.loads(mine.read_text(encoding="utf-8"))
        assert data["ctr_tracker_companies"] == {"New Co": "N9"} and data["pdms"]["hotbed_rate"] == 77.0
        assert company_project_code("New Co") == "N9" and company_project_code("Old Co") is None
        assert not list(mine.parent.glob("*.tmp"))
        mine.write_text("{ broken", encoding="utf-8")
        before = mine.read_bytes()
        try:
            C.save_tracker_companies({"X": "1"})
            raise AssertionError("a damaged file must not be overwritten")
        except ValueError:
            pass
        assert mine.read_bytes() == before
    finally:
        _restore(old)


def test_the_settings_row_edits_resets_and_lists_the_companies():
    from common import settings_view as SVW
    ship, mine, old = _setup("row", user={"ctr_tracker_companies": {"Old Co": "X1"}, "pdms": {"hotbed_rate": 77.0}})
    real_exec, real_q = SVW._CompaniesDialog.exec, QMessageBox.question
    try:
        w = H.generator()
        tab = SVW.SettingsTab(w)
        assert tab._counts["companies"].text() == "1 companies"
        dlg_entries = {}

        def fake_exec(self):
            self._add("New Co", "N9")
            self._add("new co", "N10")                                # the same company again (capitals): the last row wins
            self._add("No code", "")                                  # incomplete rows are dropped
            dlg_entries.update(self.entries())
            return SVW.QDialog.DialogCode.Accepted
        SVW._CompaniesDialog.exec = fake_exec
        tab._edit_companies()
        assert dlg_entries == {"Old Co": "X1", "new co": "N10"}
        assert json.loads(mine.read_text(encoding="utf-8"))["ctr_tracker_companies"] == {"Old Co": "X1", "new co": "N10"}
        assert tab._counts["companies"].text() == "2 companies" and SVW.backups_for(mine)
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        tab._reset_companies()
        data = json.loads(mine.read_text(encoding="utf-8"))
        assert data["ctr_tracker_companies"] == SHIPPED["ctr_tracker_companies"] and data["pdms"]["hotbed_rate"] == 77.0
        assert tab._counts["companies"].text() == "2 companies"
    finally:
        SVW._CompaniesDialog.exec, QMessageBox.question = real_exec, real_q
        _restore(old)
