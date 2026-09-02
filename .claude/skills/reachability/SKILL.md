---
name: reachability
description: Use before editing or debugging unfamiliar code, or when asked whether a route, form action, endpoint, or function is actually used, live, dead, or still wired up. Traces whether the code is reachable from a template, another route, or a caller, and reports LIVE / DEAD / NO STATIC REFERENCE with evidence.
argument-hint: [route, action name, endpoint, or function]
context: fork
agent: Explore
allowed-tools: Bash, Read, Grep, Glob
---

Determine whether **$ARGUMENTS** is actually reachable in this codebase, or dead code.

This repo has real dead code that looks live. Three separate Google Drive import paths exist;
only the Picker one (`/api/picker-import`) is reachable. The other two — the
`import_drive_file`/`import_drive_folder` form actions in `lms/routes/__init__.py` and
`/api/import-drive-file` in `lms/routes/api.py` — have no template referencing them and are
never invoked. Assuming a handler is live because it exists has already cost hours here.

## Step 1 — Classify what you're tracing

| Kind | Looks like | Reached from |
|---|---|---|
| Form POST action | `elif action == 'name':` inside a route's POST branch | a template with `<input type="hidden" name="action" value="name">` |
| API endpoint | `@api_bp.route('/path')` | `fetch('/api/path'` in a template or `static/` JS, or a server-side caller |
| Page route | `@main_bp.route(...)` above `def view_name` | `url_for('main.view_name')` in a template, or a `redirect(...)` |
| Service / helper function | `def name(...)` in `lms/*.py` | direct import + call in Python |
| Template | `lms/templates/x.html` | `render_template('x.html')`, `{% include %}`, or `{% extends %}` |

## Step 2 — Search for references

Run the checks that fit the kind. Search templates **and** Python — a thing can be reached
from either.

```bash
# Form POST action
grep -rn "value=\"<action>\"" lms/templates/
grep -rn "action.*<action>" lms/templates/

# API endpoint
grep -rn "<path>" lms/templates/ lms/static/ lms/

# Page route (blueprint.view_name)
grep -rn "url_for('<bp>.<view>'" lms/ 
grep -rn "<view_name>" lms/ --include=*.py

# Function / symbol, everywhere
grep -rn "<name>" lms/ scripts/ --include=*.py --include=*.html
```

Exclude the definition site itself when counting references — a definition is not a caller.

## Step 3 — Report a verdict

State one of these, with the evidence that supports it:

- **LIVE** — name each referencing file and line. Quote the actual reference.
- **DEAD** — no reference found anywhere. List every place you searched so the claim is checkable.
- **NO STATIC REFERENCE** — nothing found, but the call could be constructed dynamically
  (string-built URLs, `getattr`, a variable passed into `url_for`, JS assembling a path). Say
  this instead of "dead" when dynamic construction is plausible.

Then state the consequence plainly: *"Editing this changes nothing at runtime"* or
*"This is live — reached from X."*

## Notes

- Being registered on a blueprint proves nothing. Flask happily serves routes nothing links to.
- A template existing proves nothing either — check it's rendered or included somewhere.
- Report what you found; do not edit or delete anything. Deletion is the caller's decision.
- If the target is ambiguous (several matches for the name), list them and ask which is meant
  rather than guessing.
