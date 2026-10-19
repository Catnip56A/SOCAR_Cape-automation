# Handoff — 2026-10-19

## Goal
MR vs CTR Comparator + workflow setup, version 1.0.21 (next build's version number not decided).

## Done
- 1.0.21 built, installed and checked by the user on Windows (MA179, MA192 = 100 %).
- Reload/Browse fixes after the challenger review (commit e377659): same-name Browse replaces, Compare anyway in the report, ticks kept on rename, no remove/clear/add during reload, activation check off the UI thread. Verified offscreen only: 23/23 in test_file_changes (.venv Py 3.13 and system 3.10) + 132 tests in 10 other files via the test-runner agent.
- Workflow files (commit 07dd4e7): /handoff (writes this file only), /tasklog (Excel log), /pickup (start from handoff), /release skill, agents test-runner / reviewer / challenger in .claude/agents/, CLAUDE.md sections (Session workflow, Versioning, UI Testing Limits, Handoff & Task Log, Debugging). All three agents were trial-run and worked.
- MR vs CTR no longer has "Include scaffold" (shows "Scaffold: not included here"); path-font shrinking dropped by the user.

## In progress / not fully done
- MA193 (90 %): the e377659 fixes are NOT tested on Windows and not built. Row is filed under 1.0.21; change its version when the new number is decided.
- MA164 (70 %): screen-ratio test (1080p at 150 %) still to do by the user.
- Known, unfixed: a save in Excel without content change still shows the file as changed; reloading a file also removes saved-comparison tables sharing its name.
- .claude/settings.local.json still has stale ctr_generator/user-data permissions (left on purpose; git-ignored, not recoverable).

## Decisions (with reasoning)
- Same-name Browse replaces rather than adds: the old behaviour doubled data and hid the stale copy. Two same-named files can no longer coexist.
- Subagents: only 3 (test-runner haiku, reviewer sonnet read-only, challenger). No custom explorer (built-in Explore suffices) and no release-checker agent (a deterministic check inside /release is better).
- Caveman (token-saving plugin) rejected: wrong cost driver (long-session input), adds ~1,160 input tokens/request, telemetry and cloud sending of prompts.
- Session rule: one session per app part + version; ask the user at commits/topic changes whether to switch.

## Next
1. User decides the next version number and whether it is a new release (ask; never bump VERSION unasked; use /release).
2. Windows test of MA193, then MA164 screen-ratio test.

## Open questions
- Push the local commits (4d351f2, 5e6fb66, 07dd4e7, e377659 and handoff commits) to origin? Not pushed; user hasn't answered.
- New version number for the build containing e377659.

## Parked
- MA124, MA127–MA130, MA133 stay ignored until the user says.
- Disparities segfault (crash.log 2026-10-15) not reproduced; user is watching.

## Windows checks
- Browse an already-added file: replaces it, no doubled data; a failed replace keeps the old tables.
- Reload them and compare with a deleted file kept: report offers Compare anyway.
- Locate with a renamed file keeps unticked sheets unticked.
- Switch back to the window after saving in Excel (also on a network drive): orange ⟳ appears, no freeze.
- Remove/Clear/Browse refused while a reload parses; ⟳ glyph and colours render.
