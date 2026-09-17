# Handoff — 2026-09-15

## Working on
v1.0.14 — MR vs CTR Comparator's Combined View, two related changes: (1) show
the item name/Description next to Stock Code (previously only the
detail/Matched view did this — Combined View's aggregation never carried
Description through at all), and (2) replace "Compare Values" with "Show
Combined" while Combined View is active — it now highlights every Stock Code
drawn from more than one MR/CTR source document (not just ones with
mismatched totals) and wires the ↑/↓ arrows to navigate between them, fixing
row navigation that was previously dead in Combined View. Normal (detail)
view's "Compare Values" is unchanged. Everything lives in root-level `app.py`
— the comparator is a separate subsystem from `ctr_tools/` (the CTR
Generator/Tracker), despite `CLAUDE.md`'s header only mentioning `ctr_tools/`.
VERSION bumped 1.0.13 → 1.0.14, CHANGELOG.md updated. Nothing committed yet.

## Key decisions (with reasoning)
- User explicitly chose "highlight ALL stock codes spanning multiple
  documents" over "only mismatched ones" — Show Combined is a
  document-provenance indicator, not a value-diff tool. Don't reintroduce
  diff-based filtering there without re-confirming.
- User explicitly chose to swap the button's label/behavior *only* while
  Combined View is active, not globally — so "Compare Values" and "Show
  Combined" are the same `QPushButton`/checked-state (`_compare_vals_btn`),
  with `_set_compare_vals_mode(combined: bool)` (called from
  `_apply_combined_view`) swapping text/tooltip/colour/nav-tooltips. The
  detail-view Qty/Unit/Rate mismatch logic inside `_apply_value_highlights`
  was deliberately left untouched.
- The old green/red Diff-column highlighting in Combined View was removed
  outright (not kept behind another toggle) — the user's request described
  replacing that behavior, not adding alongside it. The Diff column itself
  is still computed and shown, just no longer auto-highlighted.
- While implementing, found and fixed a latent bug in `_nav_mismatch`: it
  always read the Stock Code for the status bar from `self._display_df`
  regardless of which table was on screen. Harmless before (Combined View
  never populated `_mismatch_rows`), but would have shown the wrong Stock
  Code once Show Combined started using the same nav mechanism — fixed by
  reading from whichever table is actually active.
- Deleted the now-dead `"positive"`/`"negative"` highlight kinds and
  `_POSITIVE_BG`/`_POSITIVE_FG` constants left over after removing the
  diff-coloring, rather than leaving unused code in place.

## Current state
- `app.py` changes are implemented and verified headlessly (offscreen Qt,
  synthetic MR/CTR data with multi-document stock codes, persistence paths
  sandboxed to a temp dir per `CLAUDE.md`'s Testing Safety rule — no real
  alias/preset/rename files or `TEST_Files/**/Output/` were touched). All
  assertions passed: Description columns present in Combined/Needs
  Review/Error Data tables, multi-doc rows correctly highlighted and
  navigable, single-doc rows correctly excluded, button text/tooltip/colour
  swap correctly with the Combined View toggle, detail-view Compare Values
  unaffected.
- **Not yet visually verified in the real app** — this is a Windows-only
  PySide6 desktop app and this session ran on Linux/WSL, so only underlying
  logic/widget state was checked, not actual rendering (colours, column
  widths, layout).
- CHANGELOG.md has a new `## [1.0.14] - 2026-09-15` entry; VERSION is `1.0.14`
  (no trailing newline, matching the file's existing format).
- Nothing committed. `git status` currently shows `app.py`, `CHANGELOG.md`,
  `VERSION` modified, plus this handoff file and one unexplained file (next
  section).

## Open questions
- `uv.lock` has an unexplained diff (921 lines removed — `altair`, `anyio`,
  and other packages dropped) with an on-disk mtime during this session's
  work window. I did not knowingly run `uv lock`/`uv sync`, and the cause
  wasn't identified before this handoff was written. Left as-is —
  uncommitted and un-reverted. Before committing the v1.0.14 changes, this
  needs the user's own call: review whether the `uv.lock` change is
  intentional/fine (e.g. a stale lock finally resolving) or should be reset
  with `git checkout -- uv.lock` first, so it doesn't get bundled into an
  unrelated commit.
- Whether the new orange "combined" highlight colour and the added
  Description column widths actually look right in the real Windows app
  (per `CLAUDE.md`'s output-verification rule) — still pending a relayed
  Windows check.

## Files changed
- `app.py` — Combined View Description columns, Show Combined button
  behavior, `_nav_mismatch` active-table fix, dead highlight-kind cleanup.
  Full detail in CHANGELOG.md's 1.0.14 entry.
- `CHANGELOG.md` — new 1.0.14 entry.
- `VERSION` — `1.0.13` → `1.0.14`.

## Next step
Nothing pending unless the user wants to commit. Before that: resolve the
`uv.lock` question above, then confirm whether to stage/commit
`app.py` + `CHANGELOG.md` + `VERSION` together. A real-app spot-check of the
new Combined View colours/columns (via a relayed Windows session) is also
still open, per this repo's output-verification rule for UI/formatting
changes.
