# Handoff — 2026-11-15

## Goal
Release 1.0.26, part 1 of 1 (one of the last versions): two small changes after the presentation prep. VERSION and CHANGELOG are updated; the task log is deliberately NOT updated for this version.

## Done
- PDMS: the CBAR rate boxes lost their orange background; the "PROVISIONAL" tooltip, the "provisional conversion" warning before Generate and the "(provisional rule)" line in the PDMS overview sheet are gone; comments in `builder_pdms.py`, `pdms_overview.py`, `window.py` and one USER_GUIDE sentence updated (the conversion works as the commercial team asked).
- MR vs CTR: the Compare Values status message names the Qty mode ("Compare Values (Qty: CTR < MR only) — …", also in the "no mismatches" message) — `app.py`.
- VERSION 1.0.26, CHANGELOG entry added. Full offscreen suite: 336 passed, 0 failed.
- Presentation (outside the repo): deck draft 3 (34 slides), Word script draft 2 and the Excalidraw map live in the user's Desktop folder `SOCAR_CAPE_presentation Dec 1st 2026` (01_Script … 05_Build_scripts, README.txt there). Presentation is on 1 December.

## In progress / not fully done
- Nothing in the app is half-done. Not committed yet (user commits).

## Decisions (with reasoning)
- Provisional marking removed because the user confirmed the conversion works as the colleagues said.
- No task-log row for 1.0.26 (user's call).
- Items without a Stock Code, price comparison in Proposal mode: unchanged, presented as by-design limits.

## Next
Build the .exe and check on Windows (below). Commit title: "1.0.26 part 1 - PDMS conversion no longer marked provisional, Compare Values status names the Qty mode".

## Open questions
- Still open from before: keep the two extra part rows in General Form, or only the original four?

## Parked
- Alias support for near-identical descriptions; remembering the match mode in saved comparisons; stale "Join key" paragraph in USER_GUIDE Step 3.
- Excel report and statistics with a proposal (SAGE cost empty for rows matched by Description) not checked.
- MA124, MA128–MA130, MA133, MA194 stay until feature development ends.
- Showing the app's name in the window title / exe as "SOCAR Cape Commercial Automation": not wanted now (the app will expand).

## Windows checks
- Generator in PDMS mode: the CBAR rate boxes are white, no orange; Generate shows no "provisional" warning; the overview Excel has no "(provisional rule)" text.
- MR vs CTR: press Compare Values in both Qty modes and read the status bar (the mode is named in it).
