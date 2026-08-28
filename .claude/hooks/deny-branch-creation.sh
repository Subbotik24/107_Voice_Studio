#!/bin/bash
# Hard block: main is the only branch in this repository.
#
# Reads the PreToolUse payload on stdin and refuses any Bash command that would
# create a Git branch. Exit code 2 rejects the tool call and returns the stderr
# text to the model. Inspecting, listing and deleting branches stay allowed.

payload=$(cat)
command=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)
[ -z "$command" ] && exit 0

deny() {
  echo "BLOCKED by .claude/hooks/deny-branch-creation.sh: $1" >&2
  echo "main is the only branch in this repository (see CLAUDE.md)." >&2
  echo "Commit and push directly to main instead." >&2
  exit 2
}

# git checkout -b / -B <name>
printf '%s' "$command" | grep -Eq '(^|[;&|]|\s)git\s+(-[^ ]+\s+)*checkout\s+(-[^ ]*\s+)*-{1,2}[bB]([[:space:]]|$)' \
  && deny "git checkout -b creates a branch"

# git switch -c / -C / --create
printf '%s' "$command" | grep -Eq '(^|[;&|]|\s)git\s+(-[^ ]+\s+)*switch\s+.*(-c|-C|--create|--force-create)([[:space:]]|$)' \
  && deny "git switch -c creates a branch"

# git worktree add -b
printf '%s' "$command" | grep -Eq '(^|[;&|]|\s)git\s+(-[^ ]+\s+)*worktree\s+add\s+.*-{1,2}[bB]([[:space:]]|$)' \
  && deny "git worktree add -b creates a branch"

# git branch <name>  — allow only read-only and delete forms
if printf '%s' "$command" | grep -Eq '(^|[;&|]|\s)git\s+(-[^ ]+\s+)*branch([[:space:]]|$)'; then
  rest=$(printf '%s' "$command" | sed -E 's/.*git[[:space:]]+(-[^ ]+[[:space:]]+)*branch//')
  # Bare `git branch`, or one starting with a read-only/delete flag, is fine.
  # printf without a newline yields no line for grep, so an empty rest — a bare
  # `git branch` — must be accepted before the pattern runs.
  if [ -n "$(printf '%s' "$rest" | tr -d '[:space:]')" ] && ! printf '%s\n' "$rest" | grep -Eq '^[[:space:]]*($|[|;&>]|-{1,2}(d|D|delete|list|all|a|r|remotes|v|vv|verbose|merged|no-merged|contains|show-current|format|color|sort)([[:space:]=]|$))'; then
    deny "git branch <name> creates a branch"
  fi
fi

# push that creates a new remote head from a local ref
printf '%s' "$command" | grep -Eq 'git\s+push\s+.*(HEAD|main):(refs/heads/)?(?!main)' 2>/dev/null
printf '%s' "$command" | grep -Eq 'git[[:space:]]+push[[:space:]]+[^|;&]*HEAD:' \
  && deny "git push HEAD:<ref> creates a remote branch"

exit 0
