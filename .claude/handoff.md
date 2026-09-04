# Handoff — 2026-09-04

## Working on
v1.0.10: three subsystems touched in one long session — MR vs CTR Comparator
(Combined View, Rate/Rechargeable mismatch check, Save/Load Comparison),
CTR Generator (AZN manpower Comment now states Shift/Shift Type), and CTR
Tracker (company-based Project Code overrides, currency-aware row matching,
Overwrite-vs-Separate-Revision write mode with real Excel row insertion).
Also fixed the SessionStart hook to point at this file live instead of
embedding a truncated preview.

**Everything is committed** — `1934b7b "v.1.0.10"`, working tree clean. The
CHANGELOG's `[1.0.10]` section is accurate and complete; read it for the
full feature list instead of re-deriving it here. This file covers only
reasoning and state that CHANGELOG/git log don't carry.

## Key decisions (with reasoning)
- **Revision info goes in the row itself (position + AI column), never
  written into the Comment/Y column.** The plan briefly included writing
  the revision number into Comment; the user reversed that explicitly
  ("we will insert revision number to the revision column, while only
  writing numbers there") — that write path and its config (`col_comment`)
  were added, then fully removed.
- **Same revision number → always overwrite in place, regardless of the
  Overwrite/Separate-Revision choice.** "Separate revision" only reaches
  for a spare/inserted row when the revision genuinely differs from what's
  already in the matched row. User confirmed this twice, explicitly.
- **Spare-row reuse requires the spare to be explicitly labeled with the
  target currency, once that currency already has ≥1 revision written.**
  An unlabeled blank spare is more likely the tracker's own reserved slot
  for the CTR's *other* currency (a real pre-creation convention seen in
  the actual file) than a free-for-all row — reusing it wrongly would
  clobber that pairing.
- **Missing-currency row creation ignores the Overwrite checkbox entirely.**
  If a CTR number has rows for only one currency, the other currency's row
  is always created — there's nothing existing to protect, so the checkbox
  doesn't apply. Placed immediately after the existing currency's block,
  not a fixed AZN/USD order.
- **Real Excel automation (`tracker_xlwings.py`, xlwings/COM) only for the
  rare "no spare available" insertion case** — not the normal write path,
  which stays on the fast raw-XML writer. Chosen after auditing the real
  tracker file (no merged cells, no Excel Tables, no data validation, every
  formula row-local except one running counter) confirmed openpyxl's
  `insert_rows()` would technically be safe today, but real Excel gets
  conditional-formatting/AutoFilter-range shifting correct "for free" the
  way a human inserting a row would, rather than relying on that audit
  staying true forever.
- **`_plan_insertions` pre-computes every batch entry's exact final row
  via cumulative-shift simulation before any writing happens**, rather
  than letting the write pass re-discover targets row-by-row. Two
  newly-inserted blank placeholder rows for the same CTR number are
  otherwise indistinguishable to the write pass, which was silently
  scrambling currency/revision order (see Fixed bullets in CHANGELOG).
- **Revision ordering is negative → blank ("no value") → zero-and-positive**,
  with any embedded number extracted via regex from arbitrary text for
  *comparison only* — the row's Revision cell always keeps the original
  typed text. This was a direct correction from the user after the first
  ordering rule (blank/non-numeric always last) proved wrong on real data.

## Current state
- All CTR Tracker logic verified via synthetic openpyxl workbooks matching
  the real column layout, plus two full rounds of genuine Windows/Excel
  testing (via the user relaying prompts to a remote sub-agent), which
  found and confirmed-fixed two real `tracker_xlwings.py` bugs: a stale
  running-counter formula on the row pushed down by an insertion, and the
  BA/BB row-local helper formulas never being seeded on newly inserted
  rows.
- **However**, three later correctness fixes — the multi-entry-same-CTR
  batch bug, the cross-currency/multi-revision interleaving bug (the
  `_plan_insertions` rewrite), and the blank-revision same-revision-match
  bug — were made *after* that second Windows round and have only been
  regression-tested on Linux with mocked/synthetic data, never against
  real Excel on Windows. `tracker_xlwings.py` itself still carries its
  original "untested by the author" docstring caveat (no Windows/macOS/
  Excel in this dev environment) — it has been exercised only through
  fakes.
- The MR vs CTR Comparator's Combined View, Rate/Rechargeable mismatch
  check, and Save/Load Comparison were built and manually reasoned through
  but not verified against a live PySide6 UI session in this environment
  (no browser/GUI harness available here) — worth a real click-through
  before calling that subsystem done, per CLAUDE.md's UI verification rule.
- `TEST_Files/CTR tracker/Issues/CTR-Tracker copy.xlsm` was inspected
  read-only to diagnose the interleaving bug from real scrambled data; the
  root-cause mechanism was confirmed, but the exact historical sequence of
  clicks/code-versions that produced that specific file was not fully
  reconstructed (it likely spans several pre-fix code states) — not
  expected to matter now that the fix is in, just noting it's not fully
  explained.
- The `app.spec`/PyInstaller investigation (whether `xlwings`/`pywin32`
  need explicit hidden-imports for the installer build) was started twice
  and interrupted both times before any conclusion — genuinely unresolved,
  not just undocumented.

## Open questions
- User asked, near the end of the session, to confirm whether the
  same-revision check works with "any string or number" in the Revision
  column. Answer given: ordering (`_revision_sort_key`) extracts embedded
  numbers via regex so text like "Rev2" vs "Rev10" sorts correctly, but
  flagged that dotted version-style text ("v2.10" vs "v2.5") reads as
  decimals (2.10 < 2.5), not semantic versions. Separately, the
  same-revision *match* check is plain case-sensitive text equality after
  trimming — "2" and "2.0" are currently treated as *different* revisions.
  I offered to make that check numeric-equivalent instead; **the user has
  not responded to that offer** — do not implement it unprompted.
- Whether/when the user wants the `app.spec` PyInstaller hidden-imports
  question resolved — they said "let me build it now as an installer,
  install it and test it myself" twice, suggesting they intend to do this
  themselves, but never explicitly closed the loop on whether they still
  want my help with it.

## Files changed
See `git show 1934b7b --stat` for the full list (app.py, comparison_history.py
[new], ctr_generator/{builder_azn,config,paths,tracker,tracker_fast,
tracker_window,window}.py, ctr_generator/tracker_xlwings.py [new],
ctr_generator/template_config.json, pyproject.toml, CHANGELOG.md, VERSION)
plus `sheet_parser.py` and `.claude/hooks/session_start_handoff.py` from
earlier in the session. All are on `main`, already committed.

## Next step
Nothing is currently in flight or requested. If resuming proactively, the
highest-value next step is a real Windows/Excel re-test of the three
post-second-round tracker fixes (multi-entry batch, interleaving/
`_plan_insertions`, blank-revision match) plus `tracker_xlwings.py` itself,
since those have only ever run against mocks on Linux. Otherwise wait for
the user's direction — including whether they want the numeric-revision-
equality change from Open Questions.
