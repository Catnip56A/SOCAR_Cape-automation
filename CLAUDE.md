# SOCAR Cape automation — CTR generator & MR/CTR comparator

Python + PySide6 desktop app. Source lives in `ctr_tools/`; template
layout positions are config, not code — see `ctr_tools/template_config.json`.

## Session workflow

- One session per app part + version (e.g. MR vs CTR Comparator 1.0.21). Off-topic items go under *Parked* in `.claude/handoff.md`, not into the current work.
- Start with `/pickup`; end with `/handoff` (writes handoff.md only), then `/clear` or a new session. `/tasklog` updates the Excel log on request - remind the user.
- Ask the user whether to switch sessions at each commit and when the topic changes. Never resume a days-old session; use `/pickup`.
- `/compact` only when stopping mid-task is impossible. Use the Explore agent for "where/how" questions; `test-runner`, `reviewer` and `challenger` agents (`.claude/agents/`) only when asked or when a test run / pre-commit review / non-trivial plan calls for it.

## Versioning & Changelog

- Before bumping VERSION or creating a new CHANGELOG section, ask whether this work is a new release or a continuation of the current version. Never bump the version on your own.
- Keep VERSION, CHANGELOG, README/USER_GUIDE and the task log in sync for every release.

## Testing Safety

Never run tests, scripts, or manual verification against real user data stores
(e.g. the persisted alias file, real CTR output folders, production config).
Always copy to a temp directory or point the code at a fixture path first, and
state which path you are writing to before running.

Paths that are off-limits to test runs:

- `~/.local/share/SOCAR/CTRGenerator/` (and the Windows/macOS equivalents
  returned by `ctr_tools/paths.py:user_data_dir`) — `match_aliases.json`,
  `desc_renames.json`, `ctr_presets.json`
- `TEST_Files/**/Output/` — real generated CTRs and the user's own templates
- The native **QSettings** store (`QSettings("SOCAR", "CTRGenerator")`: last-used paths,
  statistics defaults — `~/.config/SOCAR/CTRGenerator.conf` on Linux, the registry on
  Windows). Constructing the window or a settings dialog reads and writes it, so a test
  run must redirect it first: set `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `HOME` to a temp
  folder before the app is imported (`tests/harness.py` does this for every test).

To sandbox a run, redirect the persistence paths before importing the window:

```python
import ctr_tools.aliases as A, ctr_tools.desc_renames as D, ctr_tools.presets as P
A._ALIASES_PATH = TMP / "match_aliases.json"
A._BUNDLED_DEFAULT_PATH = TMP / "nonexistent.json"
D._DESC_RENAMES_PATH = TMP / "desc_renames.json"
P._PRESETS_PATH = TMP / "ctr_presets.json"
```

## UI Testing Limits

- Offscreen/sandboxed Qt tests do NOT prove the real UI works. After any UI change, list the specific things the user must check on the real Windows app: sorting types, popup visibility, multi-sheet files, and window sizing.
- Test with real multi-sheet sample files, not only synthetic fixtures.
- Keep the window minimum height small enough to fit the WSLg maximized area. Adding tabs or pickers must not raise it.
- Sort numeric columns (e.g. Diff) numerically, not as text.

## Before Editing

Before editing anything: list the exact files and functions you plan to change
with file:line, state what you believe the request means in your own words, and
flag any place where two similar subsystems could be confused. Wait for my
go-ahead.

## Scope Discipline

Before asserting that a feature 'already handles all cases', grep for the
concrete enumeration (categories, roles, stock codes) and paste the evidence.
Prefer asking one clarifying question over investigating the wrong subsystem.

## Verification Before Claiming Success

After any change to Excel/PDF output formatting, regenerate the actual output
and open/inspect it before reporting the fix as done. Do not claim output is
'clean' based on code reading alone — the user's templates (e.g. AZN) may
differ from defaults.

Formatting changes are highly coupled: check the neighbouring output too, not
only the thing that was asked for. Things that have silently regressed before —
row heights, print area vs. inserted rows, hidden/contracted rows, and merged
cells — are worth re-checking on every layout change.

## Domain Vocabulary (CTR generator)

- 'Rename' / 'Description edits' refers to the per-category, stock-code-aware
  description rename feature — NOT Match By aliases.
- 'Scaffold' and 'transport' are separate line categories and must never be
  aggregated into manpower.

When a request touches these terms, restate which subsystem you think is meant
before editing.

Related distinctions that have caused misreads:

- **Scaffold** is *equipment* — it becomes a USD Plant & Equipment line
  (quantity = tonnage, unit = TON), never a labor row.
- **Transport** becomes the AZN CTR's *Third Party Activities* section, never
  manpower and never USD equipment.
- 'Contract' / 'expand' refer to Excel **row hiding**, not outline grouping.

## Workflow

### Handoff & Task Log

- /handoff updates the Excel task log. Always edit a test copy first, make a backup, and save atomically.
- If the xlsx is locked (open in Excel), stop and ask the user to close it. Do not retry silently.
- In handoff open questions, skip rows that are 100% complete. Put phase markers (A/B) in the Phase column, not in task names.
- Before listing a feature as 'missing' in a handoff, grep the code to confirm it really doesn't exist.
- Before saying the handoff is committed, check `git status`.

## Debugging

### Debugging Principle

- When the user suggests that a data bug comes from an external source (e.g. the file's author), check the writer/reader code before accepting that and dropping the issue.
