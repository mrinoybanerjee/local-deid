#!/bin/bash
# Ask the project advisor (Fable 5.1, high reasoning) a question. The advisor
# runs headless with read-only tools in this repository and prints its answer.
#   tools/advisor.sh "question"         or       tools/advisor.sh < prompt.md
set -euo pipefail
cd "$(dirname "$0")/.."
if [ $# -gt 0 ]; then prompt="$*"; else prompt="$(cat)"; fi
[ -n "$prompt" ] || { echo "usage: tools/advisor.sh \"question\"" >&2; exit 2; }
exec env -u CLAUDE_CONFIG_DIR command claude -p \
  --model claude-fable-5-1 --effort high \
  --allowedTools "Read,Grep,Glob" \
  --append-system-prompt "You are the senior technical advisor for the Local DeID project in this repository. SPEC.md v6 is the source of truth; AGENTS.md has the rules. Read the code you are asked about before you answer. Be concrete and brief: give a recommendation, the reason, and any defect with the exact file:line. Do not edit files." \
  "$prompt"
