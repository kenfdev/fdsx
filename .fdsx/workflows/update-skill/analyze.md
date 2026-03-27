You are analyzing the fdsx source code against its current `/fdsx` skill documentation to find discrepancies.

Below is a dump of the current skill files (SKILL.md and yaml-schema.md) and the fdsx source code.

{collected_data}

## Your Task

Compare the **source code** (the ground truth) against the **current skill documentation** (SKILL.md and yaml-schema.md). Identify ALL discrepancies, including:

1. **New features** in source code not documented in the skill
2. **Removed features** documented in the skill but no longer in source code
3. **Changed behavior** — fields renamed, defaults changed, new options added, validation rules updated
4. **New providers** or provider options not documented
5. **New CLI commands or flags** not documented
6. **New state types or fields** not in yaml-schema.md
7. **Incorrect examples** — code snippets that no longer match the API
8. **Missing validation rules** — validators in source not listed in the troubleshooting/validation section

Be thorough and precise. For each discrepancy, cite:
- The source file and relevant code
- The skill file section that's wrong or missing
- What the correct documentation should say

Structure your output as:

### SKILL.md Discrepancies
(list each discrepancy with source evidence)

### yaml-schema.md Discrepancies
(list each discrepancy with source evidence)

### Summary
- Total discrepancies found: N
- SKILL.md changes needed: N
- yaml-schema.md changes needed: N

End your response with exactly one of these keywords:
- CHANGES_NEEDED — if any discrepancies were found
- UP_TO_DATE — if the skill files accurately reflect the source code
