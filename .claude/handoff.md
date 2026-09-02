# Session Handoff — 2026-08-26 → 09-02 (CTR generator v1.0.9)

## Working on

v1.0.9 of the CTR generator: reading the request form's extras block
(Additional Information / scaffold tonnage / TRANSPORT), carrying comments into
the CTRs, and a long tail of Excel/PDF output fixes that fell out of testing
against the user's *own* templates. **All of it is committed** (`d8b54d4`). The
session is effectively finished — what remains is small and listed below.

## Key decisions (with reasoning)

- **Scaffold carries only what the request states: name, quantity, TON.** My
  first version also mapped the system to a pricebook name via config and
  inferred Days from the longest duration elsewhere in the request. The user cut
  both: *"You just have to write conventional scaffold if there any, its
  quantity and in what it is measured (tons)"*, and later *"you should not fill
  scaffold days, they will be edited manually"*. The config map was also
  actively wrong — it matched a pricebook row whose rate is the placeholder
  `1`, where the real hand-priced CTR uses `2.5`.
- **The scaffold row is marked `✓ Manual` on purpose.** Unmatched rows are
  normally dropped from the output; the request *did* ask for scaffold, so the
  line must appear even unpriced. Without this it was silently vanishing.
- **Transport rates live in `transport_rates` config, not code.** The request
  states only vehicle/quantity/duration; rate, mark-up and duration unit are
  commercial figures. Seeded from the hand-priced CTR in
  `TEST_Files/3rd + scaffold/THIRD PARTY.xlsx`, which is also where the whole
  Third Party section's layout and `qty × rate × duration × (1+markup)` formula
  come from.
- **A zero mark-up writes a blank cell, not `0.0%`** — matching that same
  reference CTR, which leaves its un-marked-up fuel line empty.
- **The golden-file / invariant test harness was built, verified, and then
  deleted by the user.** *"I deleted all the files you edited... I do not need
  all of the other functionality, its fine as is."* **Do not rebuild it
  unprompted.** It did prove its worth before deletion (caught the placeholder,
  print-area and mark-up-format regressions on demand), so it's a reasonable
  thing to *offer* again — but only if asked.
- **Most output bugs were only visible against the user's templates, not the
  `TEST_Files/CTR creator/217 - pre v.1.0.9/` samples I was testing with.**
  Their `AZN_TEMPLATE.xlsx` has **column H visible**, which is why `AA` filler
  printed for them and not for me, and I wrongly reported "AZN is clean" once
  because of it. When a formatting report disagrees with the user, **build
  against their template before arguing**.
- **The user gates all commits.** Don't commit unless explicitly asked.

## Current state

- `HEAD` = `d8b54d4 bug fixes - v1.0.9`, containing every source change.
- `CLAUDE.md` is **untracked and uncommitted**. It was written this session,
  deleted by the user along with the test harness, then restored on request
  (minus the paragraph that referenced the now-deleted suite). A commit message
  was proposed and approved-in-principle but **not run**.
- `tests/` still exists but holds only an empty `golden/` and a stale
  `__pycache__/` — leftovers of the deleted harness.
- The user's real templates are at `TEST_Files/CTR creator/AZN_TEMPLATE.xlsx`
  and `USD_TEMPLATE.xlsx`. **They moved there mid-session** (previously under
  `Output/`), so any hardcoded path is stale; find them by glob.
- The 263 request lives at
  `TEST_Files/CTR creator/263 - v.1.0.9/CTR_NAMES_DB & CTR_REQUEST 263.xlsx`.
- **I modified the user's real alias store once, in error.** An early test run
  wrote `"conventional scaffold" → "SCAFFOLD STANDARD CONVENTION (CORE CREW)"`
  into `~/.local/share/SOCAR/CTRGenerator/match_aliases.json`. I removed that
  single key; their three manpower entries are untouched. This is what the
  Testing Safety section of `CLAUDE.md` exists to prevent — sandbox the
  persistence paths before any run that constructs the window or builders.
- A stale Excel lock file `~$263_USD_….xlsx` sits in their Output folder;
  generating over an open workbook fails with a permission error.

## Open questions

1. **Client field reads a phone number.** `parse_ctr_request` takes Client from
   **K5**, but on the v1.0.9 form K5 holds the requester's contact details —
   so `B3` of both CTRs comes out as
   `"Office: +994 125993000, ext.:856104…"`. Raised twice, never answered.
   **This is a real bug and the most substantive thing left.** Needs: where does
   the client name actually live on the new form?
2. **Commit `CLAUDE.md`?** And with the short message
   (`add CLAUDE.md — testing safety, verification and domain rules`) or the
   fuller one? Also whether to keep a `Co-Authored-By` trailer — their existing
   commits carry none.
3. **Delete the `tests/` leftovers?** Offered, not answered.
4. **Pricebook prices are placeholders and this is known.** USD `4410030190` has
   `Unit Price = 1` on all 244 rows; AZN `4410030127` has only `5` and `10`
   across 827 rows. User: *"the prices are just placeholders"*. Any CTR built
   from `TEST_Files` will look under-priced — **don't report this as a bug
   again**.

## Files changed

Everything substantive is in `d8b54d4` — see the commit. Not recoverable from
git:

- `CLAUDE.md` — untracked, awaiting a commit decision.
- `.claude/handoff-2026-09-01-lms-moderation.md` — the previous occupant of
  `handoff.md`, from an unrelated LMS content-moderation session. Renamed rather
  than overwritten, because it declares itself a recovery file for work that
  couldn't be continued in its own session. **It does not belong to this
  project** and was probably copied in by accident.
- `tests/golden/`, `tests/__pycache__/` — deletable leftovers.

## Next step

Answer the Client-field question (open question 1) — it's the one live defect,
and it silently puts a phone number in the client cell of every CTR generated
from the current request form. Then settle the `CLAUDE.md` commit and clear the
`tests/` leftovers.
