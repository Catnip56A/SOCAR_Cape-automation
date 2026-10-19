Start from the last handoff. (Not /resume: that is Claude Code's built-in session picker.)

1. Read `.claude/handoff.md`, run `git status --short` and `git log --oneline -5`.
2. Reply in at most 8 lines: what was done last time, what is not finished, the next step, any Parked items, any Windows checks pending.
   Flag anything in the handoff that git contradicts (e.g. "committed" but the file shows as modified).
3. Ask the user to confirm the next step (or name another). Do not start editing before that.
Work this session on one app part + version; put anything off-topic under Parked in the handoff instead of doing it here.
