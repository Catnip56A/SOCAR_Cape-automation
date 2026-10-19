Write the session handoff to `.claude/handoff.md` (overwrite; keep it under ~40 lines).
A fresh session must be able to continue from this file alone. Skip anything recoverable from
`git log` or commit messages. Before calling something "missing", grep the code. Skip rows that are 100 % done.

Sections, in this order:
## Goal — the app part + version this session worked on, in one line
## Done — what is finished and verified, one line each, stated plainly (say how it was verified)
## In progress / not fully done — what is left on each item and why; say what was NOT verified
## Decisions (with reasoning) — only what git cannot show
## Next — the first step for the next session
## Open questions — only unresolved ones
## Parked — off-topic items raised during the session (one line each) for a fresh chat
## Windows checks — what the user must check on the real Windows app (empty if nothing)

Then run `git status` and say whether handoff.md is committed. Do not commit unless asked.
Finish with: "Task log NOT updated — run /tasklog when you want it." and, if this session is
large or the topic is done, suggest /clear and a fresh session.
