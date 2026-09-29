# Handoff — 2026-09-29

## Working on
Two small, unrelated threads:
1. Cleaning up a dead `tests/` directory.
2. Prep work for the still-unstarted Word-automation feature (per-company
   CTR-to-Word generation) — confirmed the library choice from last
   session and installed the packages.

## Key decisions (with reasoning)
- **Deleted `tests/` entirely.** It contained only stale `.pyc` files
  (`__init__`, `fixtures`, `harness`, `invariants`, `run`, `snapshot`,
  `test_ctr_output`) — the `.py` sources were already gone, and an empty
  `tests/golden/`. Decompiled the bytecode (strings/docstrings) to
  understand what it *was*: a well-built output-regression suite —
  pinned fixtures, invariant checks (nothing below print area, no data on
  hidden rows), structural JSON snapshots of generated `.xlsx` files,
  diffed against goldens. Confirmed before deleting that it was genuinely
  dead, not just temporarily broken: not imported by `ctr_tools/`
  anywhere, no `just test`/`test-update` recipe in the `justfile` (despite
  the deleted `run.py`'s own docstring referencing one), no CI workflow,
  no `pytest`/test deps in `pyproject.toml`. It had apparently been built
  once and never wired into the actual workflow before its source was
  deleted outside of git (it was untracked, so there's no commit to
  recover it from either). User confirmed the deletion explicitly.
- **Word automation: confirmed python-docx + docxtpl, packages now
  installed.** This decision itself was made *last* session (see prior
  reasoning: pywin32 needs a licensed Word install this Linux/WSL machine
  can't provide, would add an Office-runtime requirement to every
  end-user's machine, doesn't fit the template-file-plus-code pattern
  already used on the Excel side). This session, the user said they
  "asked around" and confirmed going with that combination — added
  `python-docx>=1.1` and `docxtpl>=0.19` to `pyproject.toml`'s main
  `dependencies` (not the `dev` group — these are runtime deps once the
  feature ships, same tier as `openpyxl`/`xlwings`), then `uv sync`.
  Verified both import cleanly (`python-docx` 1.2.0, `docxtpl` 0.20.2).
- **Word automation stays unstarted beyond that.** User confirmed no real
  company Word template exists yet ("we will have it soon... let's
  wait"). The blocking prerequisite carried over from last session is
  unchanged: get one real template and check it for macros/VBA or
  data-bound Content Controls (SDTs) — either would force pywin32 instead
  and invalidate this session's package choice. No template code written,
  no template inspected.

## Current state
- `tests/` no longer exists (was untracked, so `git status` shows no
  change from the deletion).
- `pyproject.toml` and `uv.lock` are modified but **not committed** —
  `python-docx`/`docxtpl` added to `dependencies`. The `uv sync` run also
  dropped a batch of already-obsolete streamlit-related lock entries
  (`streamlit`, `pyarrow`, `pydeck`, etc.) — this matches the "removed
  streamlit legacy stuff" work already committed in `d9804e8`, i.e. the
  lock file was just catching up to `pyproject.toml`, not a side effect
  of today's change.
- `ctr_tools/cbar_rates.py` is still untracked, unchanged from last
  session — not touched today.

## Open questions
- Word automation: still waiting on a real per-company template before
  any code gets written. When it arrives, check macros/VBA and SDTs
  first; only then decide data-source shape (parsed straight from CTR
  requests vs. a separate structured input) and whether "several
  companies, each with its own fixed style" means one template file per
  company or one template with per-company conditional sections — neither
  was decided, just raised.
- Unrelated, still open from before: AZN CTR's `Estimated CTR Total`
  formula still omits Contingency (same bug class as the fixed USD one) —
  user was asked, hasn't said yes.
- `ctr_tools/cbar_rates.py` still has no caller — wiring it into
  `window.py` is still undecided future v1.0.15 work.

## Files changed
- `tests/` — deleted (untracked, so no diff to commit).
- `pyproject.toml`, `uv.lock` — modified, not committed
  (python-docx/docxtpl added).

## Next step
Nothing blocking. When the user has a company Word template:
- Inspect it for macros/VBA and data-bound Content Controls before
  writing any generation code — either would overturn this session's
  library choice.
- Separately, whenever convenient: commit the `pyproject.toml`/`uv.lock`
  change and the still-untracked `ctr_tools/cbar_rates.py` (neither was
  committed automatically since committing wasn't asked for).
