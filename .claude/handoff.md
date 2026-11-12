# Handoff — 2026-11-12

## Goal
App-wide / Infrastructure: task log tooling (`/tasklog` prompt and `General Form` sheet). No app code changed; VERSION stays 1.0.25.

## Done
- `/tasklog` prompt (`.claude/commands/tasklog.md`) now has a General Form step and the final line must say whether it was updated (read back after the edit).
- `task_log_append.py`: new `rebuild_sheet4` builds General Form (per App Part: average Completion % of ALL its rows, highest release number as "Last Version edited"); called after `rebuild_sheet3` on every write; `refresh` and the docstring cover it. Verified: ran `refresh` on a copy in the scratchpad, then on the real xlsx; sheet order unchanged, 237 rows in All Tasks untouched.
- Real log General Form now: CTR Generator 97 %/1.0.24, Comparator 100 %/1.0.25, Tracker 100 %/1.0.18, App-wide 98 %/1.0.24, Proposals / Documents 82 %/1.0.24, Outside app / Automation 50 %/-.
- `/tasklog` run this session: open items asked, user said none changed; `missing` listed nothing.

## In progress / not fully done
- `tasklog.md` step 3 still says "the script does not touch" General Form and describes a manual edit; that is now wrong (the script rebuilds it). Needs rewording.
- GUIDE.md ("Workbook layout") does not mention the General Form sheet yet.
- NOT verified: how the sheet looks in Excel (column widths and styling were set from code only). Header look may differ from the user's hand-made original.
- The old hand-made General Form was replaced; only 4 parts were listed, 2 parts added (Proposals / Documents, Outside app / Automation). User has not confirmed the 2 extra rows.

## Decisions (with reasoning)
- Completion % = plain average over every row of the part (same as By App Part), so it stays consistent between sheets.
- "Last Version edited" ignores `In process` / `Outside app` (not release numbers); shows `-` when none.
- No new backup: `…_start_of_1.0.25.xlsx` already exists (one backup per release).
- Task log `--comment` REPLACES the cell: pass the old history + " | " + the new entry.

## Next
Reword `tasklog.md` step 3 and add General Form to GUIDE.md, then ask the user whether to keep the 2 extra part rows. Otherwise ask for the next app task (`/pickup`).

## Open questions
- Keep Proposals / Documents and Outside app / Automation rows in General Form, or only the original four?

## Parked
- Alias support for near-identical descriptions (e.g. "SUPERWOOL … 25MM"); remembering the match mode in saved comparisons; stale "Join key" paragraph in USER_GUIDE Step 3.
- Excel report and statistics with a proposal (SAGE cost empty for rows matched by Description) not checked.
- MA124, MA127–MA130, MA133, MA194 stay until feature development ends; MA205 waits for the commercial department.

## Windows checks
- None for the app. Optional: open `Task_Log_MagsudAbbaszade.xlsx` in Excel and look at General Form.
