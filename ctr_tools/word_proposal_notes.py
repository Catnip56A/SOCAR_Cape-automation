"""
ctr_tools/word_proposal_notes.py — the notes of the Word proposal: the list, its places, the placeholders.

A note is a short text such as "Prices Validity - all prices are valid for {validity_days} days." It can
appear in one, several or no *places* — a place is a section of the proposal that has a tag in the
template ({{NOTES_<PLACE>}}), e.g. the Schedule of Activities, the Exclusions, the Price Basis. For each
place a note has its own "pre-ticked" flag (the note is used unless the user unticks it) and its own
position (the order inside that place).

`{name}` in a text is a *placeholder*, filled when the proposal is written from a value the user
chooses (location, working days, hours, coefficients, mark-ups, validity, currency …). Every value the
user types is remembered, so it can be chosen again; the closest remembered values to what is being
typed are offered first (`suggest`), and one can be forgotten (`forget`).

Stored as JSON in `word_proposal_notes.json` in the per-user data folder (see paths.py), like the other saved
lists. Without the file the built-in standard notes are used ("Reset" = delete the file).

Nothing here touches Word or Qt: builder_word_proposal.py writes the paragraphs, the window shows the list.

File layout:
    {"places": {key: label},
     "notes":  [{"id", "text", "hint" (when it is for; never written), "kind": "numbered"|"bullet",
                 "places": {place key: {"pretick": bool, "order": int}}}],
     "values": {placeholder name: [remembered values, newest first]},
     "removed": [ids of shipped notes the user deleted],
     "placeholders": {name: {"label", "default"}},     # the user's own placeholders; a known one may have just a "default"
     "schedules": [{"id", "label", "values": {SCHEDULE_KEYS: text}}]}   # the user's own work schedules (the four shipped ones are in the code)
"""

from __future__ import annotations

import copy
import difflib
import json
import logging
import re

from ctr_tools.paths import user_data_dir

log = logging.getLogger(__name__)

_NOTES_PATH = user_data_dir() / "word_proposal_notes.json"

KINDS = ("numbered", "bullet")          # a numbered item (3.1) or a "* " line under the one before; an older "header" counts as numbered
MAX_REMEMBERED = 40                                # remembered values per placeholder

# The sections of the proposal that take notes (the template carries {{NOTES_<KEY upper>}}).
DEFAULT_PLACES = {
    "schedule": "Schedule of Activities",
    "exclusions": "Exclusions",
    "price_basis": "Price Basis and Payment Terms",
}

# Placeholders the app knows: label, the value used until the user chooses one, and where the app can fill
# it from by itself ("auto"). Any other {name} in a note is the user's own: a plain typed value.
PLACEHOLDERS = {
    "location":            {"label": "Location (Onshore / Offshore)", "default": "Offshore", "auto": "request"},
    "working_days":        {"label": "Working days", "default": "Monday to Sunday"},
    "hours":               {"label": "Working hours per day", "default": "12"},
    "standby_coef":        {"label": "Standby coefficient", "default": "1"},
    "night_coef":          {"label": "Night shift coefficient", "default": "1.2"},
    "overtime_coef":       {"label": "Overtime / holiday coefficient", "default": "1"},
    "overtime_days":       {"label": "Days paid at the overtime rate, before \"Overtime and holiday\" (e.g. \"Sunday, \")",
                            "default": "", "optional": True},
    "markup_materials":    {"label": "Mark-up on materials, tools and equipment (%)", "default": "10", "auto": "Markup Rate box"},
    "markup_third_party":  {"label": "Mark-up on third party services (%)", "default": "5", "auto": "shared transport mark-up"},
    "validity_days":       {"label": "Prices valid for (days)", "default": "30"},
    "currency":            {"label": "Currency", "default": "AZN", "auto": "proposal currency"},
}

