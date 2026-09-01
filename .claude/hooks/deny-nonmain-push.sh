#!/bin/bash
# Hard block: pushes may target ONLY main. Deleting stray remote branches
# stays allowed so they can be cleaned up. Companion to
# deny-branch-creation.sh; see CLAUDE.md.

payload=$(cat)
command=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null)
[ -z "$command" ] && exit 0

printf '%s' "$command" | grep -Eq '(^|[;&|]|\s)git\s+(-[^ ]+\s+)*push([[:space:]]|$)' || exit 0

push_args=$(printf '%s' "$command" | sed -nE 's/.*git[[:space:]]+(-[^ ]+[[:space:]]+)*push([^|;&]*).*/\2/p')
verdict=$(printf '%s' "$push_args" | python3 -c '
import sys

allow_delete = False
positional = []
for token in sys.stdin.read().split():
    if ">" in token or "<" in token:
        break  # a redirection ends the argument list
    if token.startswith("-"):
        if token in ("--delete", "-d"):
            allow_delete = True
        continue
    positional.append(token)
if allow_delete:
    print("allow")  # deleting stray remote branches is cleanup, not creation
    raise SystemExit
for refspec in positional[1:]:  # first positional token is the remote
    if refspec in ("main", "main:main", "main:refs/heads/main"):
        continue
    if refspec.startswith(":"):
        continue  # bare-colon refspec is a delete
    print(f"deny {refspec}")
    raise SystemExit
print("allow")
')
case "$verdict" in
  deny*)
    echo "BLOCKED by .claude/hooks/deny-nonmain-push.sh: git push may target only main (got: ${verdict#deny })" >&2
    echo "main is the only branch in this repository (see CLAUDE.md)." >&2
    echo "Use exactly: git push origin main" >&2
    exit 2
    ;;
esac
exit 0
