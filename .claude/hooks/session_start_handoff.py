#!/usr/bin/env python3
"""SessionStart hook: if .claude/handoff.md exists (written by /handoff at the end of a prior
session), inject its content as additional context so the new session picks up where the last
one left off without the user having to say "read the handoff file" manually.

Reads relative to the current working directory, which Claude Code sets to the project root
before running SessionStart hooks.
"""
import json
import os

path = os.path.join('.claude', 'handoff.md')

if os.path.isfile(path):
    # Point at the file rather than embedding its content: large handoffs get
    # truncated to a lossy preview when dumped inline here, and an inline copy
    # can also go stale if the file is rewritten later in a long-running session.
    # Read(path) always returns what's actually on disk right now.
    print(json.dumps({
        'hookSpecificOutput': {
            'hookEventName': 'SessionStart',
            'additionalContext': (
                'A session handoff exists at .claude/handoff.md (written by /handoff at '
                'the end of a prior session). Read it with the Read tool before doing '
                'anything else, to pick up where the last session left off.'
            ),
        }
    }))
else:
    print('{}')
