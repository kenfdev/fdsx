Create a targeted fix plan based on review feedback.

**Context:** The reviewer has found issues with the implementation. Your job is to analyze the review findings and create a focused plan to address them.

**Steps:**
1. Read the review report from {report_dir}/review.md
2. Read the original plan from {report_dir}/plan.md for context
3. For each review finding:
   - Understand the root cause
   - Determine the fix approach
   - Identify if the fix might affect other areas
4. Create a focused plan that addresses all findings

**Important:**
- Do NOT expand scope beyond what the review findings require
- If findings reveal fundamental design issues that require major rework, output `[STEP:2]` (Blocked)
- If a clear fix plan can be created, output `[STEP:1]` (Fix plan is ready)

**Plan should include:**
- Summary of review findings being addressed
- Fix approach for each finding
- Files that need modification
- Any regression risks to watch for
