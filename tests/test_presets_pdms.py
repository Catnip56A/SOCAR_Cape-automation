"""CTR presets also keep the PDMS proposal's header: Customer, Customer city, Signer, Signer title (Client was always saved)."""
import harness as H
from ctr_tools import window as W
from ctr_tools.presets import _PRESET_FIELDS


def test_the_pdms_header_is_saved_in_a_preset_and_loaded_back():
    w = H.generator()
    w.show()
    for edit, text in ((w._client_edit, "SOCAR"), (w._customer_edit, "Example Customer Ltd"),
                       (w._customer_city_edit, "Baku, Azerbaijan"), (w._signer_edit, "Jane Roe"),
                       (w._signer_title_edit, "Director")):
        edit.setText(text)
    data = w._current_preset_data()
    assert (data["client"], data["customer_name"], data["customer_city"], data["signer_name"], data["signer_title"]) \
        == ("SOCAR", "Example Customer Ltd", "Baku, Azerbaijan", "Jane Roe", "Director")
    for edit in (w._client_edit, w._customer_edit, w._customer_city_edit, w._signer_edit, w._signer_title_edit):
        edit.setText("changed")
    w._apply_preset_data(data)                                               # loading the preset puts all five back
    assert [e.text() for e in (w._client_edit, w._customer_edit, w._customer_city_edit, w._signer_edit,
                               w._signer_title_edit)] == ["SOCAR", "Example Customer Ltd", "Baku, Azerbaijan", "Jane Roe", "Director"]


def test_an_older_preset_without_the_pdms_header_leaves_those_boxes_alone():
    w = H.generator()
    w.show()
    for edit, text in ((w._customer_edit, "Kept Customer"), (w._customer_city_edit, "Kept City"),
                       (w._signer_edit, "Kept Signer"), (w._signer_title_edit, "Kept Title")):
        edit.setText(text)
    old = {"client": "OLD CLIENT", "sub_client": "", "location": "X", "scope": "Y", "revision": "1",
           "project_type": "Offshore", "job_ref": "5", "output_dir": "", "markup_rate_pct": 6.5}
    w._apply_preset_data(old)
    assert w._client_edit.text() == "OLD CLIENT"                              # what the old preset has is applied ...
    assert [e.text() for e in (w._customer_edit, w._customer_city_edit, w._signer_edit, w._signer_title_edit)] \
        == ["Kept Customer", "Kept City", "Kept Signer", "Kept Title"]        # ... the rest is not wiped


def test_the_presets_label_names_the_new_fields():
    for f in ("customer_name", "customer_city", "signer_name", "signer_title"):
        assert f in _PRESET_FIELDS
    assert "PDMS Customer" in W._PRESET_SAVES and "PDMS Signer title" in W._PRESET_SAVES
