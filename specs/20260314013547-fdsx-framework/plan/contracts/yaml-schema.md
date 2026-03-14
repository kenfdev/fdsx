# YAML Schema Contract: fdsx Flow Definition

The complete YAML schema is defined in the spec (FR-14). This document captures validation rules and edge cases for implementors.

## Variable Reference Contract

### Two syntaxes by context:

| Context | Syntax | Example |
|---|---|---|
| `prompt_template`, `prompt_file` content, `message`, `webhook.template`, `command` | `{variable}` | `{plan}`, `{review.decision}`, `{reviews[0].summary}` |
| `result_path`, `aggregate.source`, `extract.result_path`, `choices[].variable` | `$.path` | `$.plan`, `$.reviews`, `$.decision` |

### Variable resolution rules:
- `{variable}` in prompts: custom safe substitution. Only registered variable names are replaced. Unknown `{...}` patterns are preserved as literals (safe for JSON/code in LLM output).
- `$.path` in JSONPath contexts: dot-separated field access with optional array indexing.

### Static analysis at validation time:
- Trace reachable states from `start_at`
- For each state's `prompt_template`/`prompt_file`, check that referenced `{variables}` correspond to a `result_path` set by a preceding state on at least one reachable path
- Report unreachable variable references as errors

## Provider CLI Command Contract

| Provider | Command Pattern | stdin | stdout |
|---|---|---|---|
| claude | `claude -p "{prompt}" --model {model}` | none | LLM text output |
| opencode | `opencode --model {model} "{prompt}"` | none | LLM text output |
| codex | `codex --model {model} "{prompt}"` | none | LLM text output |
| system | `{command}` (shell execution) | none | command stdout |

Note: Exact CLI invocation patterns may need adjustment based on each tool's actual interface. The provider adapter layer abstracts this.

## Extraction Contract

### Strategy execution order:
Given `strategy: [json, regex, keyword]`:
1. **json**: Find ` ```json...``` ` block → parse → get `pattern` as field name. If no code block, try JSON.parse entire output → get `pattern` as field name.
2. **regex**: Apply `pattern` as regex to raw output → first match group (or full match if no groups).
3. **keyword**: Split `pattern` by `|` → scan output for first occurrence (case-insensitive).

### Result contract:
- Success: extracted value stored at `extract.result_path`
- All strategies fail + no fallback: error (triggers retry)
- All strategies fail + fallback configured: invoke LLM classify
- LLM classify fails: error (triggers retry)