# The work schedules the user chooses from: one choice sets all of these placeholders (the standby and the night
# coefficient are the same in all four, so they stay ordinary placeholders). The user can add schedules of their own.
SCHEDULE_KEYS = ("location", "working_days", "hours", "overtime_days", "overtime_coef")
SHIPPED_SCHEDULES = [
    {"id": "offshore-7-12", "label": "Offshore, Monday to Sunday, 12 hours",
     "values": {"location": "Offshore", "working_days": "Monday to Sunday", "hours": "12",
                "overtime_days": "", "overtime_coef": "1"}},
    {"id": "onshore-6-10", "label": "Onshore, Monday to Saturday, 10 hours",
     "values": {"location": "Onshore", "working_days": "Monday to Saturday", "hours": "10",
                "overtime_days": "Sunday, ", "overtime_coef": "1.5"}},
    {"id": "onshore-7-12", "label": "Onshore, Monday to Sunday, 12 hours",
     "values": {"location": "Onshore", "working_days": "Monday to Sunday", "hours": "12",
                "overtime_days": "", "overtime_coef": "1"}},
    {"id": "onshore-5-8", "label": "Onshore, Monday to Friday, 8 hours",
     "values": {"location": "Onshore", "working_days": "Monday to Friday", "hours": "8",
                "overtime_days": "Saturday, Sunday, ", "overtime_coef": "1.5"}},
]
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PLACEHOLDER = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")


_OVERTIME_TEXT = ("{overtime_days}Overtime and holiday hours/days will be calculated based on pro-rata with "
                  "x{overtime_coef} coefficient.")
# A shipped note whose wording changed: a saved copy that still has one of the old texts is brought to the new one
# (a note the user edited is left alone).
_SUPERSEDED = {"sched-overtime": ["Overtime, Holiday hours/days will be calculated based on pro-rata with "
                                  "x{overtime_coef} coefficient."]}


def _note(id_, text, kind, place, order, pretick=True):
    return {"id": id_, "text": text, "kind": kind, "places": {place: {"pretick": pretick, "order": order}}}


# Notes the commercial team can tick when they apply: in every place, never pre-ticked. (id, text, hint): the hint says
# when the note is for (it is shown in the window and never written into the proposal).
_OPTIONAL = [
    ("opt-po", "Third-party equipment will not be hired and no labour & equipment mobilisation will take place until a PO or "
              "service agreement has been received.", ""),
    ("opt-reimbursable", "Quotation is cost-time reimbursable and is not fixed price in any way, any variations to the "
                         "foregoing will be charged at the applicable rate.", "ONLY REIMBURSABLE PROPOSAL"),
    ("opt-prices-azn", "All prices are in AZN excluding VAT, WHT and any other tax and are valid for {validity_days} days.",
     "ONLY AZN"),
    ("opt-prices-usd", "All prices are in USD excluding VAT, WHT and any other tax and are valid for {validity_days} days.",
     "ONLY USD"),
    ("opt-other-tools", "Any other tools, equipment and materials to be charged at net documented cost plus {markup_materials}% "
                        "mark-up and third-party services at cost plus {markup_third_party}%.", "CHECK"),
    ("opt-invoice-lumpsum", "Invoice processing shall be based on actual approved job completion certificate, act of "
                            "acceptance, etc.", "ONLY FOR LUMPSUM / UNIT RATES"),
    ("opt-indicative", "This is an indicative quotation only and prices will be adapted/finalised based on actual third-party "
                       "invoices. Invoice should be paid based on CBAR exchange rates (https://cbar.az) of the payment date.", ""),
    ("opt-scaffold-loss", "For loss of scaffolding materials Client will be charged 2500 USD / 4250 AZN per ton.", ""),
    ("opt-early-completion", "Early work completion (if the work is completed before the end of the day) it will calculated "
                             "as a full day.", ""),
    ("opt-mobilization", "Mobilization and demobilization days will be charged to Client based on normal daily rates.", ""),
    ("opt-advance", "Advance Payment 30%.", ""),
    ("opt-x-lunch", "Lunch, tea break and accommodation for staff;", ""),
    ("opt-x-messing", "Messing facilities, toilets and storage facilities;", ""),
    ("opt-x-laydown", "Secured Laydown area for equipment and materials;", ""),
    ("opt-x-transport", "Transportation from/to sea port to/from work site is excluded from rates;", "ONLY FOR OFFSHORE"),
    ("opt-x-medical", "First aid and medical facilities;", ""),
    ("opt-x-lifting", "Lifting equipment - forklift, crane, tractor and trailer.", ""),
    ("opt-x-waste", "Waste management", ""),
    # texts that stood fixed in the long templates of one task type (1.0.27 part 3)
    ("opt-fresh-water", "Fresh water, diesel for equipment and IBC for diesel and water will be provided by client.",
     "INDUSTRIAL CLEANING"),
    ("opt-wht-taxes", "Rates exclude WHT / applicable taxes.", "INSULATION"),
    ("opt-survey-days", "Survey is estimated to take 5 days based on unrestricted access to all zones. The team can close out "
                        "corrective actions, but any additional days incurred will be charged out at the rates mentioned in "
                        "section 7.1 of the proposal.", "INSULATION"),
    ("opt-reports", "Detailed reports will be issued to client within 2 weeks of completion of offshore inspection.",
     "INSULATION"),
]
# A standard note that also gets a hint from the team's list (it is the same text).
_HINTS = {"price-invoice": "ONLY REIMBURSABLE PROPOSAL"}


