# Task log — how it works

`Task_Log_MagsudAbbaszade.xlsx` (repo root, git-ignored like all `*.xlsx`) is the log of
tasks done on this app. `/handoff` keeps it up to date through `task_log_append.py` in
this folder. Do not edit the file with other scripts; use Excel by hand when nothing else
is touching it.

```
.claude/task_log/
  task_log_append.py   the only script that writes the log
  short_summaries.json one plain-language line per Version|App Part, shown on the Shortened Form sheet
  backups/             a copy of the log before every script write (30 newest kept)
  GUIDE.md             this file
```

## Workbook layout

**All Tasks** — one row per task, one filterable table (`AllTasks`). Columns are found by
their header text in row 1, so columns may be moved or deleted; these must exist:
Task ID, Task Name, Functionality, Version, App Part, Start Date, Completion Date,
Deadline, Priority (importance), Total Units, Completion %, Remaining, Comments.
`Phase` is optional. Columns whose header starts with `Col ` are treated as spare (grey).

**By App Part** — the same rows split into one table per App Part, with a task count and
average completion. It is a **copy**, rebuilt from All Tasks on every script write, so do
not edit it by hand (changes are lost on the next write).

**Shortened Form** — for quick reading (e.g. by a manager): one row per Version × App Part
with a one-line "What was done" (from `short_summaries.json`, key `Version|App Part`) and figures
computed from All Tasks: task count, highest priority, first start, last finish, average
completion, how many tasks are not finished, phases. Also rebuilt on every script write, so do
not edit it by hand. A group with no text in the JSON shows a blank "What was done" and is
listed by `missing` as `summary<TAB>key`. Write the text, then run `refresh`.

## Column conventions

| Column | Convention |
|---|---|
| Task ID | `X - MA###` (MA = Magsud Abbaszade), numbered in order of creation |
| Functionality | Adding a function · Calculation fix · Output / rendering fix · UI behaviour change · UI loading / performance · Bug fix / debugging · Refactor / maintenance · Logging / error handling · Testing · Process / approval |
| Version | the changelog version; `Unreleased` for done-but-unreleased; `Planned` for not started |
| App Part | CTR Generator · MR vs CTR Comparator · CTR Tracker · App-wide / Infrastructure · Proposals / Documents (a new part gets its own table automatically) |
| Start Date | the changelog date of the version; blank for planned work |
| Completion Date | the day it was finished; blank for planned work |
| Deadline | `None` until deadlines exist |
| Priority | importance: High / Medium / Low (there is no real priority system yet) |
| Total Units | 1 per task |
| Completion % | what was actually done. Known limitations and ideas go in Remaining and do **not** lower it unless they directly affect the work |
| Remaining | what is left or could be improved; `-` when nothing |
| Phase | `0` = everything before the phases were introduced; `1A`, `1B` = phase 1, parts A and B. The user announces the next phase |

## Commands

Run from the repo root. All take `--xlsx PATH` to work on a copy (use this for tests).

```
python3 .claude/task_log/task_log_append.py missing
python3 .claude/task_log/task_log_append.py open
python3 .claude/task_log/task_log_append.py add FILE.json
python3 .claude/task_log/task_log_append.py refresh
python3 .claude/task_log/task_log_append.py update "X - MA005" [--pct 100] [--remaining TEXT] [--comment TEXT] [--phase VALUE]
```

- `missing` — changelog versions with no row in the log yet, plus groups with no short summary.
- `refresh` — rebuilds By App Part and Shortened Form only (after editing `short_summaries.json`).
- `open` — rows below 100 %. Notes in Remaining on 100 % rows are limitations/ideas and are not listed.
- `add` — appends rows from a JSON list. Keys: `version`, `part`, `name`, `functionality`,
  `priority`, `pct` (0–100), `remaining`, `comment`, `phase` (default 0),
  `completed` (default true; `false` = planned, dates left blank).
- `update` — changes one row. `--comment` **replaces** the comment, so read the old one first
  and append to it.

Existing rows are only changed by `update`.

## Rules

1. **Close the log in Excel before any write.** The script stops if Excel's lock file
   (`~$Task_Log_…xlsx`) is next to it. Close it after your own edits too, so they are saved.
2. **Every write is preceded by a backup** in `backups/`. A write goes to a temporary file and
   is swapped in, so an interrupted save cannot leave a half-written log. To undo a mistake,
   copy the newest backup over the log (after closing Excel).
3. **`/handoff` asks about open items.** For every row listed by `open` (below 100 %) it asks the user
   which are now done, and whether they still need testing (and where:
   Windows / WSL). It never decides that from code or git. New rows come only from committed
   changelog versions, plus whatever the user asks to add.
4. Tests go against a copy (`--xlsx`), never the real log. Hand edits made in Excel after the
   last script write are not backed up until the next script write.

## Known limits

- The By App Part and Shortened Form sheets are rebuilt in full each write; any formatting added there by hand is lost.
- The script has only been verified with openpyxl, LibreOffice and (for the earlier layout)
  other spreadsheet readers; it has been opened in Excel by the user. The VS Code "Office
  Viewer" extension does not display the file reliably, so use Excel.
