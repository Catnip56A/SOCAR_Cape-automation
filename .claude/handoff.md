# Handoff — 2026-09-12

## Working on
v1.0.13, just committed locally as a single clean commit (`14498ff`) after
squashing two mislabeled outgoing commits (see "Key decisions" below). My
own direct work this session was entirely on the MR vs CTR Comparator
(`app.py`): a Hide/Show results toggle that doesn't force a recompute, a
Maximize toggle that now covers the whole comparator tab (step indicator +
pinned Compare/Load row too, not just the file-list area), Combined View
"MR Document"/"CTR Document" columns, per-table delete (✕) buttons in
Step 2 distinct from the existing skip-via-checkbox, a red/warning file
marker when all of a file's tables get deleted, a preview-pane sync fix,
and a root-caused fix for popups rendering with a solid black background.

The CTR Tracker substantive changes bundled into the same v1.0.13 commit
(Job Type dropdown, batch insertion tie-break fix, same-CTR+currency
in-batch conflict retry, duplicate-check coverage healing) were **not**
made by me in this conversation — they appeared as already-modified files
via git-status/CHANGELOG diffs partway through the session, meaning
another session or the user did that work directly. I have not reviewed
that code myself and can't vouch for its reasoning the way I can for the
Comparator work.

## Key decisions (with reasoning)
- **Black-popup-background bug root-caused to `ctrl.setStyleSheet(
  "background: transparent;")`** in `app.py`'s `_build_ui` (the widget
  inside the Comparator's scroll area, wrapping Step 1/Step 2). Applying
  *any* stylesheet to a widget switches Qt to its CSS engine for that
  widget's entire descendant subtree, which broke `QToolTip` rendering for
  everything inside it while tooltips outside it were fine — exactly the
  boundary the user found ("only below Load Comparison"). Fixed via
  `ctrl.setAutoFillBackground(False)` instead, which achieves the same
  visual effect via Qt's own default (a plain `QWidget` doesn't autofill
  its background anyway) without invoking the CSS engine. This took ~5
  rounds of hypothesis-testing first — a QSS-only fix, then a
  palette+colour-scheme fix, then two separate synthetic reproduction
  scripts (a plain button tooltip; a list-item tooltip combined with a
  widget-level tooltip on the same list) — and **both synthetic repros
  failed to reproduce the bug** even using the exact same widget types as
  the real one. Only the user's own structural observation (which part of
  the UI was affected vs. not) actually pinpointed it. Confirmed fixed by
  the user on real Windows + WSL.
- **Combined View's new doc-name columns are scoped to the Combined tab
  only** — not Needs Review/Error Data — an explicit scope decision the
  user confirmed after I asked, rather than assumed.
- **Table-list "remove" (✕) is genuinely destructive removal**, distinct
  from the pre-existing checkbox (which only skips a table from Compare
  without deleting it). The user explicitly corrected me toward this when
  I initially offered "a new Clear All button" as the interpretation of
  their request — they wanted real deletion, checkboxes already handle
  skip/include.
- **File-list red/warning styling fires only on full deletion of every
  table from that file**, never on unchecking — kept deliberately narrow
  to match the literal request, and to stay consistent with the
  skip-vs-delete distinction above.
- **Squashed the two most recent outgoing commits** (`7ff15b2`, a pure
  `ctr_generator→ctr_tools` rename with 0 content changes, and `fb6c0d7`,
  all the actual v1.0.13 substance) — both carried the *identical*
  "v1.0.13 - CTR Tracker batch/Job Type fixes..." commit message, which
  actually described `fb6c0d7`'s content, not the rename `7ff15b2` itself
  contained. Safe to squash since neither had been pushed (`ahead 2` →
  `ahead 1` after, now sitting as `14498ff`).

## Current state
- v1.0.13 is fully committed locally as one commit, **one commit ahead of
  `origin/main`, not yet pushed**.
- All my `app.py` Comparator changes were verified via headless/offscreen
  PySide6 tests in this Linux/WSL sandbox (can't render real pixels here)
  — actual visual confirmation came from the user's own screenshots and
  manual testing on their real Windows+WSL setup, which did confirm the
  black-popup fix specifically.
- Per `project_tracker_xlwings_verified.md` (memory), the CTR Tracker's
  `tracker_xlwings.py` changes bundled into this same v1.0.13 commit (the
  column-B running-counter write was removed; insert-row selection logic
  in `tracker_fast.py:_plan_insertions` changed) are **unverified against
  real Excel** — only exercised via openpyxl/raw-XML sandboxed tests on
  Linux, which can't drive the actual COM automation path at all.

## Open questions
- Whether the CTR Tracker changes bundled into this commit (made outside
  this visible conversation) have been verified on real Windows/Excel at
  all — not established either way in this session.
- Whether the user wants v1.0.13 pushed to `origin` now that the commit
  history is clean, or wants to hold off pending that Tracker
  verification.
- Combined View's new "MR Document"/"CTR Document" columns are always
  included in the exported Excel report regardless of the "Show Document
  Names" UI toggle, matching the pre-existing convention for the other
  three tabs — never explicitly re-confirmed with the user that this is
  still the desired behavior now that Combined View has doc columns too,
  though nothing has been raised as a complaint.

## Files changed
See `git show 14498ff --stat` for the full list (30 files: the
`ctr_generator`→`ctr_tools` rename plus substantive changes to `app.py`,
`comparison_history.py`, `sheet_parser.py`, and most of `ctr_tools/*`).
`CHANGELOG.md`'s `[1.0.13]` section documents the full feature/fix list.

## Next step
Nothing is currently in flight. If resuming proactively: confirm whether
v1.0.13's bundled CTR Tracker changes need real-Excel verification before
this gets pushed/released, ask whether to push the now-clean commit to
`origin`, and otherwise wait for direction.