def default_notes() -> dict:
    """The standard texts of the Word proposal, as they were fixed in the template."""
    notes = [
        _note("sched-head", "{location} Work Schedule:", "numbered", "schedule", 0),
        _note("sched-days", "Normal Working schedule for {location} is based on {working_days} with {hours} working hours.",
              "bullet", "schedule", 1),
        _note("sched-lunch", "Normal Working hours inclusive lunch and tea break; lunch shall be provided by client.",
              "bullet", "schedule", 2),
        _note("sched-standby", "Standby days/hours will be calculated based on x{standby_coef} coefficient.",
              "bullet", "schedule", 3),
        _note("sched-night", "NightShift will be calculated based on x{night_coef} coefficient.", "bullet", "schedule", 4),
        _note("sched-overtime",
              _OVERTIME_TEXT,
              "bullet", "schedule", 5),
        _note("excl-container",
              "Office container, utilities such as electricity, water, gas, diesel and provision of office cleaning services.",
              "numbered", "exclusions", 0),
        _note("excl-lunch", "Lunch for labor and fuel for equipment will be provided by Client.", "numbered", "exclusions", 1),
        _note("excl-laydown",
              "Secured laydown areas for equipment and materials, messing facilities, toilets, storage facilities.",
              "numbered", "exclusions", 2),
        _note("excl-medical", "First aid and medical facilities will be provided by client, if required.",
              "numbered", "exclusions", 3),
        _note("excl-lifting", "Forklift, crane, tractor and trailer shall be provided by client, if required.",
              "numbered", "exclusions", 4),
        _note("excl-other",
              "Any other tools, equipment, materials to be charged at net documented cost plus {markup_materials}% mark-up "
              "and third party services at cost plus {markup_third_party}%.", "numbered", "exclusions", 5),
        _note("price-validity", "Prices Validity - all prices are valid for {validity_days} days.", "numbered", "price_basis", 0),
        _note("price-invoice",
              "Invoice processing shall be based on actual approved Time Sheets for Labour, Plant & Equipment Hire Sheets "
              "and Materials/Consumables Delivery Notes.", "numbered", "price_basis", 1),
        _note("price-currency",
              "Currency - prices quoted are in {currency} for labour, equipment and materials excluding value added tax.",
              "numbered", "price_basis", 2),
    ]
    for n in notes:
        n["hint"] = _HINTS.get(n["id"], "")
    data = {"places": dict(DEFAULT_PLACES), "notes": notes, "values": {}, "removed": [], "placeholders": {},
            "schedules": []}
    for nid, text, hint in _OPTIONAL:
        note = {"id": nid, "text": text, "kind": "numbered", "hint": hint, "places": {}}
        data["notes"].append(note)
        for place in data["places"]:
            set_place(data, nid, place, True, False)             # in every place, never pre-ticked
    return data


