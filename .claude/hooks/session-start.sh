#!/bin/bash
set -euo pipefail

# Only run in remote (Claude Code on the web) environments
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Authenticate gh CLI if GITHUB_TOKEN is available
if [ -n "${GITHUB_TOKEN:-}" ]; then
  echo "$GITHUB_TOKEN" | gh auth login --with-token 2>/dev/null || true
  echo 'export GH_TOKEN="'"$GITHUB_TOKEN"'"' >> "$CLAUDE_ENV_FILE"
fi
