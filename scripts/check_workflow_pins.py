"""Check GitHub Actions workflow dependencies and checkout credential policy."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

_USES_RE = re.compile(r"^\s*(?:-\s*)?uses\s*:\s*(?P<value>\S+)")
_UNSUPPORTED_USES_RE = re.compile(
    r"^\s*(?:-\s*)?(?:['\"]uses['\"]\s*:|\{\s*['\"]?uses['\"]?\s*:|,\s*['\"]?uses['\"]?\s*:)")
_KEY_RE = re.compile(r"^(?P<key>[A-Za-z0-9_.-]+)\s*:\s*(?P<value>.*?)\s*$")
_IMMUTABLE_REF_RE = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _workflow_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        yield path
    elif path.is_dir():
        yield from sorted((*path.glob("*.yml"), *path.glob("*.yaml")))


def _step_bounds(lines: list[str], uses_index: int) -> tuple[int, int]:
    """Return the list-item bounds containing a uses key."""

    uses_line = lines[uses_index]
    uses_indent = _indent(uses_line)
    if uses_line.lstrip().startswith("-"):
        step_indent = uses_indent
        start = uses_index
    else:
        step_indent = uses_indent
        start = uses_index
        for index in range(uses_index - 1, -1, -1):
            candidate = lines[index]
            if (
                candidate.strip()
                and _indent(candidate) < uses_indent
                and candidate.lstrip().startswith("-")
            ):
                step_indent = _indent(candidate)
                start = index
                break

    end = len(lines)
    for index in range(uses_index + 1, len(lines)):
        candidate = lines[index]
        if not candidate.strip():
            continue
        candidate_indent = _indent(candidate)
        if candidate_indent < step_indent or (
            candidate_indent == step_indent and candidate.lstrip().startswith("-")
        ):
            end = index
            break
    return start, end


def _checkout_has_non_persistent_credentials(lines: list[str], uses_index: int) -> bool:
    start, end = _step_bounds(lines, uses_index)
    with_index: int | None = None
    with_indent = -1
    for index in range(start, end):
        candidate = lines[index]
        if candidate.strip().startswith("with:"):
            candidate_indent = _indent(candidate)
            if candidate_indent > _indent(lines[start]):
                with_index = index
                with_indent = candidate_indent
                inline_with = candidate.strip()[len("with:") :].strip()
                if inline_with:
                    if not (inline_with.startswith("{") and inline_with.endswith("}")):
                        return False
                    fields = inline_with[1:-1].split(",")
                    return any(
                        (match := _KEY_RE.match(field.strip()))
                        and match.group("key") == "persist-credentials"
                        and match.group("value").strip() == "false"
                        for field in fields
                    )
                break
    if with_index is None:
        return False

    property_indent: int | None = None
    for index in range(with_index + 1, end):
        candidate = lines[index]
        if not candidate.strip():
            continue
        candidate_indent = _indent(candidate)
        if candidate_indent <= with_indent:
            break
        if property_indent is None:
            property_indent = candidate_indent
        if candidate_indent != property_indent:
            continue
        match = _KEY_RE.match(candidate.strip())
        if match and match.group("key") == "persist-credentials":
            value = match.group("value").split("#", 1)[0].strip()
            return value == "false"
    return False


def check_workflow_paths(paths: Iterable[str | Path]) -> list[str]:
    """Return actionable policy violations for workflow files or directories."""

    violations: list[str] = []
    for requested in paths:
        path = Path(requested)
        files = list(_workflow_files(path))
        if not files:
            violations.append(f"{path}: no .yml or .yaml workflow files found")
            continue
        for workflow in files:
            try:
                lines = workflow.read_text(encoding="utf-8").splitlines()
            except OSError as exc:
                violations.append(f"{workflow}: cannot read workflow ({exc})")
                continue
            for line_number, line in enumerate(lines, start=1):
                if _UNSUPPORTED_USES_RE.match(line):
                    violations.append(
                        f"{workflow}:{line_number}: unsupported uses syntax; use a canonical "
                        "unquoted 'uses: owner/action@<40 lowercase hexadecimal SHA>' mapping"
                    )
                    continue
                match = _USES_RE.match(line)
                if not match:
                    continue
                reference = match.group("value")
                if not reference.startswith("./") and not _IMMUTABLE_REF_RE.fullmatch(reference):
                    violations.append(
                        f"{workflow}:{line_number}: external action '{reference}' must use "
                        "an immutable @40 lowercase hexadecimal commit SHA"
                    )
                requires_credentials_check = reference.startswith("actions/checkout@")
                if requires_credentials_check and not _checkout_has_non_persistent_credentials(
                    lines, line_number - 1
                ):
                    violations.append(
                        f"{workflow}:{line_number}: actions/checkout must set "
                        "persist-credentials: false in its own with block"
                    )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths", nargs="+", type=Path, help="workflow files or directories to check"
    )
    args = parser.parse_args(argv)
    violations = check_workflow_paths(args.paths)
    if violations:
        print("Workflow policy violations:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    print(f"Workflow policy passed for {len(args.paths)} path(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
