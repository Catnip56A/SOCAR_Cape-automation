"""Maintain Task_Log_MagsudAbbaszade.xlsx (used by the /handoff command).

    python3 .claude/task_log/task_log_append.py missing  [--xlsx PATH]
    python3 .claude/task_log/task_log_append.py open     [--xlsx PATH]
    python3 .claude/task_log/task_log_append.py add FILE.json [--xlsx PATH]
    python3 .claude/task_log/task_log_append.py update "X - MA005" [--pct 100] [--remaining TEXT]
                                              [--comment TEXT] [--phase VALUE] [--version 1.0.20]
                                              [--start YYYY-MM-DD] [--completion YYYY-MM-DD] [--xlsx PATH]
    python3 .claude/task_log/task_log_append.py update-many FILE.json [--xlsx PATH]
    python3 .claude/task_log/task_log_append.py drop-column "Total Units" [--xlsx PATH]
    python3 .claude/task_log/task_log_append.py replace-text "OLD" "NEW" [--xlsx PATH]
    python3 .claude/task_log/task_log_append.py delete-row "X - MA176" [--xlsx PATH]
    python3 .claude/task_log/task_log_append.py refresh  [--xlsx PATH]

missing  changelog versions that have no row in the log yet (version + date), then
         "version<TAB>id<TAB>value" for every row whose Version is not a release number
         (Planned / Unreleased are not used any more), then
         "summary<TAB>key" for each Version|App Part group with no text in short_summaries.json
open     rows whose Completion % < 100 (a note in Remaining on a 100% row is a
         known limitation or idea, not unfinished work, so it is not listed)
add      append rows; FILE.json is a list of objects with keys
         version (a release number such as 1.0.20 - the release the work is for, planned work
         included; default: the number in VERSION), part, name, functionality, priority,
         pct (0-100), remaining, comment, phase (default 0; e.g. 1, "1A"),
         completed (default true; false = not finished: Completion Date left blank),
         start / completion (optional YYYY-MM-DD; completion default: today when completed)
         Start Date is always filled: the day the row is logged, unless "start" is given.
update   change Completion % / Remaining / Comments / Phase / Version / dates of one existing row.
         Always: a blank Start Date is filled with today; reaching 100 % fills a blank
         Completion Date with today; going below 100 % clears the Completion Date.
update-many  the same for several rows in ONE write (one backup): FILE.json is a list of objects
         with "id" and any of version, pct, remaining, comment, phase, start, completion
drop-column  remove a column that is not required (e.g. "Total Units"); backs up first
replace-text replace OLD with NEW in the Task Name, Remaining and Comments of every row (e.g. to
         keep a person's name out of the log); sheets 2 and 3 are rebuilt
delete-row   remove one row for good (only when the user asks) and move every higher id of the same
         prefix down by one, so the numbering stays gapless; sheets 2 and 3 are rebuilt. Make a copy of the
         file first: the per-release backup is not taken again when one already exists
refresh  rebuild sheets 2 and 3 only (use after editing short_summaries.json)
Sheet 3 ("Shortened Form") has one row per Version x App Part: a one-line plain-language summary
(from short_summaries.json, key "Version|App Part") plus figures computed from sheet 1.
Columns are found by their header text in row 1, so the user may delete or move
columns (e.g. the spare "Col N" ones); only the headers listed in NEEDED must exist.
Existing rows are never rewritten except by `update`. Sheets 2 ("By App Part") and 3
are rebuilt from sheet 1 after every change. Close the file in Excel first.
Writes go through a temp file and an atomic swap. Backups: ONE per release, not one per write - before the first write
under a new number in VERSION the file is copied to .claude/task_log/backups/<name>_start_of_<version>.xlsx (= the log as it
was at the end of the previous version); later writes of that version make no further copy.
"""
import argparse
import shutil
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_XLSX = ROOT / "Task_Log_MagsudAbbaszade.xlsx"
CHANGELOG = ROOT / "CHANGELOG.md"
BACKUP_DIR = Path(__file__).resolve().parent / "backups"
S1, S2, S3 = "All Tasks", "By App Part", "Shortened Form"
SUMMARIES = Path(__file__).resolve().parent / "short_summaries.json"
RANK = {"High": 3, "Medium": 2, "Low": 1}
PARTS = ["CTR Generator", "MR vs CTR Comparator", "CTR Tracker", "App-wide / Infrastructure"]
NEEDED = ["Task ID", "Task Name", "Functionality", "Version", "App Part", "Start Date", "Completion Date",
          "Deadline", "Priority (importance)", "Completion %", "Remaining", "Comments"]
