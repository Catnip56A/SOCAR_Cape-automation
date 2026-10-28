#!/usr/bin/env python3
"""
ctr_tools/cbar_rates.py

Fetches daily official currency exchange rates published by the Central
Bank of the Republic of Azerbaijan (CBAR) and saves the latest snapshot as
JSON in the app's own per-user data directory (see ctr_tools/paths.py) —
the same directory match_aliases.json / desc_renames.json / ctr_presets.json
already live in.

Not wired into the app yet — standalone for now, moved here (out of
TEST_Files/) specifically so it isn't forgotten. The plan for a later release is to
call fetch_and_save() on a periodic timer from inside the running app,
rather than running this file directly.

CBAR publishes a daily XML file at a predictable URL:
    https://www.cbar.az/currencies/DD.MM.YYYY.xml

fetch_and_save() (also runnable standalone, see USAGE below):
  1. Downloads that day's XML (defaults to today; falls back to the most
     recent day with published rates if today's isn't up yet — CBAR
     doesn't always publish same-day).
  2. Parses it into {currency_code: {"name", "nominal", "value"}}.
  3. Overwrites cbar_rates.json in the app's data directory with the
     latest snapshot (what the app starts from), and adds the same day to
     cbar_rates_log.json — one entry per CBAR date, kept for good, so a
     past rate can be looked up and checked (see rate_on / usd_history).
     cbar_usage_log.json records which documents used which rate.

USAGE (standalone, for manual testing)
---------------------------------------
    python3 -m ctr_tools.cbar_rates                  # fetch latest, save + print
    python3 -m ctr_tools.cbar_rates --date 15.09.2026
    python3 -m ctr_tools.cbar_rates --currencies USD EUR GBP TRY
    python3 -m ctr_tools.cbar_rates --json           # print JSON instead of a table
    python3 -m ctr_tools.cbar_rates --no-save        # print only, don't touch the JSON file
    python3 -m ctr_tools.cbar_rates --history        # the logged USD rates (add --date DD.MM.YYYY for one day)
    python3 -m ctr_tools.cbar_rates --validate       # check the whole log for values that look wrong
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

if __package__ is None:
    # Run directly as a plain script (`python ctr_tools/cbar_rates.py`)
    # rather than imported or run via `python -m` — Python then only puts
    # this file's own directory (ctr_tools/) on sys.path, so `ctr_tools` as
    # a package can't be found from inside itself. Add the project root
    # (this file's grandparent) so the import below resolves either way.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ctr_tools.paths import user_data_dir

log = logging.getLogger(__name__)

BASE_URL = "https://www.cbar.az/currencies/{date}.xml"
DEFAULT_CURRENCIES = ["USD", "EUR", "GBP", "TRY", "RUB"]

_RATES_PATH = user_data_dir() / "cbar_rates.json"
_LOG_PATH   = user_data_dir() / "cbar_rates_log.json"      # {"rates": {"YYYY-MM-DD": {code: {...}}}}
_USAGE_PATH = user_data_dir() / "cbar_usage_log.json"      # [{"used_at", "rates", ...}]
_CURRENCIES_PATH = user_data_dir() / "cbar_currencies.json"   # which currencies the user turned on

DEFAULT_ENABLED = ["USD", "EUR", "GBP"]       # AZN is always there (rate 1) and is not listed
JUMP_WARN_OTHER_PCT = 5.0                     # one day to the next, any currency but USD

# The manat is pegged to the dollar (1.70 since 2017); anything far from it is almost certainly a
# slip (a comma read as a thousands separator, 17.0 for 1.70, a swapped pair), not a devaluation.
USD_PLAUSIBLE = (1.60, 1.80)
JUMP_WARN_PCT = 2.0          # one day to the next
TYPED_WARN_PCT = 3.0         # a typed rate against the latest logged one


def fetch_xml_for_date(date_obj: datetime) -> str:
    """Download the raw XML for a given date. Raises on HTTP error."""
    url = BASE_URL.format(date=date_obj.strftime("%d.%m.%Y"))
    req = urllib.request.Request(url, headers={"User-Agent": "cbar-rates-script/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


def fetch_latest(max_days_back: int = 5):
    """
    Try today, then walk backwards up to `max_days_back` days until a
    published XML file is found (CBAR has no file for some days, e.g.
    holidays). Returns (date_obj, xml_text).
    """
    day = datetime.now()
    last_error = None
    for _ in range(max_days_back + 1):
        try:
            xml_text = fetch_xml_for_date(day)
            return day, xml_text
        except urllib.error.HTTPError as e:
            last_error = e
            day -= timedelta(days=1)
        except urllib.error.URLError as e:
            # Network problem — no point walking further back.
            raise RuntimeError(f"Could not reach CBAR: {e}") from e
    raise RuntimeError(
        f"No published rates found in the last {max_days_back} days "
        f"(last error: {last_error})"
    )


def parse_rates(xml_text: str) -> dict:
    """Parse CBAR's XML into {code: {'name': str, 'nominal': int, 'value': float}}."""
    root = ET.fromstring(xml_text)
    rates = {}
    for valute in root.iter("Valute"):
        code = valute.get("Code")
        name_el = valute.find("Name")
        nominal_el = valute.find("Nominal")
        value_el = valute.find("Value")
        if code and value_el is not None:
            # Most nominals are a plain integer ("1", "100"), but precious
            # metals (XAU, XAG, XPT, XPD) use "1 t.u." (troy unit) instead —
            # take the leading number and drop whatever unit text follows.
            nominal = 1
            if nominal_el is not None and nominal_el.text:
                match = re.match(r"\d+", nominal_el.text.strip())
                if match:
                    nominal = int(match.group())
            # One unreadable value (a letter, an empty tag) must not lose the other currencies:
            # it is kept as None and reported by the validation for that currency alone.
            try:
                value = float((value_el.text or "").strip().replace(",", "."))
            except ValueError:
                value = None
            rates[code] = {
                "name": name_el.text if name_el is not None else "",
                "nominal": nominal,
                "value": value,
            }
    return rates


