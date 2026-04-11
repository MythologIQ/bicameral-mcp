---
name: bicameral-search
description: Search past decisions before writing code. Use as pre-flight to surface constraints, prior decisions, and context relevant to a feature or task.
---

# Bicameral Search

Pre-flight check before coding — surface past decisions relevant to what you're about to build.

## When to use

- Before starting implementation on a feature
- When the user asks "what was decided about X?"
- When the user says "check for prior decisions" or "pre-flight"

## Steps

1. Call the `bicameral.search` MCP tool with:
   - `query` — natural language description of the feature or area (from user input or $ARGUMENTS)
   - `min_confidence` — 0.3 for broad search, 0.7 for precise matches
2. Present the results clearly:
   - For each matching decision: description, status (reflected/drifted/pending/ungrounded), who decided it, when, and what code it maps to
   - Highlight any **drifted** decisions — these are constraints that may have been violated
   - Highlight any **pending** decisions — these are agreed-upon but not yet implemented
3. If relevant, suggest how the found constraints should inform the user's implementation plan

## Arguments

$ARGUMENTS — the feature, task, or area to search for prior decisions about

## Example

User: "/bicameral:search rate limiting"
→ Call `bicameral.search` with query "rate limiting"
→ "Found 2 decisions: (1) 'Rate limit checkout endpoint' — pending, from Sprint 14 planning. (2) 'API rate limiting uses token bucket' — reflected in middleware/rate_limit.py"
