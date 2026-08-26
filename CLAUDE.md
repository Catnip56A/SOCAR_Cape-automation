# SOCAR Cape automation — CTR generator & MR/CTR comparator

Python + PySide6 desktop app. Source lives in `ctr_generator/`; template
layout positions are config, not code — see `ctr_generator/template_config.json`.

## Testing Safety

Never run tests, scripts, or manual verification against real user data stores
(e.g. the persisted alias file, real CTR output folders, production config).
Always copy to a temp directory or point the code at a fixture path first, and
state which path you are writing to before running.

Paths that are off-limits to test runs:

- `~/.local/share/SOCAR/CTRGenerator/` (and the Windows/macOS equivalents
  returned by `ctr_generator/paths.py:user_data_dir`) — `match_aliases.json`,
  `desc_renames.json`, `ctr_presets.json`
- `TEST_Files/**/Output/` — real generated CTRs and the user's own templates

To sandbox a run, redirect the persistence paths before importing the window:

```python
import ctr_generator.aliases as A, ctr_generator.desc_renames as D, ctr_generator.presets as P
A._ALIASES_PATH = TMP / "match_aliases.json"
A._BUNDLED_DEFAULT_PATH = TMP / "nonexistent.json"
D._DESC_RENAMES_PATH = TMP / "desc_renames.json"
P._PRESETS_PATH = TMP / "ctr_presets.json"
```

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
