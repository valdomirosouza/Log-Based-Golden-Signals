#!/usr/bin/env bash
# PreToolUse hook — blocks Edit/Write/NotebookEdit unless an active GitHub Issue
# is recorded for the current session.
#
# Active issue resolution order:
#   1. CURRENT_ISSUE env var (set explicitly: export CURRENT_ISSUE=42)
#   2. Git branch name matches the issue-branch convention:
#      (feat|fix|hotfix|chore|security|privacy|perf|refactor|test|docs|style|ci|build)/NNN-*
#
# Exemptions — always allowed without an issue:
#   Files inside .claude/ (settings, hooks, memory — framework meta-config)
#
# Exit codes: 0 = allow tool call, non-zero = block (stdout shown to Claude)

set -euo pipefail

# ── Parse tool input (JSON on stdin) ─────────────────────────────────────────
INPUT=$(cat)
FILE_PATH=$(printf '%s' "$INPUT" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('tool_input',d).get('file_path',''))" \
  2>/dev/null || true)

# ── Exemptions ────────────────────────────────────────────────────────────────
case "$FILE_PATH" in
  */.claude/*|.claude/*)
    exit 0 ;;  # meta-config / hook scripts themselves are always exempt
esac

# ── Check 1: explicit CURRENT_ISSUE env var ───────────────────────────────────
if [[ -n "${CURRENT_ISSUE:-}" ]]; then
  exit 0
fi

# ── Check 2: issue-branch naming convention ───────────────────────────────────
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [[ -n "$REPO_ROOT" ]]; then
  BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  # Matches: fix/42-description, security/42-foo, feat/42-bar, etc.
  if printf '%s' "$BRANCH" \
      | grep -qE '^(feat|fix|hotfix|chore|security|privacy|perf|refactor|test|docs|style|ci|build)/[0-9]+-'; then
    exit 0
  fi
fi

# ── Blocked ───────────────────────────────────────────────────────────────────
cat <<'MSG'
BLOCKED — No active GitHub Issue.

Every file change in this repository must be linked to a GitHub Issue.
Create the issue first, then resume with one of:

  Option A — Set the env var in your shell:
    export CURRENT_ISSUE=<number>      # e.g. export CURRENT_ISSUE=42

  Option B — Work on an issue-scoped branch:
    git checkout -b fix/42-short-description

To create a new issue:
  gh issue create --title "..." --label "bug,golden-signals" --body "..."

This rule covers: source code, configuration, tests, migrations,
CI/CD workflows, Dockerfiles, and documentation.
MSG
exit 1
