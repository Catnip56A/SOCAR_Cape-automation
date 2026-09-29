#!/usr/bin/env python3
"""
ctr_tools/cbar_rates.py

Fetches daily official currency exchange rates published by the Central
Bank of the Republic of Azerbaijan (CBAR) and saves the latest snapshot as
JSON in the app's own per-user data directory (see ctr_tools/paths.py) —
the same directory match_aliases.json / desc_renames.json / ctr_presets.json
already live in.

Not wired into the app yet — standalone for now, moved here (out of
TEST_Files/) specifically so it isn't forgotten. The plan for v1.0.15 is to
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
     latest snapshot. Not a running log — each call replaces whatever was
     saved before, since a stale rate is wrong information rather than
     history worth keeping.

USAGE (standalone, for manual testing)
---------------------------------------
    python3 -m ctr_tools.cbar_rates                  # fetch latest, save + print
    python3 -m ctr_tools.cbar_rates --date 15.09.2026
    python3 -m ctr_tools.cbar_rates --currencies USD EUR GBP TRY
    python3 -m ctr_tools.cbar_rates --json           # print JSON instead of a table
    python3 -m ctr_tools.cbar_rates --no-save        # print only, don't touch the JSON file
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
            rates[code] = {
                "name": name_el.text if name_el is not None else "",
                "nominal": nominal,
                "value": float(value_el.text),
            }
    return rates


def save_rates_json(date_obj: datetime, rates: dict, currencies: list) -> None:
    """Overwrite the app's cbar_rates.json with the latest snapshot — the
    selected currencies' rates as of date_obj. Not a running log: each
    call replaces whatever was saved before, since a stale rate is wrong
    information rather than history worth keeping."""
    payload = {
        "date": date_obj.strftime("%Y-%m-%d"),
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "rates": {code: rates[code] for code in currencies if code in rates},
    }
    try:
        _RATES_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        # non-fatal — the rates just won't persist for the app to read —
        # but worth a trail, matching how presets.py/aliases.py treat a
        # failed save of their own data directory JSON.
        log.warning("Failed to save %s: %s", _RATES_PATH, exc)


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
    args = parser.parse_args()

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
        save_rates_json(day, rates, args.currencies)
        print(f"Saved to {_RATES_PATH}")


if __name__ == "__main__":
    main()
