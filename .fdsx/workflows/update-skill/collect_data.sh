#!/usr/bin/env bash
# Collect fdsx source code + current skill files for analysis.
# Outputs a structured dump that an LLM can compare against the skill docs.
set -euo pipefail

SKILL_DIR="$HOME/.claude/skills/fdsx"
SRC_DIR="src/fdsx"

echo "===== CURRENT SKILL.md ====="
cat "$SKILL_DIR/SKILL.md"

echo ""
echo "===== CURRENT yaml-schema.md ====="
cat "$SKILL_DIR/references/yaml-schema.md"

echo ""
echo "===== SOURCE: models/flow.py ====="
cat "$SRC_DIR/models/flow.py"

echo ""
echo "===== SOURCE: models/task.py ====="
cat "$SRC_DIR/models/task.py"

echo ""
echo "===== SOURCE: models/validators.py ====="
cat "$SRC_DIR/models/validators.py"

echo ""
echo "===== SOURCE: providers/base.py ====="
cat "$SRC_DIR/providers/base.py"

echo ""
echo "===== SOURCE: providers/claude.py ====="
cat "$SRC_DIR/providers/claude.py"

echo ""
echo "===== SOURCE: providers/codex.py ====="
cat "$SRC_DIR/providers/codex.py"

echo ""
echo "===== SOURCE: providers/opencode.py ====="
cat "$SRC_DIR/providers/opencode.py"

echo ""
echo "===== SOURCE: providers/gemini.py ====="
cat "$SRC_DIR/providers/gemini.py"

echo ""
echo "===== SOURCE: providers/system.py ====="
cat "$SRC_DIR/providers/system.py"

echo ""
echo "===== SOURCE: providers/__init__.py ====="
cat "$SRC_DIR/providers/__init__.py"

echo ""
echo "===== SOURCE: cli/main.py ====="
cat "$SRC_DIR/cli/main.py"

echo ""
echo "===== SOURCE: core/loader.py ====="
cat "$SRC_DIR/core/loader.py"

echo ""
echo "===== SOURCE: core/variables.py ====="
cat "$SRC_DIR/core/variables.py"

echo ""
echo "===== SOURCE: core/extraction.py ====="
cat "$SRC_DIR/core/extraction.py"

echo ""
echo "===== SOURCE: core/hooks.py ====="
cat "$SRC_DIR/core/hooks.py"

echo ""
echo "===== SOURCE: core/profiles.py ====="
cat "$SRC_DIR/core/profiles.py"

echo ""
echo "===== SOURCE: core/config.py ====="
cat "$SRC_DIR/core/config.py"

echo ""
echo "===== SOURCE: core/engine/validate.py ====="
cat "$SRC_DIR/core/engine/validate.py"

echo ""
echo "===== SOURCE: core/compiler/compile.py ====="
cat "$SRC_DIR/core/compiler/compile.py"

echo ""
echo "===== SOURCE: core/compiler/routing.py ====="
cat "$SRC_DIR/core/compiler/routing.py"

echo ""
echo "===== SOURCE: core/compiler/aggregation.py ====="
cat "$SRC_DIR/core/compiler/aggregation.py"

echo ""
echo "===== SOURCE: core/engine/run.py ====="
cat "$SRC_DIR/core/engine/run.py"

echo ""
echo "===== SOURCE: core/engine/resume.py ====="
cat "$SRC_DIR/core/engine/resume.py"

echo ""
echo "===== SOURCE: core/engine/batch.py ====="
cat "$SRC_DIR/core/engine/batch.py"

echo ""
echo "===== SOURCE: core/engine/tasks_dir.py ====="
cat "$SRC_DIR/core/engine/tasks_dir.py"

echo ""
echo "===== SOURCE: core/selector.py ====="
cat "$SRC_DIR/core/selector.py"

echo ""
echo "===== SOURCE: core/batch.py ====="
cat "$SRC_DIR/core/batch.py"

echo ""
echo "===== RECENT GIT LOG (last 30 commits) ====="
git log --oneline -30