def shipped_ids() -> set[str]:
    return {n["id"] for n in default_notes()["notes"]}


def merge_shipped(data: dict) -> int:
    """Bring a saved list up to date with the notes that ship with the app: a shipped note the list does not have (and
    the user did not delete) is added, at the end of each of its places; a standard note gets the hint of the shipped one
    when it has none. The user's own notes, texts and order are never touched. Returns how many notes were added."""
    shipped = default_notes()
    have = {n["id"]: n for n in data["notes"]}
    added = 0
    for n in shipped["notes"]:
        if n["id"] in have:
            if have[n["id"]]["text"] in _SUPERSEDED.get(n["id"], []):
                have[n["id"]]["text"] = n["text"]
            if n.get("hint") and not have[n["id"]].get("hint"):
                have[n["id"]]["hint"] = n["hint"]
            continue
        if n["id"] in data.get("removed", []):
            continue
        new = {"id": n["id"], "text": n["text"], "kind": n["kind"], "hint": n.get("hint", ""), "places": {}}
        data["notes"].append(new)
        for place, spec in n["places"].items():
            if place in data["places"]:
                set_place(data, new["id"], place, True, spec["pretick"])
        added += 1
    return added


# ── load / save ──────────────────────────────────────────────────────────────

def _clean(data) -> dict:
    """The file's content as a well-formed list (junk entries are dropped, nothing is invented)."""
    if not isinstance(data, dict):
        raise ValueError("a JSON object was expected")
    places = {str(k): str(v) for k, v in (data.get("places") or {}).items() if str(k).strip()}
    for k, v in DEFAULT_PLACES.items():
        places.setdefault(k, v)
    notes, seen = [], set()
    for n in data.get("notes") or []:
        if not isinstance(n, dict) or not str(n.get("text", "")).strip():
            continue
        nid = str(n.get("id") or "").strip()
        if not nid or nid in seen:
            nid = new_id(seen)
        seen.add(nid)
        kind = n.get("kind") if n.get("kind") in KINDS else "numbered"
        pl = {}
        for key, spec in (n.get("places") or {}).items():
            if key in places and isinstance(spec, dict):
                order = spec.get("order")
                pl[key] = {"pretick": bool(spec.get("pretick", True)),
                           "order": order if isinstance(order, int) and not isinstance(order, bool) else len(notes)}
        notes.append({"id": nid, "text": str(n["text"]), "kind": kind, "hint": str(n.get("hint") or ""), "places": pl})
    values = {}
    for name, vals in (data.get("values") or {}).items():
        if isinstance(vals, list):
            values[str(name)] = [str(v) for v in vals if str(v).strip()][:MAX_REMEMBERED]
    removed = [str(r) for r in (data.get("removed") or []) if str(r).strip()]
    custom = {}
    for name, spec in (data.get("placeholders") or {}).items():
        if _NAME.match(str(name)) and isinstance(spec, dict):
            custom[str(name)] = {"label": str(spec.get("label") or ""), "default": str(spec.get("default") or "")}
    schedules, taken = [], {s["id"] for s in SHIPPED_SCHEDULES}
    for sc in data.get("schedules") or []:
        if not isinstance(sc, dict) or not str(sc.get("label", "")).strip() or not isinstance(sc.get("values"), dict):
            continue
        sid = str(sc.get("id") or "").strip()
        if not sid or sid in taken:
            sid = new_id(taken)
        taken.add(sid)
        schedules.append({"id": sid, "label": str(sc["label"]).strip(),
                          "values": {k: str(sc["values"].get(k, "")) for k in SCHEDULE_KEYS}})
    return {"places": places, "notes": notes, "values": values, "removed": removed, "placeholders": custom,
            "schedules": schedules}


