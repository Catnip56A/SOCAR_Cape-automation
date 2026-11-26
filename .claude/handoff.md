# Handoff — 2026-11-26 (1.0.27, parts 1-3)

## Goal
1.0.27, Word proposal (the Word document made by the CTR Generator; formerly "PDMS"): empty sections, the rename, and per-task-type templates. VERSION is 1.0.27.

## Done
- Part 1 (commit 401a7da): a category with no lines (manpower, equipment, consumables, third party) is left out of the Word proposal with caption, table and summary row. Verified: tests for short and long templates, and a generated short proposal read back.
- Part 2 (0a46a49): "PDMS" renamed to "Word proposal" in UI, docs, modules, identifiers, stored names (`word_proposal/` QSettings keys, `word_proposal_notes.json`, the `"word_proposal"` block of template_config.json) and template files. No migration, by decision. A grep finds no "pdms" left except old CHANGELOG entries, handoff history and the task log.
- Part 3 (e5f1cd6, pushed): one **Word Proposal folder** (templates found by name `Word_Proposal_Template_<short|long>[_<Task type>].docx`), mandatory task-type drop-down, a Templates… window to map any file by hand (a mapping wins over the name). Four tagged long templates (Industrial Cleaning, Insulation, Rigging and Lifting, Rope Access) next to the Scaffolding one; four optional notes added (22 in all). New: `word_proposal_templates.py`, `word_proposal_templates_view.py`; `tests/test_word_proposal_kind.py` rewritten.
- Verified: full suite 469 passed (468 plus one test fixed and re-run), 0 skipped. All five long templates generate with no leftover tags; Industrial Cleaning and Insulation were also rendered to PDF and looked at.
- Task log updated this session: MA262, MA263, MA264 added; MA124 to 80 %, MA128, MA130 and MA242 to 100 %; General Form refreshed.

## In progress / not fully done
- Nothing is unfinished in the code. NOT verified: any of this in real Word or on Windows (see Windows checks); the Rigging and Lifting and Rope Access output was not viewed as a PDF; the cover "Reference Number / Date" lines (LibreOffice hides them under the photo).
- The user edited the Industrial Cleaning and Rope Access templates after I built them (09:29 / 09:31); I only re-ran a fill check on all five, no visual check.

## Decisions (with reasoning)
- The four old "short … Commercial Proposal.docx" files were really long-layout documents; they were tagged as long templates.
- Task-type texts that stood fixed in the old documents became optional, never pre-ticked notes with a hint (notes are global, not per task type). The second "All prices are in AZN …" wording of the Insulation document was not added (close to the existing AZN note).
- Tests that need a template without note tags use `H.untagged_word_proposal_template()` (built from short_v2 in the sandbox), because the old plain short template is gone.
- The tagging script is not in the repo (it was in the scratchpad); the templates are not in git (TEST_Files is ignored).

## Next
The user decides part 4, or the Windows checks below come back with findings. Commit this handoff first.

## Open questions
- Keep the tagging script as a repo tool, or leave it out?
- Reform the user's own `template_config.json` in the data folder (its old `"pdms"` block is now ignored): only when the user asks.

## Parked
- MA124, MA129, MA133, MA194 stay until feature development ends; MA130 notes work (per-category notes, filters) as before.
- Share the new task-type templates with colleagues (outside the repo).

## Windows checks
Source Files: folder row, Browse…, Templates… window (map a renamed file, Cancel/OK); the task-type box lists the right types for Short and Long and Generate stops until one is chosen; generate from each of the five long templates and a short one and open them in Word (cover text, Tables A-D, notes, page breaks); labels in Source Files not clipped; the window did not grow. After the first start the template paths and notes list start empty (no migration).
