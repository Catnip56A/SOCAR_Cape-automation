Generate a handoff summary of this session and save it to .claude/handoff.md.

Include only:
## Working on
## Key decisions (with reasoning)
## Current state
## Open questions
## Files changed
## Next step

Skip anything recoverable from git log or commit messages — this file is for
context that would otherwise be lost, not a changelog.

## Also update the task log

After writing handoff.md, update `Task_Log_MagsudAbbaszade.xlsx` (repo root) with
`.claude/task_log/task_log_append.py` (read its docstring; never edit the xlsx by hand).
Only run it against the real file; to test it, pass `--xlsx` with a copy.

1. **Open items on rows already logged** (only rows below 100 %; a Remaining note on a 100 % row is not unfinished work). Run `python3 .claude/task_log/task_log_append.py open`.
   If it lists anything, ALWAYS ask the user (AskUserQuestion, multiSelect, batch up to 4
   rows per question) which of those are now done, started, or need testing (and where). Do not
   guess from the code or git. For each one they confirm, run `update "<id>" --pct <n>
   --remaining "<what is still left, or ->"`; mention the date or session in `--comment`
   if useful. Leave unconfirmed ones untouched. Ask even if nothing seems to have changed.
2. **New work.** Run `... missing`. For each changelog version listed (committed work only —
   not uncommitted changes), build a JSON file in the scratchpad with one object per task
   (keys: version, part, name, functionality, priority, pct, remaining, comment, phase) following the
   conventions of the existing rows (parts: CTR Generator, MR vs CTR Comparator, CTR Tracker,
   App-wide / Infrastructure; priority = High/Medium/Low importance), then run `... add FILE`.
   Also add a one-line plain-language summary (for a non-technical reader) per new Version|App Part to
   `.claude/task_log/short_summaries.json`, and for any `summary<TAB>key` line printed by `missing`; then run `... refresh`.
   If open-item updates changed a group's meaning, update its line too.
3. Finish with one line: rows added, rows updated.
