---
name: bicameral-drift
description: Check a file for drifted decisions before committing or during code review. Surfaces all decisions that touch symbols in the file and flags divergence.
---

# Bicameral Drift

Code review check — surface decisions that touch a file and flag any that have drifted from intent.

## When to use

- Before committing changes to a file
- During code review / PR review
- When the user asks "are there any drifted decisions for this file?"

## Steps

1. Determine the file path — from $ARGUMENTS, the currently open file, or ask the user
2. Call the `bicameral.drift` MCP tool with:
   - `file_path` — relative path from repo root
   - `use_working_tree` — true for pre-commit (compare against disk), false for PR review (compare against HEAD)
3. Present the results:
   - List each decision that touches this file with its status
   - **Drifted**: the code has changed since the decision was recorded — needs review
   - **Pending**: the decision exists but the code hasn't been written yet
   - **Reflected**: the code matches the intent — all good
4. For drifted decisions, explain what changed and suggest whether the decision or the code should be updated

## Arguments

$ARGUMENTS — file path to check (relative to repo root)

## Example

User: "/bicameral:drift payments/processor.py"
→ Call `bicameral.drift` with file_path "payments/processor.py"
→ "2 decisions touch this file: (1) 'Webhook retry with backoff' — DRIFTED (code changed since decision). (2) 'Log payment failures' — reflected."
