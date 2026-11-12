---
name: minimal-diff
description: Use when you need to modify existing code, fix a bug, or add a small feature to an existing codebase. Do not use for writing new code from scratch (use code-simplicity for that).
---
 
# Minimal changes to existing code
 
Change only what the task requires.
 
## Rules
 
1. **Read the surrounding code first** and follow its style, naming and patterns, even if you would have done it differently.
2. **Do not rework neighbouring code.** No renaming, moving, reformatting or "cleanup" that is not part of the task.
3. **Do not add changes nobody asked for**, including fixes for other bugs outside the task's scope. Mention them in your reply instead.
4. **Keep the diff small.** If the change needs more than expected, stop and explain why before continuing.
5. **Do not change public interfaces** (function signatures, API responses, field names) without explicit permission.
6. **A test that accompanies the change goes into an existing file in the matching `tests/` folder** (see `TESTS.md`, "Where the tests live"). Create a new file only if none fits, inside the area's folder, never directly in `tests/`.
## Check before handing over
 
- Is every changed line needed for the task?
- Does the style match the surrounding code?
- If I noticed something outside the scope, did I mention it rather than change it?
 