CENTER = {"Task ID", "Version", "Start Date", "Completion Date", "Deadline", "Priority (importance)",
          "Completion %", "Phase"}
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def parse_date(text, what):
    try:
        return datetime.strptime(str(text), "%Y-%m-%d").date()
    except ValueError:
        sys.exit(f"{what}: expected YYYY-MM-DD, got {text!r}")


def to_date(v):
    """Excel hands back datetimes, the script writes dates: compare them as dates."""
    return v.date() if isinstance(v, datetime) else v


def check_version(v, what="version"):
    if not VERSION_RE.match(str(v)):
        sys.exit(f"{what}: {v!r} is not a release number like 1.0.20 ('Planned'/'Unreleased' are not used)")
    return str(v)


def current_version():
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()
WRAP = {"Task Name", "Functionality", "App Part", "Remaining", "Comments"}

_thin = Side(style="thin", color="BFBFBF")
BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)


def changelog_versions():
    out = {}
    for m in re.finditer(r"^## \[([\d.]+)\] - (\d{4}-\d{2}-\d{2})", CHANGELOG.read_text(encoding="utf-8"), re.M):
        out[m.group(1)] = datetime.strptime(m.group(2), "%Y-%m-%d").date()
    return out


class Layout:
    """Header-driven view of sheet 1."""

    def __init__(self, ws):
        self.head = []
        for c in range(1, ws.max_column + 1):
            v = ws.cell(1, c).value
            if v is None:
                break
            self.head.append(str(v))
        self.n = len(self.head)
        self.col = {h: i + 1 for i, h in enumerate(self.head)}
        missing = [h for h in NEEDED if h not in self.col]
        if missing:
            sys.exit(f"Task log is missing column(s) {missing}; found {self.head}")
        self.widths = [ws.column_dimensions[get_column_letter(c)].width or 12 for c in range(1, self.n + 1)]

    def spare(self, c):
        return self.head[c - 1].startswith("Col ")


def data_rows(ws):
    return [r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value]


def style_row(ws, r, L):
    for c, h in enumerate(L.head, 1):
        cell = ws.cell(r, c)
        cell.border = BORDER
        cell.alignment = Alignment(vertical="top", wrap_text=h in WRAP, horizontal="center" if h in CENTER else None)
        if h in ("Start Date", "Completion Date"):
            cell.number_format = "yyyy-mm-dd"
        if h == "Completion %":
            cell.number_format = "0%"
        if L.spare(c):
            cell.fill = PatternFill("solid", fgColor="F2F2F2")


def style_header(ws, row, L):
    for c in range(1, L.n + 1):
        cell = ws.cell(row, c)
        spare = L.spare(c)
        cell.font = Font(bold=True, color="7F7F7F" if spare else "FFFFFF", size=8 if spare else 11)
        cell.fill = PatternFill("solid", fgColor="D9D9D9" if spare else "1F3864")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[row].height = 32


def rebuild_sheet2(wb, L):
    ws1 = wb[S1]
    rows = [[ws1.cell(r, c).value for c in range(1, L.n + 1)] for r in data_rows(ws1)]
    part_i = L.col["App Part"] - 1
    pct_l = get_column_letter(L.col["Completion %"])
    if S2 in wb.sheetnames:
        del wb[S2]
    ws = wb.create_sheet(S2)
    for c, w in enumerate(L.widths, 1):
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.cell(1, 1, "Tasks grouped by App Part. Same columns as 'All Tasks'; copy of the data, not linked.").font = Font(italic=True, color="595959")
    last_col = get_column_letter(L.n)
    r = 3
    extra = sorted({x[part_i] for x in rows} - set(PARTS))
    for n, part in enumerate(PARTS + extra, 1):
        data = [x for x in rows if x[part_i] == part]
        if not data:
            continue
        ws.cell(r, 1, part).font = Font(bold=True, size=13)
        for c in range(1, L.n + 1):
            ws.cell(r, c).fill = PatternFill("solid", fgColor="D6E0F0")
        hdr, first = r + 1, r + 2
        last = hdr + len(data)
        ws.cell(r, L.col["Completion %"] - 1, "Tasks:").font = Font(bold=True)
        ws.cell(r, L.col["Completion %"], f"=COUNTA(A{first}:A{last})").font = Font(bold=True)
        ws.cell(r, L.col["Remaining"],
                f'="Average completion: "&TEXT(AVERAGE({pct_l}{first}:{pct_l}{last}),"0.0%")').font = Font(bold=True)
        for c, h in enumerate(L.head, 1):
            ws.cell(hdr, c, h)
        style_header(ws, hdr, L)
        for i, vals in enumerate(data):
            for c, v in enumerate(vals, 1):
                ws.cell(first + i, c, v)
            style_row(ws, first + i, L)
        tb = Table(displayName=f"Part{n}", ref=f"A{hdr}:{last_col}{last}")
        tb.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=False)
        ws.add_table(tb)
        r = last + 3
    ws.freeze_panes = "C1"


