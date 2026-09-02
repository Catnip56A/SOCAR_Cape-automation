---
name: phase-wrap
description: Use when finishing a chunk of work and getting it ready to land — review the diff, update the development checklist, and propose a commit message.
argument-hint: [what was worked on, optional]
disable-model-invocation: true
---

Wrap up and land the current chunk of work: $ARGUMENTS

Runs in order. Do not skip the review steps to get to the commit message faster.

## Step 1 — Establish scope

```bash
git status --short
git diff --stat
git log --oneline -5
```

Read the actual diff for anything you did not write yourself in this conversation. Do not
describe changes you have not looked at.

## Step 2 — Review

Invoke `/code-review` on the diff.

Additionally invoke `/security-review` when the change touches any of: authentication,
permission gates (`has_perm`, `is_managed_by`, `is_admin`), file uploads, Drive sharing or
file permissions, rate limits, OAuth scopes and tokens, or anything rendered into a template
from user input.

Act on real findings before continuing. If you disagree with a finding, say why rather than
silently skipping it.

## Step 3 — Update the checklist

Edit `Docs/rework docs/development_checklist.md`, matching the house style of the existing
entries.

- Mark completed items `- [x]`, with a short note on **what was implemented and how it was
  verified**. Existing entries cite concrete evidence — exact chunk counts, Redis counter
  values, live end-to-end runs. Match that bar; "done" alone is below it.
- Anything found but deliberately **not** fixed goes in as an unchecked `- [ ]` follow-up with
  the reason, not omitted.
- Bugs found through real use get an entry naming the root cause, not just the symptom — the
  root cause is the reusable part.
- If a previous entry turns out to be wrong, correct it in place and say so. Stale entries in
  this file have caused real wasted work.

## Step 4 — Propose a commit message

Match the repo's terse, lowercase style — `topic - detail, detail`:

```
phase 6 v1 - effort modes + rate limits, manual quiz grading, password toggle
drive fixes - picker resource keys + app id, video type sniffing
additional fix - promocodes and teacher regulation
```

Name what a reader would grep for later. Flag anything in the diff that shouldn't be committed
(debug scripts, dead code slated for deletion, secrets in tracked files).

## Step 5 — Stop

**Present the message as text. Do not run `git add` or `git commit`** unless the user
explicitly asks in a separate instruction. Same for `git push`.

## Notes

- Never commit `.env` or anything containing a key. If the diff touches a tracked file that now
  holds a credential, stop and flag it.
- If tests or verification failed, say so plainly and do not describe the work as complete.
