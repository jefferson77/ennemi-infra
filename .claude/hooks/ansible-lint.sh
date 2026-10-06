#!/usr/bin/env bash
# PostToolUse hook for Edit|Write: lints the edited file with ansible-lint when
# it is Ansible YAML. Exit 2 hands the findings back to Claude to fix; any
# other file, or a clean one, exits 0 silently.
set -uo pipefail

file=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
root=${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}

case "$file" in
  "$root"/*) rel=${file#"$root"/} ;;
  *) exit 0 ;;
esac

case "$rel" in
  .ansible/* | .claude/* | .serena/* | collections/*) exit 0 ;;
  *.yml | *.yaml) ;;
  *) exit 0 ;;
esac

[ -f "$file" ] || exit 0

if ! command -v ansible-lint >/dev/null; then
  echo "ansible-lint is not installed: run 'uv tool install ansible-lint'" >&2
  exit 2
fi

cd "$root" || exit 0
if ! out=$(ansible-lint --nocolor "$rel" 2>&1); then
  printf 'ansible-lint failed on %s:\n%s\n' "$rel" "$out" >&2
  exit 2
fi
