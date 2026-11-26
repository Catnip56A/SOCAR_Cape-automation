"""Word proposal notes (ctr_tools/word_proposal_notes.py): the list, places with their own pre-tick and order, placeholders,
remembered values with closest-first suggestions. Pure data: nothing of Word or Qt; a sandboxed file."""
import json

import harness as H
from ctr_tools import word_proposal_notes as N


def _fresh(tag):
    p = H.SANDBOX / f"notes_{tag}.json"
    p.unlink(missing_ok=True)
    N._NOTES_PATH = p
    return p


def test_without_a_file_the_standard_notes_are_used_and_they_match_the_template_texts():
    _fresh("std")
    d = N.load_notes()
    assert set(d["places"]) >= {"schedule", "exclusions", "price_basis"}
    assert [n["kind"] for n in N.notes_for(d, "schedule")][:6] == ["numbered"] + ["bullet"] * 5
    ticks = N.default_ticks(d)                                                  # only the standard texts are pre-ticked
    assert [len(ticks[p]) for p in ("schedule", "exclusions", "price_basis")] == [6, 6, 3]
    assert ticks["exclusions"][0] == "excl-container"
    opt = [n for n in d["notes"] if n["id"].startswith("opt-")]                  # the team's optional notes: in every place, never pre-ticked
    assert len(opt) == 22 and all(set(n["places"]) == set(d["places"]) for n in opt)
    assert not any(n["places"][p]["pretick"] for n in opt for p in n["places"])


def test_save_load_roundtrip_and_junk_is_dropped():
    p = _fresh("rt")
    d = N.load_notes()
    N.save_notes(d)
    assert N.load_notes() == d
    p.write_text(json.dumps({"notes": [{"text": " "}, "x", {"text": "ok", "kind": "weird",
                                                              "places": {"nope": {}, "exclusions": {"order": 3}}}],
                             "values": {"hours": ["12", "", 7]}}), encoding="utf-8")
    back = N.load_notes()
    assert back["notes"][0]["text"] == "ok" and back["notes"][0]["kind"] == "numbered"
    assert list(back["notes"][0]["places"]) == ["exclusions"]                  # an unknown place is dropped
    assert N.shipped_ids() <= {n["id"] for n in back["notes"]}                 # the shipped notes are merged in
    assert back["values"] == {"hours": ["12", "7"]} and N.check_file() is None
    p.write_text(json.dumps({"notes": [{"text": "Old lead-in:", "kind": "header", "places": {"schedule": {"order": 0}}}]}),
                 encoding="utf-8")
    assert N.load_notes()["notes"][0]["kind"] == "numbered"                    # the removed "header" kind loads as numbered
    p.write_text("{ broken", encoding="utf-8")
    assert N.check_file() and N.load_notes() == N.default_notes()               # a damaged file: the standard notes


def test_a_note_can_be_in_several_places_each_with_its_own_pretick_and_order():
    _fresh("places")
    d = N.default_notes()
    n = N.add_note(d, "Mobilisation is by the client.", "numbered", {"exclusions": True, "price_basis": False})
    last_e, last_p = len(N.notes_for(d, "exclusions")) - 1, len(N.notes_for(d, "price_basis")) - 1
    assert n["places"]["exclusions"] == {"pretick": True, "order": last_e}      # at the end of each place
    assert n["places"]["price_basis"] == {"pretick": False, "order": last_p}
    assert n["id"] in N.default_ticks(d)["exclusions"] and n["id"] not in N.default_ticks(d)["price_basis"]
    N.set_place(d, n["id"], "exclusions", False)                                # out of one place only
    assert "exclusions" not in n["places"] and "price_basis" in n["places"]
    N.set_place(d, n["id"], "price_basis", False)                               # in no place: kept, never offered
    assert n in d["notes"] and not any(n in N.notes_for(d, p) for p in d["places"])
    N.delete_note(d, n["id"])
    assert n not in d["notes"]


