from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _hash_tree(path: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    size = 0
    files = 0
    for member in sorted(path.rglob("*")):
        if member.is_dir():
            continue
        relative = member.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        if member.is_symlink():
            digest.update(member.readlink().as_posix().encode("utf-8"))
            digest.update(b"\0")
            files += 1
            continue
        member_hash, member_size = _hash_file(member)
        digest.update(member_hash.encode("ascii"))
        digest.update(b"\0")
        size += member_size
        files += 1
    return digest.hexdigest(), size, files


def artifact_info(root: Path, path: Path) -> dict[str, Any]:
    target = path.resolve()
    try:
        relative = target.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"artifact must be inside release directory: {path}") from exc
    if target.is_dir():
        sha256, size, files = _hash_tree(target)
        return {
            "path": relative,
            "kind": "directory",
            "tree_sha256": sha256,
            "size": size,
            "files": files,
        }
    if not target.is_file():
        raise FileNotFoundError(target)
    sha256, size = _hash_file(target)
    return {
        "path": relative,
        "kind": "file",
        "sha256": sha256,
        "size": size,
    }


def create_manifest(
    release_directory: Path,
    artifacts: list[Path],
    *,
    release_label: str,
    acceptance_result: Path,
    repository_root: Path,
) -> dict[str, Any]:
    acceptance_sha256, _ = _hash_file(acceptance_result)
    acceptance = json.loads(acceptance_result.read_text(encoding="utf-8"))
    if acceptance.get("status") != "PASS" or acceptance.get("tasks", 0) < 50:
        raise ValueError("release manifest requires a passing 50-task acceptance result")
    return {
        "manifest_version": 1,
        "project_version": "0.3.0rc1",
        "release_label": release_label,
        "release_kind": "unsigned-macos-test-rc",
        "created_at": datetime.now(UTC).isoformat(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "artifacts": [
            artifact_info(release_directory, artifact) for artifact in artifacts
        ],
        "acceptance": {
            "file": "acceptance-result.json",
            "sha256": acceptance_sha256,
            "status": acceptance["status"],
            "tasks": acceptance["tasks"],
            "crashes": acceptance["crashes"],
            "originals_unchanged": acceptance["originals_unchanged"],
            "storage_status": acceptance["storage_audit"]["status"],
        },
        "repository_metadata": "present" if (repository_root / ".git").exists() else "absent",
        "signing": "ad-hoc",
        "production": False,
        "privacy_default": "local/private",
        "accuracy_claim": "none",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release-directory", type=Path, required=True)
    parser.add_argument("--release-label", required=True)
    parser.add_argument("--acceptance-result", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = create_manifest(
        args.release_directory,
        args.artifact,
        release_label=args.release_label,
        acceptance_result=args.acceptance_result,
        repository_root=args.repository_root,
    )
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
