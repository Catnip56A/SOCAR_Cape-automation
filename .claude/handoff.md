# Handoff — 2026-10-19 (end of session 6)

## Working on
**1.0.21 is committed locally (not pushed) and was built, installed and checked by the user on Windows** (MA179 and MA192 = 100 %). Two commits on `main`: `4d351f2` (restructure into `comparator/`, `stats/`, `common/`, split out with a temporary worktree so it contains no feature code; subset of tests passed there) and `5e6fb66` (all 1.0.21 features). Last full offscreen run before the final two small changes: 192 tests passing under `.venv/bin/python` (Py 3.13, same as `just run`), run file by file so each run stayed under 5 minutes.

## Key decisions (with reasoning)
- **Nothing is assumed** (still the core rule): a missing Days, Stock Code type or rate is shown blank/orange/red with a reason and a "how to fix", never guessed. MR line days come from the matching CTR (user picks if CTRs disagree, or types their own).
- **Equipment vs consumable** comes from the shipped Stock Code types list (confirmed > list > CTR section > unknown; unknown/conflict is not priced). The shipped `ctr_tools/stockcode_types.json` is **git-ignored on purpose (privacy; repo is public)** — builds must be made from this folder, not a fresh clone, or the installed app has no list.
- **Reload changed files** (MA192): re-reads from the path remembered at Browse; any failure keeps the old tables and is reported; unticked sheets stay unticked; **two loaded files with the same name are NOT guessed between** (skipped with a message — I deviated from my first proposal "reload the latest" because it could swap the wrong tables); saved-comparison files have no origin and just get an explanatory tooltip.
- **Scaffold is not in MR vs CTR statistics** (`StatsPanel(show_scaffold=False)`, toolbar says "Scaffold: not included here"); the Generator keeps Include scaffold. Path-font shrinking was dropped by the user.
- **Two commits chosen by the user (option A)**; the restructure commit deliberately has no feature code (the `doc_filter` limit change etc. are in the second).
- CLAUDE.md rule remembered: list files/functions and wait for the go-ahead before editing; user's explicit "do this" was taken as the go-ahead for small changes.

## Current state
- `main` = `5e6fb66`, 2 commits ahead of `origin/main` (`afbf7b1`). **Not pushed — user was asked, no answer yet.**
- Working tree clean. Task log: MA179, MA192 = 100 %. Open: MA124 (8 %), MA127–MA130, MA133 (0 %, ignored until the user says), **MA164 (70 %: still to test on the specific screen ratios — 1080p at 150 % — for the "Unable to set geometry … minimum size" log line and Download Excel Report reachable after Compare)**.
- Disparities segfault (crash.log 2026-10-15) not reproduced; user chose to watch for it.
- Not seen in the real window by me: appearance of the Files-changed / File-not-found dialogs (the user reports MA192 works).

## Open questions
- Push the two commits to `origin/main`? (Normal push; no history rewrite needed.)
- Which installers in `Output/` to keep (undecided; never delete them; `just clean` keeps `Output/`).

## Files changed
(Details are in the two commits.) Key places: `app.py` (Reload: `_reload_changed_files`, `_mark_changed_files`, `_swap_file_tables`, `_ask_missing_file`, `_ask_changed_files`; `_parse(replace=, on_done=)`), `stats/stats_view.py` (`show_scaffold`), `tests/test_file_changes.py` (18 tests), `tests/test_price_stats.py`, `CHANGELOG.md`, `USER_GUIDE.md`, `tests/README.md`. Task-log rows MA192 added.

## Next step
1. Push when the user says so.
2. MA164 screen-ratio test (user, later).
3. Next release work only when the user names it; MA127–MA130 and MA133 stay ignored until told.
