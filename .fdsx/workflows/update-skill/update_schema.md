You are updating the fdsx `/fdsx` skill's yaml-schema.md reference file based on a source code analysis.

## Source Data
{collected_data}

## Analysis of Discrepancies
{analysis}

## Your Task

Rewrite the yaml-schema.md file at `~/.claude/skills/fdsx/references/yaml-schema.md` to be fully up-to-date with the current source code.

**Rules:**
1. Preserve the existing structure: Table of Contents, then each state type, then Extract/Aggregate/Hook/Validation sections
2. The schema MUST exactly match the Pydantic models in `models/flow.py` and `models/task.py`
3. Every field must show its correct type, whether it's required/optional, and any constraints (min, max, pattern, enum values)
4. Validation rules must match what `models/validators.py` and `core/engine/validate.py` actually enforce
5. Use the `?` suffix notation for optional fields (e.g., `version?: string`)
6. Keep examples minimal — this is a reference doc, not a tutorial
7. If new fields or state types exist in source, add them
8. If fields were removed from source, remove them from the schema

Write the complete updated yaml-schema.md file content. The file will be saved automatically.
