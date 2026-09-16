---
name: repository-workflow
description: Inspect a Git repository, make targeted changes, and validate them.
---

Read the repository README and relevant project instructions before editing.
Identify the test or build command from project configuration.
Plan multi-step work with todo_write; keep one item in_progress at a time.
Use task for bounded investigations; include necessary context in its prompt.
Read relevant code and make the smallest coherent change.
Run relevant tests and inspect the diff. Report what changed, validation results,
and remaining limitations. Do not claim that tests ran unless they actually ran.
