You are updating the fdsx `/fdsx` skill's SKILL.md file based on a source code analysis.

## Source Data
{collected_data}

## Analysis of Discrepancies
{analysis}

## Your Task

Generate the complete updated SKILL.md content and **print it to stdout as plain text**. Do NOT use any tools (Read, Edit, Write, Bash) to modify files. Your entire text output will be captured and saved automatically by the workflow engine.

**Rules:**
1. Preserve the existing structure and writing style of SKILL.md
2. Keep the YAML frontmatter (name, description) — update the description if the capabilities have changed
3. Only change sections where discrepancies were found — don't rewrite sections that are already correct
4. Ensure all provider options tables match the source code exactly
5. Ensure all CLI commands and flags match `cli/main.py` exactly
6. Ensure all examples use correct field names and valid YAML
7. Keep the document concise — don't add unnecessary verbosity
8. If new features were added, add them in the most logical existing section (or create a minimal new section if needed)
9. If features were removed, remove them cleanly without leaving stubs

**IMPORTANT: Output ONLY the raw file content. No preamble, no explanation, no code fences. Just the complete SKILL.md content starting with the `---` frontmatter.**