def load_summaries():
    return json.loads(SUMMARIES.read_text(encoding="utf-8")) if SUMMARIES.exists() else {}


def groups(ws1, L):
    """Rows of sheet 1 grouped by (Version, App Part), in order of first appearance."""
    out = {}
    for r in data_rows(ws1):
        key = (str(ws1.cell(r, L.col["Version"]).value), ws1.cell(r, L.col["App Part"]).value)
        out.setdefault(key, []).append({h: ws1.cell(r, c).value for h, c in L.col.items()})
    return out


def rebuild_sheet3(wb, L):
    ws1 = wb[S1]
    summ = load_summaries()
    if S3 in wb.sheetnames:
        del wb[S3]
    ws = wb.create_sheet(S3)
    head = ["Version", "App Part", "What was done", "Tasks", "Priority", "Started", "Finished", "Done %", "Still open", "Phase"]
    for c, (h, w) in enumerate(zip(head, [11, 24, 80, 8, 10, 12, 12, 9, 34, 8]), 1):
        ws.cell(1, c, h)
        ws.column_dimensions[get_column_letter(c)].width = w
    for c in range(1, len(head) + 1):
        cell = ws.cell(1, c)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F3864")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[1].height = 24
    r = 1
    for (ver, part), items in groups(ws1, L).items():
        r += 1
        starts = [to_date(i["Start Date"]) for i in items if i.get("Start Date")]
        ends = [to_date(i["Completion Date"]) for i in items if i.get("Completion Date")]
        pcts = [i["Completion %"] or 0 for i in items]
        open_n = sum(1 for p in pcts if p < 1)
        phases = sorted({str(i.get("Phase", 0)) for i in items})
        vals = [ver, part, summ.get(f"{ver}|{part}", ""), len(items),
                max((i["Priority (importance)"] for i in items), key=lambda x: RANK.get(x, 0)),
                min(starts) if starts else None, max(ends) if ends else None, sum(pcts) / len(pcts),
                f"{open_n} of {len(items)} not finished" if open_n else "-", ", ".join(phases)]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(r, c, v)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=c in (2, 3, 9), horizontal="center" if c in (1, 4, 5, 6, 7, 8, 10) else None)
        ws.cell(r, 6).number_format = ws.cell(r, 7).number_format = "yyyy-mm-dd"
        ws.cell(r, 8).number_format = "0%"
        # Excel does not auto-fit rows written by a script; size by the longest wrapped cell (~1.1 chars per width unit).
        lines = max(-(-len(str(vals[i])) // int(w * 1.1)) for i, w in ((1, 24), (2, 80), (8, 34)))
        ws.row_dimensions[r].height = 15 * max(lines, 1)
    tb = Table(displayName="ShortForm", ref=f"A1:{get_column_letter(len(head))}{max(r, 2)}")
    tb.tableStyleInfo = TableStyleInfo(name="TableStyleLight1", showRowStripes=False)
    ws.add_table(tb)
    ws.freeze_panes = "A2"


def drop_column(ws, name):
    """Delete a non-required column of sheet 1, keep the other columns' widths and
    rebuild the AllTasks table over the remaining columns."""
    head = [str(ws.cell(1, c).value) for c in range(1, ws.max_column + 1) if ws.cell(1, c).value is not None]
    if name in NEEDED:
        sys.exit(f"{name!r} is a required column and cannot be dropped")
    if name not in head:
        sys.exit(f"No column {name!r} (found {head})")
    idx = head.index(name) + 1
    widths = {c: ws.column_dimensions[get_column_letter(c)].width for c in range(1, len(head) + 1)}
    style = ws.tables["AllTasks"].tableStyleInfo
    del ws.tables["AllTasks"]
    ws.delete_cols(idx)
    for c in range(idx, len(head)):
        ws.column_dimensions[get_column_letter(c)].width = widths[c + 1]
    if get_column_letter(len(head)) in ws.column_dimensions:      # the now-empty last column
        del ws.column_dimensions[get_column_letter(len(head))]
    last = max(r for r in range(2, ws.max_row + 1) if ws.cell(r, 1).value)
    ref = f"A1:{get_column_letter(len(head) - 1)}{last}"
    tb = Table(displayName="AllTasks", ref=ref)
    tb.tableStyleInfo = style
    ws.add_table(tb)


def refresh_table_ref(ws1, L):
    last = max(data_rows(ws1))
    ref = f"A1:{get_column_letter(L.n)}{last}"
    ws1.tables["AllTasks"].ref = ref
    ws1.tables["AllTasks"].autoFilter.ref = ref


def backup(path):
    """One snapshot per release: the first write under the version now in VERSION copies the
    file (= the log as it was at the end of the previous version). Returns (path, created)."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    dest = BACKUP_DIR / f"{path.stem}_start_of_{current_version()}{path.suffix}"
    if dest.exists():
        return dest, False
    shutil.copy2(path, dest)
    return dest, True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["missing", "open", "add", "update", "update-many", "drop-column",
                                      "replace-text", "delete-row", "refresh"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("arg2", nargs="?")
    ap.add_argument("--xlsx", default=str(DEFAULT_XLSX))
    ap.add_argument("--pct", type=float)
    ap.add_argument("--remaining")
    ap.add_argument("--comment")
    ap.add_argument("--phase")
    ap.add_argument("--version")
    ap.add_argument("--start")
    ap.add_argument("--completion")
    a = ap.parse_args()

    path = Path(a.xlsx)
    print(f"Task log: {path}", file=sys.stderr)
    writes = ("add", "update", "update-many", "drop-column", "replace-text", "delete-row", "refresh")
    if a.cmd in writes and (path.parent / f"~${path.name}").exists():
        sys.exit("The task log looks open in Excel (lock file found). Close it first.")
    wb = load_workbook(path)
    if a.cmd in writes and path.resolve() == DEFAULT_XLSX.resolve():
        dest, created = backup(path)
        print(f"Backup: {'saved' if created else 'already have'} {dest.name}", file=sys.stderr)
    ws = wb[S1]
    L = Layout(ws)
    col = L.col
    rows = data_rows(ws)

    if a.cmd == "missing":
        have = {str(ws.cell(r, col["Version"]).value) for r in rows}
        for v, d in changelog_versions().items():
            if v not in have:
                print(f"{v}\t{d}")
        for r in rows:
            v = ws.cell(r, col["Version"]).value
            if not VERSION_RE.match(str(v)):
                print(f"version\t{ws.cell(r, 1).value}\t{v}")
        summ = load_summaries()
        for ver, part in groups(ws, L):
            if f"{ver}|{part}" not in summ:
                print(f"summary\t{ver}|{part}")
        return

    if a.cmd == "open":
        for r in rows:
            pct, rem = ws.cell(r, col["Completion %"]).value or 0, ws.cell(r, col["Remaining"]).value
            if pct < 1:
                print(f"{ws.cell(r, 1).value}\t{ws.cell(r, col['Version']).value}\t{ws.cell(r, col['App Part']).value}\t"
                      f"{round(pct * 100)}%\t{ws.cell(r, col['Task Name']).value}\t| REMAINING: {rem}")
        return

    if a.cmd == "add":
        items = json.loads(Path(a.arg).read_text(encoding="utf-8"))
        vdates = changelog_versions()
        nxt = max((int(re.search(r"(\d+)$", ws.cell(r, 1).value).group(1)) for r in rows), default=0)
        r = max(rows) + 1
        for it in items:
            nxt += 1
            done = it.get("completed", True)
            ver = check_version(it.get("version") or current_version())
            start = parse_date(it["start"], "start") if it.get("start") else (
                vdates.get(ver, date.today()) if done else date.today())
            vals = {
                "Task ID": f"X - MA{nxt:03d}", "Task Name": it["name"], "Functionality": it["functionality"],
                "Version": ver, "App Part": it["part"],
                "Start Date": start,
                "Completion Date": (parse_date(it["completion"], "completion") if it.get("completion")
                                    else date.today()) if done else None, "Deadline": "None",
                "Priority (importance)": it["priority"],
                "Completion %": it.get("pct", 100) / 100, "Remaining": it.get("remaining") or "-",
                "Phase": it.get("phase", 0), "Comments": it.get("comment", ""),
            }
            for c, h in enumerate(L.head, 1):
                ws.cell(r, c, vals.get(h))
            style_row(ws, r, L)
            print(f"added {vals['Task ID']}  {it['name']}")
            r += 1
        refresh_table_ref(ws, L)

    def apply_update(r, pct=None, remaining=None, comment=None, phase=None, version=None,
                     start=None, completion=None):
        if pct is not None:
            ws.cell(r, col["Completion %"], float(pct) / 100)
        if remaining is not None:
            ws.cell(r, col["Remaining"], remaining)
        if comment is not None:
            ws.cell(r, col["Comments"], comment)
        if phase is not None:
            if "Phase" not in col:
                sys.exit("No Phase column in the task log")
            ws.cell(r, col["Phase"], int(phase) if str(phase).isdigit() else phase)
        if version is not None:
            ws.cell(r, col["Version"], check_version(version))
        if start is not None:
            ws.cell(r, col["Start Date"], parse_date(start, "start"))
        if completion is not None:
            ws.cell(r, col["Completion Date"], parse_date(completion, "completion"))
        # Dates are kept up to date on every change.
        if not ws.cell(r, col["Start Date"]).value:
            ws.cell(r, col["Start Date"], date.today())
        pct_now = ws.cell(r, col["Completion %"]).value or 0
        if pct_now >= 1 and not ws.cell(r, col["Completion Date"]).value:
            ws.cell(r, col["Completion Date"], date.today())
        elif pct_now < 1 and completion is None:
            ws.cell(r, col["Completion Date"], None)
        for h in ("Start Date", "Completion Date"):
            ws.cell(r, col[h]).number_format = "yyyy-mm-dd"
        print(f"updated {ws.cell(r, 1).value}: {ws.cell(r, col['Task Name']).value}")

    def row_of(task_id):
        hit = [r for r in rows if ws.cell(r, 1).value == task_id]
        if not hit:
            sys.exit(f"No row with id {task_id!r}")
        return hit[0]

    if a.cmd == "update":
        apply_update(row_of(a.arg), a.pct, a.remaining, a.comment, a.phase, a.version, a.start, a.completion)

    if a.cmd == "update-many":
        for ch in json.loads(Path(a.arg).read_text(encoding="utf-8")):
            apply_update(row_of(ch["id"]), ch.get("pct"), ch.get("remaining"), ch.get("comment"),
                         ch.get("phase"), ch.get("version"), ch.get("start"), ch.get("completion"))

    if a.cmd == "replace-text":
        if not a.arg or a.arg2 is None:
            sys.exit('usage: replace-text "OLD" "NEW"')
        n = 0
        for r in rows:
            for h in ("Task Name", "Remaining", "Comments"):
                cell = ws.cell(r, col[h])
                if isinstance(cell.value, str) and a.arg in cell.value:
                    cell.value = cell.value.replace(a.arg, a.arg2)
                    n += 1
        print(f"replaced in {n} cell(s)")

    if a.cmd == "delete-row":
        m = re.match(r"^(.*?)(\d+)$", a.arg or "")
        if not m:
            sys.exit('usage: delete-row "X - MA176"')
        prefix, number = m.group(1), int(m.group(2))
        r = row_of(a.arg)
        name = ws.cell(r, col["Task Name"]).value
        ws.delete_rows(r)
        moved = 0
        for rr in data_rows(ws):
            mm = re.match(r"^(.*?)(\d+)$", str(ws.cell(rr, 1).value))
            if mm and mm.group(1) == prefix and int(mm.group(2)) > number:
                ws.cell(rr, 1).value = f"{prefix}{int(mm.group(2)) - 1:0{len(mm.group(2))}d}"
                moved += 1
        for rr in data_rows(ws):
            style_row(ws, rr, L)
        refresh_table_ref(ws, L)
        print(f"deleted {a.arg}: {name}; {moved} higher id(s) moved down by one")

    if a.cmd == "drop-column":
        drop_column(ws, a.arg)
        L = Layout(ws)
        print(f"dropped column {a.arg!r}")

    rebuild_sheet2(wb, L)
    rebuild_sheet3(wb, L)
    # Write to a temp file in the same folder, then swap it in, so a crash or a reader
    # mid-write never sees a half-written file.
    tmp = path.with_name(f".{path.stem}.tmp{path.suffix}")
    try:
        wb.save(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
