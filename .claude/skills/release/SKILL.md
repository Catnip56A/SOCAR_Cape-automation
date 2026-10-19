---
name: release
description: Finalize a version release
---
1. Ask the user: new version or continuation of the current VERSION? Show the current value.
2. Update VERSION, CHANGELOG (merge into the existing section if continuing), README, USER_GUIDE.
3. Show the planned setup.iss diff and wait for approval.
4. Run the full test suite.
5. Propose a commit split, then commit after the user confirms.
6. Run /handoff and print a real-Windows-app verification checklist.
