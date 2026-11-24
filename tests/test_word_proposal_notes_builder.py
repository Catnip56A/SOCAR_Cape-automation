"""The notes in the Word proposal: a {{NOTES_<PLACE>}} paragraph of the template is the model copied once per
note (style, indent and Word numbering), a bullet note gets "* ", an empty place loses its heading, a template without
the tags is unaffected. Fixture rows; the tagged copy of the template is read, the output goes to the sandbox."""
import copy

import docx
from docx.oxml.ns import qn

import harness as H
from ctr_tools import builder_word_proposal as B
from ctr_tools import word_proposal_notes as N
import test_builder_word_proposal as TB

V2 = H.INPUTS / "Word_Proposal_Template_short_v2.docx"
OLD = H.INPUTS / "Word_Proposal_Template_short.docx"


def _need():
    if not V2.is_file():
        raise H.Skip("Word_Proposal_Template_short_v2.docx is not in TEST_Files/CTR creator")
    N._NOTES_PATH = H.SANDBOX / "notes_builder_none.json"        # no saved list: the standard notes


def _build(tag, template=V2, **kw):
    out = B.build_word_proposal(TB.MAN, [], TB.EQUIP, TB.CONS, template, H.SANDBOX / f"word_proposaln_{tag}", TB.HEADER,
                       rates={"USD": 1.7}, markup_rate=0.1, **kw)
    return docx.Document(str(out))


def _num(p):
    pPr = p._p.pPr
    n = pPr.find(qn("w:numPr")) if pPr is not None else None
    return None if n is None else (n.find(qn("w:ilvl")).get(qn("w:val")), n.find(qn("w:numId")).get(qn("w:val")))


def _after(d, heading):
    """(the non-empty paragraphs after a heading, in order): the template keeps its blank line under the heading."""
    ps = d.paragraphs
    i = next(i for i, p in enumerate(ps) if p.text.strip() == heading)
    return [p for p in ps[i + 1:] if p.text.strip()]


def test_the_standard_notes_fill_the_tagged_template():
    _need()
    d = _build("std")
    text = "\n".join(p.text for p in d.paragraphs)
    assert "{" not in text and "}" not in text
    ps = _after(d, "Schedule of Activities")
    assert ps[0].text == "Offshore Work Schedule:"                                      # {location} from the proposal
    assert ps[1].text == "* Normal Working schedule for Offshore is based on Monday to Sunday with 12 working hours."
    assert ps[3].text == "* Standby days/hours will be calculated based on x1 coefficient."
    assert ps[4].text == "* NightShift will be calculated based on x1.2 coefficient."
    assert ps[0].style.name == "CAP_SignOff" and _num(ps[0]) == ("1", "44")             # the numbered first line (2.1)
    assert all(_num(ps[k]) is None for k in range(1, 6))                                # the "* " lines carry no number
    ps = _after(d, "exclusionS")
    excl = ps[:6]
    assert all(_num(p) == ("1", "44") for p in excl)                                    # Word numbers them 3.1 … 3.6
    assert "Office container" in excl[0].text
    assert "plus 10% mark-up and third party services at cost plus 5%." in excl[-1].text
    ps = _after(d, "PRICE BASIS AND PAYMENT TERMS")
    assert "valid for 30 days." in ps[0].text and "quoted are in AZN for" in ps[2].text


def test_chosen_notes_values_and_order_are_used_and_an_empty_place_loses_its_heading():
    _need()
    notes = {"items": {"schedule": [{"text": "Work is {working_days}.", "kind": "bullet"},
                                    {"text": "Standby x{standby_coef}.", "kind": "bullet"}],
                       "exclusions": [],                                              # nothing ticked
                       "price_basis": [{"text": "Second {validity_days}", "kind": "numbered"},
                                       {"text": "First", "kind": "numbered"},
                                       {"text": "Needs {unknown_name}", "kind": "numbered"}],
                       "not_in_template": [{"text": "ignored", "kind": "numbered"}]},
             "values": {"working_days": "Monday to Friday", "standby_coef": "1.5", "validity_days": "45"}}
    d = _build("chosen", notes=notes)
    texts = [p.text for p in d.paragraphs]
    assert "exclusionS" not in texts and "Schedule of Activities" in texts             # the empty place: heading gone
    assert [p.text for p in _after(d, "Schedule of Activities")[:2]] == ["* Work is Monday to Friday.", "* Standby x1.5."]
    assert [p.text for p in _after(d, "PRICE BASIS AND PAYMENT TERMS")[:3]] == [
        "Second 45", "First", "Needs {unknown_name}"]                                  # the order given; a name with no value stays
    assert not any("ignored" in t for t in texts)
    assert not any(t.startswith("{{") for t in texts)


def test_a_template_without_the_tags_is_unaffected_and_a_bullet_without_its_own_model_still_works():
    _need()
    if not OLD.is_file():
        raise H.Skip("Word_Proposal_Template_short.docx is not in TEST_Files/CTR creator")
    plain = [p.text for p in _build("old1", template=OLD).paragraphs]
    given = [p.text for p in _build("old2", template=OLD, notes={"items": {"schedule": [{"text": "x", "kind": "numbered"}]},
                                                                  "values": {}}).paragraphs]
    assert plain == given and "Prices Validity - all prices are valid for 30 days." in plain      # the fixed texts stay
    d = docx.Document(str(V2))                                                         # no _BULLET model for a place
    gone = next(p for p in d.paragraphs if p.text.strip() == "{{NOTES_SCHEDULE_BULLET}}")
    gone._p.getparent().remove(gone._p)
    tpl = H.SANDBOX / "v2_no_bullet_model.docx"
    d.save(str(tpl))
    out = _build("nobullet", template=tpl, notes={"items": {"schedule": [{"text": "Line", "kind": "bullet"}]}, "values": {}})
    texts = [p.text for p in out.paragraphs]
    assert "* Line" in texts


def test_the_builder_helpers_find_the_places_of_the_template():
    _need()
    assert B.notes_places_in(docx.Document(str(V2))) == ["schedule", "exclusions", "price_basis"]
    assert B.notes_places_in(docx.Document(str(OLD))) == [] if OLD.is_file() else True


def test_the_chosen_schedule_gives_the_sunday_and_saturday_overtime_wording_in_the_proposal():
    _need()
    def overtime(schedule):
        notes = {"items": {"schedule": [{"text": n["text"], "kind": n["kind"]}
                                        for n in N.notes_for(N.default_notes(), "schedule") if n["id"] == "sched-overtime"]},
                 "values": dict(N.schedule_by_id(N.default_notes(), schedule)["values"], standby_coef="1")}
        d = _build("ot_" + schedule, notes=notes)
        return _after(d, "Schedule of Activities")[0].text.removeprefix("* ")
    assert overtime("offshore-7-12") == "Overtime and holiday hours/days will be calculated based on pro-rata with x1 coefficient."
    assert overtime("onshore-6-10").startswith("Sunday, Overtime and holiday") and "x1.5" in overtime("onshore-6-10")
    assert overtime("onshore-5-8").startswith("Saturday, Sunday, Overtime and holiday")
