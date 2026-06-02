#!/bin/bash
# SessionStart hook for Claude Code on the web.
# Installs the project's Python dependencies so the agent can run
# main_routine.py (and any future tests/linters) without a manual setup step.
# Idempotent and non-interactive — safe to re-run.
set -euo pipefail

# Only run in the remote (web) environment; local setups manage their own venv.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

echo "[session-start] installing Python dependencies from requirements.txt"
python3 -m pip install --quiet --disable-pip-version-check -r requirements.txt

echo "[session-start] done"