def test_moving_a_note_changes_the_order_in_that_place_only():
    d = N.default_notes()
    N.set_place(d, "excl-lunch", "price_basis", True)
    before = [n["id"] for n in N.notes_for(d, "price_basis")]
    assert N.move(d, "exclusions", "excl-lunch", -1)
    assert [n["id"] for n in N.notes_for(d, "exclusions")][:2] == ["excl-lunch", "excl-container"]
    assert [n["id"] for n in N.notes_for(d, "price_basis")] == before           # the other place keeps its order
    assert not N.move(d, "exclusions", "excl-lunch", -1)                        # already first
    assert not N.move(d, "exclusions", "no-such", 1)
    last = N.notes_for(d, "exclusions")[-1]["id"]
    assert not N.move(d, "exclusions", last, 1)


def test_placeholders_are_found_rendered_and_missing_ones_named():
    t = "Normal schedule for {location} is {working_days} with {hours} hours, {hours}; {{NOT_ONE}}."
    assert N.placeholders_of(t) == ["location", "working_days", "hours"]        # each once; {{…}} is the template's tag
    vals = {"location": "Onshore", "working_days": "Monday to Friday"}
    assert N.render(t, vals) == "Normal schedule for Onshore is Monday to Friday with {hours} hours, {hours}; {{NOT_ONE}}."
    assert N.missing(t, vals) == ["hours"]
    assert N.render(t, {**vals, "hours": "10"}).count("{hours}") == 0
    d = N.default_notes()
    N.add_note(d, "Mobilisation is {mob_days} days.", "numbered", {"exclusions": True})
    assert "mob_days" in N.placeholder_names(d) and N.placeholder_names(d)[0] == "location"     # known first, then the user's own


def test_a_starting_value_is_the_apps_own_then_the_one_used_last_then_the_default():
    d = N.default_notes()
    assert N.initial_value(d, "hours") == "12" and N.initial_value(d, "nothing") == ""
    assert N.initial_value(d, "location", {"location": "Onshore"}) == "Onshore"
    N.remember(d, "hours", "10")
    assert N.initial_value(d, "hours") == "10" and N.initial_value(d, "hours", {"hours": "8"}) == "8"


def test_remembered_values_are_newest_first_without_repeats_and_can_be_forgotten():
    d = N.default_notes()
    for v in ("Monday to Sunday", "Monday to Friday", "Monday to Sunday", "  ", ""):
        N.remember(d, "working_days", v)
    assert d["values"]["working_days"] == ["Monday to Sunday", "Monday to Friday"]
    for i in range(60):
        N.remember(d, "hours", str(i))
    assert len(d["values"]["hours"]) == N.MAX_REMEMBERED and d["values"]["hours"][0] == "59"
    N.forget(d, "working_days", "Monday to Friday")
    N.forget(d, "working_days", "Monday to Sunday")
    assert "working_days" not in d["values"]


def test_suggestions_put_the_closest_to_the_typing_first():
    d = N.default_notes()
    for v in ("Monday to Friday", "Tuesday to Saturday", "Monday to Sunday", "Sunday to Thursday"):
        N.remember(d, "working_days", v)
    assert N.suggest(d, "working_days", "")[0] == "Sunday to Thursday"           # nothing typed: the newest first
    s = N.suggest(d, "working_days", "mon")
    assert s[:2] == ["Monday to Sunday", "Monday to Friday"] and "Tuesday to Saturday" not in s
    assert N.suggest(d, "working_days", "sun")[0] == "Sunday to Thursday"        # starts with it
    assert "Monday to Sunday" in N.suggest(d, "working_days", "sun")             # contains it
    assert N.suggest(d, "working_days", "mondey to sunday")[0] == "Monday to Sunday"   # a slip of the keyboard
    assert N.suggest(d, "working_days", "zzzz") == []
    assert len(N.suggest(d, "working_days", "", limit=2)) == 2
    assert N.suggest(d, "hours", "1") == ["12"]                                   # a known placeholder's default is offered


def test_the_search_bar_finds_notes_by_any_words_of_their_text():
    d = N.default_notes()
    assert {n["id"] for n in N.search(d, "LUNCH")} == {"sched-lunch", "excl-lunch", "opt-x-lunch"}
    assert {n["id"] for n in N.search(d, "lunch provided client")} == {"sched-lunch", "excl-lunch"}
    assert {n["id"] for n in N.search(d, "tea break")} == {"sched-lunch", "opt-x-lunch"}
    assert [n["id"] for n in N.search(d, "only offshore")] == ["opt-x-transport"]        # the hint is searched too
    assert len(N.search(d, "")) == len(d["notes"]) and N.search(d, "zzz") == []


