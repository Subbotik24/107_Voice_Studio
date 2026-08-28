from __future__ import annotations

import argparse
from pathlib import Path

from voice_studio.help_content import resolve_help_root, validate_help_tree


def main() -> int:
    parser = argparse.ArgumentParser(description="validate canonical VOICE Studio Help")
    parser.add_argument("root", nargs="?", type=Path, help="override docs/help directory")
    args = parser.parse_args()
    root = resolve_help_root(args.root) if args.root else resolve_help_root()
    issues = validate_help_tree(root)
    if issues:
        for issue in issues:
            print(f"FAIL: {issue}")
        return 1
    print(f"PASS: {root} ({len(tuple(root.rglob('*.md')))} Markdown files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
