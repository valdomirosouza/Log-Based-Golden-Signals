#!/usr/bin/env bash
# commit-msg hook: block commits without a GitHub Issue reference.
# Called by pre-commit framework; $1 is the path to the commit-msg temp file.
#
# Accepted patterns (anywhere in the message):
#   #42  /  Closes #42  /  Fixes #42  /  Refs: #42  /  GH-42
#
# Exemptions: Merge commits, Revert commits, chore(release): bumps.

set -euo pipefail

MSG=$(cat "${1:?no commit-msg file}")

case "$MSG" in
  "Merge "*|"Revert \""*|"chore(release):"*)
    exit 0 ;;
esac

if printf '%s' "$MSG" | grep -qiE \
    '(closes|fixes|resolves|refs?:?[ ]?|references?|see)[ :]*#[0-9]+|#[0-9]+|GH-[0-9]+'; then
  exit 0
fi

cat >&2 <<'ERRMSG'
COMMIT BLOCKED: no GitHub Issue reference in commit message.

Add one of these to the message body:
  Refs: #42   /   Closes #42   /   Fixes #42

Example:
  security(audit): encrypt audit_events.metadata (ADR-0018)

  Refs: #25
  Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
ERRMSG
exit 1