def load_notes() -> dict:
    """The saved list, or the standard notes when there is no file (or it cannot be read)."""
    if not _NOTES_PATH.exists():
        return default_notes()
    try:
        data = _clean(json.loads(_NOTES_PATH.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        log.warning("Failed to load %s, using the standard notes: %s", _NOTES_PATH, exc)
        return default_notes()
    merge_shipped(data)                    # notes a newer version brings appear without a Reset (not pre-ticked)
    return data


def save_notes(data: dict) -> None:
    try:
        _NOTES_PATH.write_text(json.dumps(_clean(data), indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        # Not fatal: the list just will not survive a restart.
        log.warning("Failed to save %s: %s", _NOTES_PATH, exc)


def check_file() -> str | None:
    """None when there is no file or it is readable, else why not (Settings shows it)."""
    if not _NOTES_PATH.exists():
        return None
    try:
        _clean(json.loads(_NOTES_PATH.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        return str(exc)
    return None


def new_id(taken=()) -> str:
    n = 1
    while f"note-{n}" in taken:
        n += 1
    return f"note-{n}"


# ── the notes of a place ─────────────────────────────────────────────────────

def notes_for(data: dict, place: str) -> list[dict]:
    """The notes that have `place`, in that place's order."""
    here = [(n["places"][place]["order"], i, n) for i, n in enumerate(data["notes"]) if place in n["places"]]
    return [n for _o, _i, n in sorted(here, key=lambda t: (t[0], t[1]))]


def default_ticks(data: dict) -> dict[str, list[str]]:
    """{place: ids of the notes pre-ticked there}, in order — what a new proposal starts with."""
    return {p: [n["id"] for n in notes_for(data, p) if n["places"][p]["pretick"]] for p in data["places"]}


def move(data: dict, place: str, note_id: str, delta: int) -> bool:
    """Move a note up (-1) or down (+1) inside a place. Returns False at the end of the list."""
    order = notes_for(data, place)
    ids = [n["id"] for n in order]
    if note_id not in ids:
        return False
    i, j = ids.index(note_id), ids.index(note_id) + delta
    if not 0 <= j < len(ids):
        return False
    order[i], order[j] = order[j], order[i]
    for pos, n in enumerate(order):
        n["places"][place]["order"] = pos
    return True


def add_note(data: dict, text: str, kind: str = "numbered", places: dict | None = None, hint: str = "") -> dict:
    """Add a note; `places` = {place: pretick}. It goes to the end of each place."""
    note = {"id": new_id({n["id"] for n in data["notes"]}), "text": text, "kind": kind if kind in KINDS else "numbered",
            "hint": hint, "places": {}}
    data["notes"].append(note)
    for place, pretick in (places or {}).items():
        set_place(data, note["id"], place, True, pretick)
    return note


def set_place(data: dict, note_id: str, place: str, on: bool, pretick: bool = True) -> None:
    """Put a note in a place (at its end) or take it out; a note left in no place is kept (it is just not offered)."""
    note = next(n for n in data["notes"] if n["id"] == note_id)
    if not on:
        note["places"].pop(place, None)
        return
    if place in note["places"]:
        note["places"][place]["pretick"] = bool(pretick)
        return
    last = max((n["places"][place]["order"] for n in data["notes"] if place in n["places"]), default=-1)
    note["places"][place] = {"pretick": bool(pretick), "order": last + 1}


def delete_note(data: dict, note_id: str) -> None:
    """Delete a note. A note that ships with the app is remembered as removed, so an update does not bring it back."""
    data["notes"] = [n for n in data["notes"] if n["id"] != note_id]
    if note_id in shipped_ids() and note_id not in data.setdefault("removed", []):
        data["removed"].append(note_id)


# ── placeholders ─────────────────────────────────────────────────────────────

def placeholders_of(text: str) -> list[str]:
    """The names of the {placeholders} in a text, in order of appearance, each once."""
    return list(dict.fromkeys(_PLACEHOLDER.findall(text or "")))


def placeholder_names(data: dict) -> list[str]:
    """Every placeholder a note can use: the ones the app knows, the ones the user defined, then any a note already
    uses without a definition."""
    names = list(PLACEHOLDERS) + [n for n in data.get("placeholders", {}) if n not in PLACEHOLDERS]
    for n in data["notes"]:
        names += [p for p in placeholders_of(n["text"]) if p not in names]
    return names


def is_known(name: str) -> bool:
    return name in PLACEHOLDERS


def label_of(data: dict, name: str) -> str:
    return (PLACEHOLDERS.get(name, {}).get("label") or data.get("placeholders", {}).get(name, {}).get("label")
            or "your own")


def default_of(data: dict, name: str) -> str:
    """The value a placeholder starts with: the one the user set, else the app's own, else nothing."""
    own = data.get("placeholders", {}).get(name, {}).get("default")
    return str(own) if own else str(PLACEHOLDERS.get(name, {}).get("default", ""))


def add_placeholder(data: dict, name: str, label: str = "", default: str = "") -> str:
    """Define a placeholder of your own so notes can use {name}. Letters, digits and _ only; it must not be one the app
    knows or one that is defined already. Returns the name; ValueError says what is wrong."""
    name = (name or "").strip().strip("{}").strip()
    if not _NAME.match(name):
        raise ValueError("A name has letters, digits and _ only, and does not start with a digit.")
    if name in PLACEHOLDERS or name in data.setdefault("placeholders", {}):
        raise ValueError(f"{{{name}}} exists already.")
    data["placeholders"][name] = {"label": label.strip(), "default": default.strip()}
    if default.strip():
        remember(data, name, default)
    return name


def remove_placeholder(data: dict, name: str) -> bool:
    """Remove a placeholder of your own and its remembered values (a note that still uses it keeps the {name}, as one
    without a definition). The ones the app knows cannot be removed."""
    if name in PLACEHOLDERS or name not in data.get("placeholders", {}):
        return False
    del data["placeholders"][name]
    data["values"].pop(name, None)
    return True


def set_default(data: dict, name: str, value: str) -> None:
    """The value a new proposal starts with for this placeholder (a known one keeps the app's own label)."""
    spec = data.setdefault("placeholders", {}).setdefault(name, {"label": "", "default": ""})
    spec["default"] = str(value).strip()
    if spec["default"]:
        remember(data, name, spec["default"])


def add_value(data: dict, name: str, value: str) -> bool:
    """Add a value to what a placeholder offers (in advance of any proposal). False when it is empty or already there."""
    value = str(value).strip()
    if not value or value in data["values"].get(name, []):
        return False
    data["values"].setdefault(name, []).append(value)            # at the end: the newest typed in a proposal stay on top
    data["values"][name] = data["values"][name][:MAX_REMEMBERED]
    return True


def move_value(data: dict, name: str, value: str, delta: int) -> bool:
    vals = data["values"].get(name, [])
    if value not in vals:
        return False
    i, j = vals.index(value), vals.index(value) + delta
    if not 0 <= j < len(vals):
        return False
    vals[i], vals[j] = vals[j], vals[i]
    return True


def is_optional(name: str) -> bool:
    """A placeholder that may stay empty (it is then replaced by nothing, not reported as missing)."""
    return bool(PLACEHOLDERS.get(name, {}).get("optional"))


def render(text: str, values: dict) -> str:
    """The text with every {name} replaced by its value; a name without a value stays as it is (an optional one
    becomes nothing)."""
    def one(m):
        v = values.get(m.group(1))
        if v not in (None, ""):
            return str(v)
        return "" if is_optional(m.group(1)) else m.group(0)
    return _PLACEHOLDER.sub(one, text)


def missing(text: str, values: dict) -> list[str]:
    return [p for p in placeholders_of(text) if values.get(p) in (None, "") and not is_optional(p)]


# ── work schedules ───────────────────────────────────────────────────────────

def all_schedules(data: dict) -> list[dict]:
    """The four shipped schedules, then the user's own."""
    return [copy.deepcopy(s) for s in SHIPPED_SCHEDULES] + [copy.deepcopy(s) for s in data.get("schedules", [])]


def schedule_by_id(data: dict, sid: str | None) -> dict | None:
    return next((s for s in all_schedules(data) if s["id"] == sid), None)


def is_shipped_schedule(sid: str) -> bool:
    return any(s["id"] == sid for s in SHIPPED_SCHEDULES)


def add_schedule(data: dict, label: str, values: dict) -> dict:
    """A schedule of your own; returns it. Values are the SCHEDULE_KEYS (a missing one is empty)."""
    label = label.strip()
    if not label:
        raise ValueError("A schedule needs a name.")
    taken = {s["id"] for s in all_schedules(data)}
    sc = {"id": new_id(taken), "label": label, "values": {k: str(values.get(k, "")) for k in SCHEDULE_KEYS}}
    data.setdefault("schedules", []).append(sc)
    return sc


def delete_schedule(data: dict, sid: str) -> bool:
    """Delete a schedule of your own (a shipped one cannot be deleted)."""
    before = len(data.get("schedules", []))
    data["schedules"] = [s for s in data.get("schedules", []) if s["id"] != sid]
    return len(data["schedules"]) != before


def uses_schedule(text: str) -> bool:
    """Does a note text use a placeholder that a schedule sets (working days, hours, overtime …)?"""
    return any(p in SCHEDULE_KEYS for p in placeholders_of(text))


def initial_value(data: dict, name: str, auto: dict | None = None) -> str:
    """The value a placeholder starts with: what the app knows (auto), else the starting value the user set, else the one
    used last, else the app's own default."""
    if auto and auto.get(name) not in (None, ""):
        return str(auto[name])
    own = data.get("placeholders", {}).get(name, {}).get("default")
    if own:                                    # a starting value the user set wins over the one used last
        return str(own)
    if data["values"].get(name):
        return data["values"][name][0]
    return default_of(data, name)


def remember(data: dict, name: str, value: str) -> None:
    """Keep a value the user typed so it can be chosen again (newest first, no repeats, a limit)."""
    value = str(value).strip()
    if not value:
        return
    kept = [v for v in data["values"].get(name, []) if v != value]
    data["values"][name] = ([value] + kept)[:MAX_REMEMBERED]


def forget(data: dict, name: str, value: str) -> None:
    data["values"][name] = [v for v in data["values"].get(name, []) if v != value]
    if not data["values"][name]:
        del data["values"][name]


def suggest(data: dict, name: str, typed: str, limit: int = 8) -> list[str]:
    """The remembered values for `name` ordered by how close they are to what is being typed: those that start
    with it, then those that contain it, then the nearest spellings; with nothing typed, the newest first. The
    default of a known placeholder counts as remembered."""
    pool = list(data["values"].get(name, []))
    default = default_of(data, name)
    if default and default not in pool:
        pool.append(default)
    t = (typed or "").strip().lower()
    if not t:
        return pool[:limit]
    starts = [v for v in pool if v.lower().startswith(t)]
    inside = [v for v in pool if t in v.lower() and v not in starts]
    rest = [v for v in pool if v not in starts and v not in inside]
    near = sorted(rest, key=lambda v: -difflib.SequenceMatcher(None, t, v.lower()).ratio())
    near = [v for v in near if difflib.SequenceMatcher(None, t, v.lower()).ratio() >= 0.5]
    return (starts + inside + near)[:limit]


def search(data: dict, query: str) -> list[dict]:
    """The notes whose text contains every word of the query (any case), for the search bar."""
    words = (query or "").lower().split()
    return [n for n in data["notes"] if all(w in (n["text"] + " " + n.get("hint", "")).lower() for w in words)]


def copy_of(data: dict) -> dict:
    return copy.deepcopy(data)
