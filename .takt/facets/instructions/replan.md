Create a targeted fix plan based on review feedback, classify complexity, and route to the right fix agent.

**Context:** The reviewer has found issues. Your job is to analyze findings, create a concrete fix plan, and decide whether a simple or complex model is needed.

**Steps:**
1. Read the review report from {report_dir}/review.md
2. Read the original plan from {report_dir}/plan.md for context
3. Check for past iteration reports: run Glob with `replan.md.*` and `fix-report.md.*` patterns in {report_dir}. Read up to 2 most recent of each to understand what was already tried and what failed. This is critical to avoid repeating failed approaches.
4. For each review finding:
   - Understand the root cause
   - Check if a similar fix was attempted before — if so, explain why it failed and what must be different
   - Write the concrete fix approach with pseudocode or code snippets
   - Classify as **simple** or **complex** (see criteria below)

**Complexity classification:**
- **Simple** (cheap model can handle): naming fixes, missing imports, unused variables, straightforward error handling, single-line logic fixes, adding simple tests, style/formatting
- **Complex** (needs capable model): concurrency/threading patterns, type system rewrites (10+ errors), architectural refactors, security pattern changes, fixes that failed in previous iterations

**Routing decision:**
- If ALL findings are simple → output `[STEP:1]` (route to cheap model)
- If ANY finding is complex → output `[STEP:2]` (route to capable model)
- If findings reveal fundamental design issues requiring major rework → output `[STEP:3]` (blocked)

**Scope boundary — plan report:**
Read {report_dir}/plan.md for the **Out of Scope** section. Any review finding that targets files or areas listed as out of scope MUST be placed in your "Out of Scope" table — do NOT create fix instructions for them, even if the reviewer flagged them as blocking. The plan's scope boundary takes precedence over reviewer findings.

**Important:**
- Do NOT expand scope beyond what the review findings require
- The fix agent works best with specific, concrete instructions. Write exact code patterns, not vague guidance like "handle concurrency properly"
- When a finding was attempted before and failed, you MUST describe a different approach

**Output format:**

## Fix Plan

### Previous Attempts (if any)
| Iteration | What was tried | Why it failed |
|-----------|---------------|---------------|

### Complexity Assessment
| Finding | Classification | Reason |
|---------|---------------|--------|

**Overall routing: simple / complex / blocked**

### Findings to Address
#### Finding 1: {title}
- **File**: `{path}:{line}`
- **Classification**: simple / complex
- **Root cause**: {why this is wrong}
- **Fix approach**: {concrete steps}
- **Code pattern**:
  ```
  # exact code or pseudocode showing the fix
  ```
- **Regression risk**: {what could break}
- **Verification**: {how to confirm}

### Out of Scope (if any)
| Finding | Reason |
|---------|--------|