def test_the_team_notes_have_hints_placeholders_and_clean_texts():
    d = N.default_notes()
    by = {n["id"]: n for n in d["notes"]}
    assert by["opt-prices-azn"]["hint"] == "ONLY AZN" and by["opt-x-transport"]["hint"] == "ONLY FOR OFFSHORE"
    assert by["price-invoice"]["hint"] == "ONLY REIMBURSABLE PROPOSAL"                  # a standard note gets the hint too
    assert N.placeholders_of(by["opt-other-tools"]["text"]) == ["markup_materials", "markup_third_party"]
    assert "{validity_days}" in by["opt-prices-usd"]["text"]
    assert not any(h in n["text"] for n in d["notes"] for h in ("ONLY ", "CHECK"))        # no annotation inside a text


def test_an_older_list_gets_the_new_shipped_notes_without_losing_its_own_and_a_deleted_one_stays_deleted():
    p = _fresh("merge")
    old = N.default_notes()
    old["notes"] = [n for n in old["notes"] if not n["id"].startswith("opt-")]            # a list saved before they existed
    for n in old["notes"]:
        n.pop("hint", None)
    mine = N.add_note(old, "My own note.", "numbered", {"exclusions": True})
    mine["text"] = "My own note, edited."
    p.write_text(json.dumps(old), encoding="utf-8")
    back = N.load_notes()
    ids = [n["id"] for n in back["notes"]]
    assert N.shipped_ids() <= set(ids) and mine["id"] in ids                              # both
    assert next(n for n in back["notes"] if n["id"] == mine["id"])["text"] == "My own note, edited."
    assert N.default_ticks(back)["exclusions"][-1] == mine["id"]                          # still pre-ticked; the new ones are not
    assert next(n for n in back["notes"] if n["id"] == "price-invoice")["hint"]
    assert N.merge_shipped(back) == 0                                                     # nothing to add twice
    N.delete_note(back, "opt-advance")
    assert "opt-advance" in back["removed"]
    N.save_notes(back)
    assert "opt-advance" not in [n["id"] for n in N.load_notes()["notes"]]                # not brought back by the next load
    N.delete_note(back, mine["id"])
    assert mine["id"] not in back["removed"]                                              # only shipped notes are tracked


