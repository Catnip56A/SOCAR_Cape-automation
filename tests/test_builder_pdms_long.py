"""The long PDMS proposal (PDMS_TEMPLATE_long.docx, a tagged copy of the long proposal): Position and Shift columns,
mark-up columns, its own third-party table, tables lettered A-D in the order present, the cover's text boxes, the notes
(schedule, 8.2 pricing notes, Provided by client), an empty optional table or place leaving no trace. Fixture rows."""
import docx
from docx.oxml.ns import qn

import harness as H
from ctr_tools import builder_pdms as B
from ctr_tools import pdms_notes as N
import test_builder_pdms as TB

LONG = H.INPUTS / "PDMS_TEMPLATE_long.docx"


def _need():
    if not LONG.is_file():
        raise H.Skip("PDMS_TEMPLATE_long.docx is not in TEST_Files/CTR creator")
    N._NOTES_PATH = H.SANDBOX / "notes_long_none.json"


def _build(tag, equip=TB.EQUIP, cons=TB.CONS, transport=TB.TRANSPORT, hotbed=None, **kw):
    out = B.build_pdms(TB.MAN, [], equip, cons, LONG, H.SANDBOX / f"pdmsl_{tag}", dict(TB.HEADER, project_type="Onshore"),
                       rates={"USD": 1.7}, markup_rate=0.1, third_party_rows=transport, hotbed=hotbed, **kw)
    return docx.Document(str(out))


def _tables(d):
    """The commercial tables by their first header cell texts (the clients table is the first of the document)."""
    return {t.rows[0].cells[1].text.strip(): [[c.text for c in r.cells] for r in t.rows] for t in d.tables}


def _all_text(d):
    return "\n".join("".join(t.text or "" for t in p.iter(qn("w:t"))) for p in d.element.body.iter(qn("w:p")))


def test_the_long_proposal_has_position_shift_markup_and_its_own_third_party_table():
    _need()
    d = _build("full", hotbed={"rate": 59.0, "days": 10})
    text = _all_text(d)
    assert "{{" not in text and "}}" not in text
    t = _tables(d)
    labour = t["Position"]
    assert labour[1] == ["1", "PA Level Foreman", "60", "Day shift", "1", "445.68 ₼", "26,740.80 ₼"]     # Position and Shift apart
    assert labour[3][1:] == ["Hotbed – if required", "10", "", "", "59.00 ₼", "590.00 ₼"] and labour[3][0] == ""
    assert labour[-1][-1] == "59,774.00 ₼"
    equip = [[c.text for c in r.cells] for r in [tb for tb in d.tables if tb.rows[0].cells[1].text.strip() == "Description"][0].rows]
    assert equip[1] == ["1", "Conventional Scaffold", "62", "TON", "11.42", "4.25 ₼", "3,009.17 ₼"]
    descs = [tb for tb in d.tables if tb.rows[0].cells[1].text.strip() == "Description"]
    cons = [[c.text for c in r.cells] for r in descs[1].rows]
    tp = [[c.text for c in r.cells] for r in descs[2].rows]
    assert cons[1] == ["1", "GLOVES, SIZE 9", "EA", "15", "10%", "3.40 ₼", "56.10 ₼"]              # price before the mark-up
    assert cons[-1][-1] == "56.10 ₼"                                                                  # no transport in it
    assert tp[1] == ["1", "Minibus+driver", "Days", "20", "6.5%", "110.00 ₼", "2,343.00 ₼"]           # quantity x duration
    assert tp[2][4] == "0%" and tp[-1][-1] == "2,543.00 ₼"
    for caption in ("Table A. Personnel", "Table B. Plant and Equipment", "Table C. Materials and Consumables",
                    "Table D. Third-Party services"):
        assert caption in text
    assert "plus 10% mark-up." in text and "plus 6.5% mark-up." in text                              # the captions under C and D


def test_the_cover_text_boxes_are_filled_in_both_copies_word_keeps():
    _need()
    d = _build("cover")
    boxes = [b for b in d.element.body.iter(qn("w:txbxContent"))
             if "Location" in "".join(t.text or "" for t in b.iter(qn("w:t")))]
    assert len(boxes) == 2
    for b in boxes:
        txt = "".join(t.text or "" for t in b.iter(qn("w:t")))
        assert "Location: Onshore" in txt and "SCOPE: P000001 - Test job - REQ0000001" in txt
    text = _all_text(d)
    assert "Reference Number: PRP-26-099" in text and "Date: 9 September 2026" in text


def test_an_empty_optional_table_leaves_no_caption_note_or_letter_gap():
    _need()
    d = _build("empty", equip=[], cons=[], transport=[])
    text = _all_text(d)
    assert "{{" not in text
    for gone in ("Plant and Equipment", "Materials and Consumables", "Third-Party services", "third-party services to be charged",
                 "consumables to be charged"):
        assert gone not in text
    assert "Table A. Personnel" in text
    # only the labour table is left of the commercial tables
    assert sum(1 for tb in d.tables if tb.rows[0].cells[1].text.strip() in ("Position", "Description")) == 1
    d2 = _build("noequip", equip=[], transport=[])                        # the letters follow the tables that are there
    text2 = _all_text(d2)
    assert "Table A. Personnel" in text2 and "Table B. Materials and Consumables" in text2 and "Plant and Equipment" not in text2


def test_the_long_proposal_notes_take_the_chosen_schedule_pricing_notes_and_client_items():
    _need()
    data = N.default_notes()
    sched = N.schedule_by_id(data, "onshore-5-8")["values"]
    ids = N.default_ticks(data)
    items = {p: [{"text": n["text"], "kind": n["kind"]} for n in N.notes_for(data, p) if n["id"] in ids[p]]
             for p in ids}
    values = {n: N.initial_value(data, n, {}) for n in N.placeholder_names(data)}
    values.update(sched)
    d = _build("notes", notes={"items": items, "values": values})
    paras = [p.text for p in d.paragraphs if p.text.strip()]
    i = paras.index("Onshore Work Schedule:")
    assert paras[i + 1] == "* Normal Working schedule for Onshore is based on Monday to Friday with 8 working hours."
    assert paras[i + 5].startswith("* Saturday, Sunday, Overtime and holiday") and "x1.5" in paras[i + 5]
    assert "8.2 Pricing notes" in paras and "Provided by client:" in paras
    assert "Prices Validity - all prices are valid for 30 days." in paras
    assert not any("ONLY" in p for p in paras if "reimbursable" in p.lower())                       # hints are never written
    d2 = _build("nonotes", notes={"items": {"schedule": [], "exclusions": [], "price_basis": []}, "values": {}})
    t2 = [p.text for p in d2.paragraphs if p.text.strip()]
    assert "8.2 Pricing notes" not in t2 and "Provided by client:" not in t2 and "Onshore Work Schedule:" not in t2


def test_an_untagged_proposal_is_refused_with_a_message_that_says_so():
    orig = H.INPUTS / "PDMS_old" / "long proposal.docx"
    if not orig.is_file():
        raise H.Skip("the original long proposal is not in TEST_Files/CTR creator/PDMS_old")
    try:
        B.build_pdms(TB.MAN, [], TB.EQUIP, TB.CONS, orig, H.SANDBOX / "pdmsl_untagged", TB.HEADER,
                     rates={"USD": 1.7}, markup_rate=0.1)
    except ValueError as exc:
        assert "not a tagged PDMS template" in str(exc) and "long proposal.docx" in str(exc)
    else:
        raise AssertionError("an untagged proposal was accepted")