def save_rates_json(date_obj: datetime, rates: dict, currencies: list,
                    snapshot_codes: list | None = None) -> None:
    """Save a fetched list. cbar_rates.json (what the app starts from) takes the rates in
    `snapshot_codes` (default all) and keeps its previous entry for every other currency; each
    entry carries its own day and check time. Everything published is also appended to
    cbar_rates_log.json — one entry per CBAR day, kept for good (see _append_log)."""
    iso = date_obj.strftime("%Y-%m-%d")
    payload = {
        "date": iso,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "rates": {code: rates[code] for code in currencies if code in rates},
    }
    # The snapshot is what the app starts from, so it only takes rates that passed the checks
    # (snapshot_codes; default all). Every other currency keeps its previous entry, and each entry
    # carries its own day. The log below takes everything that was published.
    snap_codes = set(payload["rates"] if snapshot_codes is None else snapshot_codes)
    try:
        old = json.loads(_RATES_PATH.read_text(encoding="utf-8")).get("rates", {})
    except (OSError, ValueError, AttributeError):
        old = {}
    snap = {c: v for c, v in old.items() if isinstance(v, dict)}
    for code, info in payload["rates"].items():
        if code in snap_codes:
            snap[code] = {**info, "date": iso, "checked_at": payload["fetched_at"]}
    try:
        _RATES_PATH.write_text(
            json.dumps({**payload, "rates": snap}, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        _append_log(payload)
    except OSError as exc:
        # non-fatal — the rates just won't persist for the app to read —
        # but worth a trail, matching how presets.py/aliases.py treat a
        # failed save of their own data directory JSON.
        log.warning("Failed to save %s: %s", _RATES_PATH, exc)


def _write_json_atomic(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_log() -> dict:
    """{"YYYY-MM-DD": {code: {"name", "nominal", "value"}}} — every day ever fetched.
    Empty when there is no log or it cannot be read."""
    try:
        return dict(json.loads(_LOG_PATH.read_text(encoding="utf-8")).get("rates", {}))
    except (OSError, ValueError, AttributeError):
        return {}


def _append_log(payload: dict) -> None:
    """Add (or, for the same CBAR day, refresh) one day in the log."""
    log_rates = load_log()
    log_rates[payload["date"]] = payload["rates"]
    _write_json_atomic(_LOG_PATH, {"rates": dict(sorted(log_rates.items()))})


def usd_history() -> list[tuple[str, float]]:
    """[(date, AZN per USD)] oldest first, from the log."""
    return history("USD")


def rate_on(day: str) -> float | None:
    """AZN per USD that CBAR published for `day` ("YYYY-MM-DD"), from the log; None if that
    day was never fetched (fetch_and_save("DD.MM.YYYY") adds it)."""
    return dict(usd_history()).get(day)


def fetch_and_save(date_str: str | None = None, currencies: list | None = None) -> dict:
    """
    Fetch CBAR rates (for `date_str` as DD.MM.YYYY, or the latest available
    if omitted), save them to cbar_rates.json, and return the parsed rates
    dict for the requested `currencies` (default: DEFAULT_CURRENCIES).

    This is the entry point v1.0.15's periodic caller will use — no CLI
    parsing, just fetch + persist.
    """
    currencies = currencies or DEFAULT_CURRENCIES
    if date_str:
        day = datetime.strptime(date_str, "%d.%m.%Y")
        xml_text = fetch_xml_for_date(day)
    else:
        day, xml_text = fetch_latest()

    rates = parse_rates(xml_text)
    save_rates_json(day, rates, currencies)
    return {c: rates[c] for c in currencies if c in rates}


def _usd_per_unit(rates: dict) -> float:
    """AZN for one USD from a parsed rates dict (CBAR quotes some currencies per 100)."""
    usd = rates["USD"]
    return usd["value"] / (usd["nominal"] or 1)


def saved_usd_rate() -> tuple[float, str] | None:
    """(AZN per USD, "YYYY-MM-DD") from the last saved snapshot, or None. No network."""
    return saved_rate("USD")


def fetch_usd_rate() -> tuple[float, str, list[str]]:
    """The USD rate only: (AZN per USD, "YYYY-MM-DD", warnings); see fetch_rates. An unusable
    list raises ValueError. Raises RuntimeError / OSError when CBAR cannot be reached."""
    res = fetch_rates(["USD"])["USD"]
    if res["error"]:
        raise ValueError(res["error"])
    return res["rate"], res["date"], res["warnings"]


# ── Several currencies ────────────────────────────────────────────────────────
# The CBAR list holds ~40 currencies; the log keeps all of them. Which ones the PDMS proposal
# offers is the user's choice (Settings): USD, EUR and GBP by default, AZN always (rate 1).

CURRENCY_SIGNS = {"AZN": "₼", "USD": "$", "EUR": "€", "GBP": "£", "TRY": "₺", "RUB": "₽",
                  "JPY": "¥", "CNY": "¥", "INR": "₹", "KRW": "₩", "CHF": "CHF", "GEL": "₾", "KZT": "₸",
                  "UAH": "₴", "ILS": "₪"}


def currency_sign(code: str) -> str:
    """The sign a table shows for a currency (₼ $ € £ …); a currency without a common sign shows its code."""
    code = (code or "").upper()
    return CURRENCY_SIGNS.get(code, code)


def per_unit(rates: dict, code: str) -> float:
    """AZN for one unit of `code` from a parsed rates dict (some are quoted per 100 or 1000)."""
    info = rates[code]
    return info["value"] / (info["nominal"] or 1)


def enabled_currencies() -> list[str]:
    """The currencies turned on in Settings (never AZN)."""
    try:
        codes = json.loads(_CURRENCIES_PATH.read_text(encoding="utf-8"))
        if isinstance(codes, list) and all(isinstance(c, str) for c in codes):
            return [c for c in dict.fromkeys(c.upper() for c in codes) if c and c != "AZN"]
    except (OSError, ValueError):
        pass
    return list(DEFAULT_ENABLED)


def set_enabled_currencies(codes: list[str]) -> None:
    try:
        _write_json_atomic(_CURRENCIES_PATH, [c.upper() for c in codes if c and c.upper() != "AZN"])
    except OSError as exc:
        log.warning("Failed to save %s: %s", _CURRENCIES_PATH, exc)


def available_currencies() -> list[tuple[str, str]]:
    """[(code, name)] of every currency CBAR published in the newest logged list, plus the
    ones turned on (so a turned-on currency never disappears from the list). No network."""
    names: dict[str, str] = {}
    log_rates = load_log()
    if log_rates:
        for code, info in log_rates[max(log_rates)].items():
            names[code] = str(info.get("name", ""))
    for code in enabled_currencies():
        names.setdefault(code, "")
    return sorted(names.items())


def fetch_available() -> list[tuple[str, str]]:
    """Download the latest list (saving it) and return every currency CBAR publishes."""
    day, xml_text = fetch_latest()
    rates = parse_rates(xml_text)
    save_rates_json(day, rates, list(rates))
    return available_currencies()


def history(code: str) -> list[tuple[str, float]]:
    """[(date, AZN per unit)] of one currency, oldest first, from the log."""
    out = []
    for day, rates in load_log().items():
        try:
            out.append((day, per_unit(rates, code)))
        except (KeyError, TypeError, ZeroDivisionError):
            continue
    return sorted(out)


def saved_rate(code: str) -> tuple[float, str] | None:
    """(AZN per unit, "YYYY-MM-DD") of `code` from the last saved snapshot; None if unknown.
    No network."""
    try:
        data = json.loads(_RATES_PATH.read_text(encoding="utf-8"))
        info = data["rates"][code]
        return per_unit(data["rates"], code), str(info.get("date") or data["date"])
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
        return None


def accept_snapshot(code: str, day: str) -> bool:
    """The user approved a rate that looked wrong: make the logged `day` the saved rate of `code`
    (what the app starts from). False when that day / currency is not in the log."""
    info = load_log().get(day, {}).get(code)
    if not info:
        return False
    try:
        data = json.loads(_RATES_PATH.read_text(encoding="utf-8"))
        snap = {c: v for c, v in data.get("rates", {}).items() if isinstance(v, dict)}
    except (OSError, ValueError, AttributeError):
        data, snap = {}, {}
    snap[code] = {**info, "date": day, "checked_at": datetime.now().isoformat(timespec="seconds")}
    try:
        _write_json_atomic(_RATES_PATH, {**data, "date": day, "rates": snap})
    except OSError as exc:
        log.warning("Failed to save %s: %s", _RATES_PATH, exc)
        return False
    return True


def saved_checked_at(code: str) -> str | None:
    """When the saved rate of `code` was last fetched ("YYYY-MM-DDTHH:MM:SS"), or None. No network."""
    try:
        data = json.loads(_RATES_PATH.read_text(encoding="utf-8"))
        return str(data["rates"][code].get("checked_at") or data.get("fetched_at") or "") or None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def validate_currency(code: str, rates: dict, day: datetime | None = None,
                      previous: float | None = None) -> tuple[list[str], list[str]]:
    """(errors, warnings) for one currency of a parsed list. USD has its own range checks
    (validate_rates); any other must be a positive number and stay within JUMP_WARN_OTHER_PCT of
    the previous logged day."""
    if code == "USD":
        return validate_rates(rates, day=day, previous=previous)
    info = rates.get(code)
    if not info:
        return [f"CBAR's list has no {code} entry."], []
    try:
        value, nominal = float(info["value"]), int(info.get("nominal") or 0)
    except (TypeError, ValueError):
        return [f"The {code} value could not be read as a number."], []
    if nominal <= 0 or not value > 0:
        return [f"The {code} entry is unusable (value {info.get('value')}, nominal {info.get('nominal')})."], []
    rate = value / nominal
    warnings = []
    if previous and abs(rate - previous) / previous * 100 > JUMP_WARN_OTHER_PCT:
        warnings.append(f"{code} rate {rate:.4f} differs from the previous logged {previous:.4f} "
                        f"by more than {JUMP_WARN_OTHER_PCT:g}%.")
    return [], warnings


def fetch_rates(codes: list[str] | None = None) -> dict[str, dict]:
    """
    Download the latest CBAR list once, log it, and return for each wanted currency
    {"rate", "date", "warnings", "error"} (rate is None when error is set). `codes` defaults to
    the turned-on currencies. A currency whose entry is unusable is left out of the log; one that
    only looks wrong is logged (it is what CBAR published) and comes back with its warnings.
    Raises RuntimeError / OSError when CBAR cannot be reached.
    """
    codes = list(codes) if codes else enabled_currencies()
    day, xml_text = fetch_latest()
    rates = parse_rates(xml_text)
    iso = day.strftime("%Y-%m-%d")
    out: dict[str, dict] = {}
    for code in codes:
        before = [v for d, v in history(code) if d < iso]
        errors, warnings = validate_currency(code, rates, day=day, previous=before[-1] if before else None)
        if errors:
            out[code] = {"rate": None, "date": iso, "warnings": warnings, "error": " ".join(errors)}
            rates.pop(code, None)
        else:
            out[code] = {"rate": per_unit(rates, code), "date": iso, "warnings": warnings, "error": None}
    save_rates_json(day, rates, list(rates),
                    snapshot_codes=[c for c, r in out.items() if r["rate"] is not None and not r["warnings"]]
                    + [c for c in rates if c not in out])
    return out


# ── Which documents used which rate ───────────────────────────────────────────

def load_usage() -> list[dict]:
    try:
        data = json.loads(_USAGE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def log_usage(document: str, prp: str, rates: dict[str, tuple]) -> None:
    """Record that `document` was generated with these rates: {code: (rate, CBAR day or None when
    typed[, source])}. `source` says "default" for the built-in rate that was never fetched; without
    it the source is "CBAR" (a day is given) or "typed". The USD entry is repeated at the top level
    (rate / rate_date / source) as older entries and the Inspector's first columns carry it. Never
    raises: the log is a convenience."""
    def _src(day, extra):
        return extra or ("CBAR" if day else "typed")
    entry = {"used_at": datetime.now().isoformat(timespec="seconds"), "document": document, "prp": prp,
             "rates": {c: {"rate": v[0], "rate_date": v[1], "source": _src(v[1], v[2] if len(v) > 2 else None)}
                       for c, v in rates.items()}}
    if "USD" in rates:
        v = rates["USD"]
        entry.update({"rate": v[0], "rate_date": v[1], "source": _src(v[1], v[2] if len(v) > 2 else None)})
    try:
        _write_json_atomic(_USAGE_PATH, load_usage() + [entry])
    except OSError as exc:
        log.warning("Failed to save %s: %s", _USAGE_PATH, exc)


# ── Validation: flag what looks like a typo or a parsing slip ─────────────────

def parse_typed_rate(text: str) -> float | None:
    """A typed rate as a number ("1,7" and "1.7" both work), or None when it is not one."""
    try:
        value = float(str(text).strip().replace(",", "."))
    except ValueError:
        return None
    return value if value == value and value not in (float("inf"), float("-inf")) else None


def validate_rates(rates: dict, day: datetime | None = None, requested: datetime | None = None,
                   previous: float | None = None) -> tuple[list[str], list[str]]:
    """
    Check one parsed CBAR list. Returns (errors, warnings).
    errors   — the list cannot be used (no USD, not a positive number, nominal 0): not logged.
    warnings — it can be read but looks wrong (outside USD_PLAUSIBLE, a jump from `previous`,
               a different day from the one asked for, a cross-rate that looks like a swapped pair).
    """
    errors, warnings = [], []
    usd = rates.get("USD")
    if not usd:
        return ["The list has no USD entry — the file may be in a new format."], warnings
    try:
        value, nominal = float(usd["value"]), int(usd.get("nominal") or 0)
    except (TypeError, ValueError):
        return ["The USD value could not be read as a number."], warnings
    if nominal <= 0 or not value > 0:
        return [f"The USD entry is unusable (value {usd.get('value')}, nominal {usd.get('nominal')})."], warnings
    rate = value / nominal
    if nominal != 1:
        warnings.append(f"USD nominal is {nominal}, not 1 — the rate was divided by it ({rate:.4f}); check the file.")
    lo, hi = USD_PLAUSIBLE
    if not lo <= rate <= hi:
        warnings.append(f"USD rate {rate:.4f} is outside the usual {lo:.2f}–{hi:.2f} range "
                        "(a decimal slip or a parsing error?).")
    if previous and abs(rate - previous) / previous * 100 > JUMP_WARN_PCT:
        warnings.append(f"USD rate {rate:.4f} differs from the previous logged {previous:.4f} "
                        f"by more than {JUMP_WARN_PCT:g}%.")
    if day and requested and day.date() != requested.date():
        warnings.append(f"CBAR returned {day:%d.%m.%Y} for the requested {requested:%d.%m.%Y}.")
    eur = rates.get("EUR")
    if eur:
        try:
            ratio = (float(eur["value"]) / (eur.get("nominal") or 1)) / rate
        except (TypeError, ValueError, ZeroDivisionError):
            ratio = None
        if ratio is not None and not 0.8 <= ratio <= 1.5:
            warnings.append(f"EUR/USD works out to {ratio:.2f} — one of the two looks wrong.")
    return errors, warnings


def validate_typed_rate(text: str, reference: tuple[float, str] | None = None) -> list[str]:
    """Warnings for a rate typed by hand (never a block — any rate may be used). `reference` is
    (rate, date) of the latest logged day."""
    value = parse_typed_rate(text)
    if value is None:
        return [f"“{text}” is not a number."]
    if value <= 0:
        return ["The rate must be positive."]
    out = []
    lo, hi = USD_PLAUSIBLE
    if not lo <= value <= hi:
        out.append(f"{value:g} is outside the usual USD→AZN range {lo:.2f}–{hi:.2f}.")
    elif reference and abs(value - reference[0]) / reference[0] * 100 > TYPED_WARN_PCT:
        out.append(f"{value:g} differs by more than {TYPED_WARN_PCT:g}% from CBAR's {reference[0]:.4f} "
                   f"of {reference[1]}.")
    return out


def validate_log() -> list[str]:
    """Every problem found in the log (USD and the turned-on currencies), as 'DATE: message'
    lines; empty = nothing found."""
    out = []
    previous: dict[str, float] = {}
    for day, rates in sorted(load_log().items()):
        for code in dict.fromkeys(["USD", *enabled_currencies()]):
            if code not in rates:
                continue
            errors, warnings = validate_currency(code, rates, previous=previous.get(code))
            out += [f"{day}: ERROR {m}" for m in errors] + [f"{day}: {m}" for m in warnings]
            if not errors:
                previous[code] = per_unit(rates, code)
    return out


def main():
    parser = argparse.ArgumentParser(description="Fetch daily CBAR currency rates.")
    parser.add_argument("--date", help="Specific date as DD.MM.YYYY (default: latest available)")
    parser.add_argument(
        "--currencies", nargs="+", default=DEFAULT_CURRENCIES,
        help=f"Currency codes to show/save (default: {' '.join(DEFAULT_CURRENCIES)})",
    )
    parser.add_argument(
        "--no-save", action="store_true", help="Don't write cbar_rates.json"
    )
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table")
    parser.add_argument("--history", action="store_true",
                        help="Show the logged USD rates (with --date DD.MM.YYYY: that one day)")
    parser.add_argument("--validate", action="store_true",
                        help="Check the whole log for values that look wrong")
    parser.add_argument("--list", action="store_true",
                        help="List every currency CBAR publishes (fetches the latest list)")
    parser.add_argument("--currency", default="USD", help="Currency for --history (default USD)")
    args = parser.parse_args()

    if args.list:
        try:
            for code, name in fetch_available():
                print(f"{code:<5} {name}")
        except Exception as e:
            print(f"Error fetching the list: {e}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)

    if args.validate:
        problems = validate_log()
        print("\n".join(problems) if problems else f"No problems found in {len(load_log())} logged day(s).")
        sys.exit(1 if problems else 0)
    if args.history:
        hist = history(args.currency.upper())
        if args.date:
            iso = datetime.strptime(args.date, "%d.%m.%Y").strftime("%Y-%m-%d")
            hist = [h for h in hist if h[0] == iso]
            if not hist:
                print(f"{args.date} is not in the log — run with --date {args.date} (no --history) to fetch it.")
                sys.exit(1)
        for day, value in hist:
            print(f"{day}  {args.currency.upper()} {value:.4f}")
        sys.exit(0)

    try:
        if args.date:
            day = datetime.strptime(args.date, "%d.%m.%Y")
            xml_text = fetch_xml_for_date(day)
        else:
            day, xml_text = fetch_latest()
    except Exception as e:
        print(f"Error fetching rates: {e}", file=sys.stderr)
        sys.exit(1)

    rates = parse_rates(xml_text)

    if args.json:
        print(json.dumps(
            {"date": day.strftime("%Y-%m-%d"),
             "rates": {c: rates[c] for c in args.currencies if c in rates}},
            indent=2, ensure_ascii=False,
        ))
    else:
        print(f"CBAR official rates for {day.strftime('%Y-%m-%d')} (AZN per unit):")
        print("-" * 46)
        for code in args.currencies:
            info = rates.get(code)
            if info:
                print(f"  {code:<5} {info['nominal']:>3} {info['name']:<20} {info['value']:.4f}")
            else:
                print(f"  {code:<5} not found in today's list")
        print("-" * 46)

    if not args.no_save:
        save_rates_json(day, rates, list(rates))
        print(f"Saved to {_RATES_PATH}")


if __name__ == "__main__":
    main()