def test_a_placeholder_of_your_own_can_be_defined_used_and_removed():
    p = _fresh("own")
    d = N.default_notes()
    assert N.add_placeholder(d, " {crane_days} ", "Crane days", "3") == "crane_days"
    assert N.label_of(d, "crane_days") == "Crane days" and N.default_of(d, "crane_days") == "3"
    assert N.initial_value(d, "crane_days") == "3" and N.suggest(d, "crane_days", "") == ["3"]
    assert "crane_days" in N.placeholder_names(d) and N.placeholder_names(d)[:3] == ["location", "working_days", "hours"]
    for bad in ("", "9lives", "two words", "hours", "crane_days", "x-y"):                      # not valid / known / taken
        try:
            N.add_placeholder(d, bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    N.add_note(d, "A crane is on site for {crane_days} days.", "numbered", {"exclusions": True})
    N.save_notes(d)
    back = N.load_notes()
    assert back["placeholders"]["crane_days"] == {"label": "Crane days", "default": "3"}
    assert N.remove_placeholder(back, "crane_days") and "crane_days" not in back["values"]
    assert not N.remove_placeholder(back, "hours") and not N.remove_placeholder(back, "nope")  # the app's own stay
    assert "crane_days" in N.placeholder_names(back)                                            # a note still uses it
    p.write_text(json.dumps({"placeholders": {"ok": {"label": "L"}, "bad name": {}, "x": 5}}), encoding="utf-8")
    assert list(N.load_notes()["placeholders"]) == ["ok"]


def test_values_can_be_prepared_ahead_reordered_and_one_made_the_starting_value():
    _fresh("prep")
    d = N.default_notes()
    for v in ("Monday to Saturday", "Sunday to Thursday", "Monday to Saturday", " "):
        N.add_value(d, "working_days", v)
    assert d["values"]["working_days"] == ["Monday to Saturday", "Sunday to Thursday"]      # added at the end, no repeats
    N.remember(d, "working_days", "Monday to Friday")                                       # one used in a proposal: on top
    assert d["values"]["working_days"][0] == "Monday to Friday"
    assert N.move_value(d, "working_days", "Sunday to Thursday", -1)
    assert d["values"]["working_days"] == ["Monday to Friday", "Sunday to Thursday", "Monday to Saturday"]
    assert not N.move_value(d, "working_days", "Monday to Friday", -1) and not N.move_value(d, "working_days", "x", 1)
    assert N.initial_value(d, "working_days") == "Monday to Friday"                         # the one used last
    N.set_default(d, "working_days", "Monday to Saturday")
    assert N.initial_value(d, "working_days") == "Monday to Saturday"                       # a starting value the user set wins
    N.remember(d, "working_days", "Tuesday")                                                # and a later use does not replace it
    assert N.initial_value(d, "working_days") == "Monday to Saturday"
    assert N.initial_value(d, "working_days", {"working_days": "Sunday"}) == "Sunday"       # what the app knows still wins
    assert N.label_of(d, "working_days") == "Working days"                                  # a known one keeps its label
    N.save_notes(d)
    assert N.load_notes()["placeholders"]["working_days"]["default"] == "Monday to Saturday"


def test_the_four_work_schedules_set_the_overtime_wording_and_a_schedule_of_your_own_is_saved():
    d = N.default_notes()
    assert [s["id"] for s in N.all_schedules(d)] == ["offshore-7-12", "onshore-6-10", "onshore-7-12", "onshore-5-8"]
    sat = N.schedule_by_id(d, "onshore-6-10")["values"]
    assert sat == {"location": "Onshore", "working_days": "Monday to Saturday", "hours": "10",
                   "overtime_days": "Sunday, ", "overtime_coef": "1.5"}
    fri = N.schedule_by_id(d, "onshore-5-8")["values"]
    over = next(n for n in d["notes"] if n["id"] == "sched-overtime")["text"]
    assert N.render(over, sat) == "Sunday, Overtime and holiday hours/days will be calculated based on pro-rata with x1.5 coefficient."
    assert N.render(over, fri).startswith("Saturday, Sunday, Overtime and holiday")
    off = N.schedule_by_id(d, "offshore-7-12")["values"]
    assert N.render(over, off) == "Overtime and holiday hours/days will be calculated based on pro-rata with x1 coefficient."
    assert N.missing(over, off) == []                                  # an empty {overtime_days} is allowed, not "missing"
    assert N.missing("Needs {crane_days}", off) == ["crane_days"]       # any other empty placeholder still is
    mine = N.add_schedule(d, "Onshore, Monday to Saturday, 12 hours",
                          {"location": "Onshore", "working_days": "Monday to Saturday", "hours": "12",
                           "overtime_days": "Sunday, ", "overtime_coef": "1.5"})
    assert N.schedule_by_id(d, mine["id"])["label"].startswith("Onshore, Monday to Saturday, 12")
    N._NOTES_PATH = H.SANDBOX / "notes_schedules.json"
    N.save_notes(d)
    again = N.load_notes()
    assert [s["id"] for s in N.all_schedules(again)][-1] == mine["id"] and len(N.all_schedules(again)) == 5
    assert not N.delete_schedule(again, "offshore-7-12") and len(N.all_schedules(again)) == 5     # a shipped one stays
    assert N.delete_schedule(again, mine["id"]) and len(N.all_schedules(again)) == 4


def test_a_saved_overtime_note_with_the_old_text_is_updated_and_an_edited_one_is_left_alone():
    d = N.default_notes()
    note = next(n for n in d["notes"] if n["id"] == "sched-overtime")
    note["text"] = "Overtime, Holiday hours/days will be calculated based on pro-rata with x{overtime_coef} coefficient."
    N.merge_shipped(d)
    assert next(n for n in d["notes"] if n["id"] == "sched-overtime")["text"].startswith("{overtime_days}Overtime and holiday")
    note["text"] = "My own overtime wording x{overtime_coef}."
    N.merge_shipped(d)
    assert next(n for n in d["notes"] if n["id"] == "sched-overtime")["text"] == "My own overtime wording x{overtime_coef}."
