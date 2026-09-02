from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__:
    from scripts.generate_sbom import validate_sbom_document
    from scripts.release_filesystem import file_fingerprint, read_file_within_root
else:
    from generate_sbom import validate_sbom_document
    from release_filesystem import file_fingerprint, read_file_within_root

_PROJECT_VERSION = "0.3.0rc1"
RELEASE_KINDS = ("unsigned-macos-test-rc", "unsigned-windows-test-rc")
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


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


def _is_reparse_point(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _sbom_stat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError("release manifest SBOM does not exist") from exc
    except OSError as exc:
        raise ValueError("release manifest SBOM could not be inspected safely") from exc


def _read_sbom_bytes(release_directory: Path, sbom: Path) -> tuple[bytes, str]:
    root = Path(release_directory).absolute()
    raw_target = Path(sbom)
    if ".." in raw_target.parts:
        raise ValueError("release manifest SBOM path contains lexical traversal")
    target = raw_target.absolute()
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError("release manifest SBOM must be inside release directory") from exc
    if not relative.parts:
        raise ValueError("release manifest SBOM must be a regular file")

    root_info = _sbom_stat(root)
    if _is_reparse_point(root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise ValueError("release manifest directory is not a safe directory")
    current = root
    for index, component in enumerate(relative.parts):
        current /= component
        info = _sbom_stat(current)
        if _is_reparse_point(info):
            raise ValueError("release manifest SBOM path contains a symlink or reparse point")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ValueError("release manifest SBOM path contains a non-directory")
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("release manifest SBOM must be a regular file")

    try:
        content, secure_relative, fingerprint = read_file_within_root(root, target)
    except FileNotFoundError as exc:
        raise FileNotFoundError("release manifest SBOM does not exist") from exc
    except ValueError as exc:
        if "changed" in str(exc):
            raise ValueError("release manifest SBOM changed during read") from exc
        raise ValueError("release manifest SBOM could not be opened safely") from exc
    except OSError as exc:
        raise ValueError("release manifest SBOM could not be opened safely") from exc
    try:
        after_path = os.lstat(target)
    except OSError as exc:
        raise ValueError("release manifest SBOM changed during read") from exc
    if (
        _is_reparse_point(after_path)
        or not stat.S_ISREG(after_path.st_mode)
        or file_fingerprint(after_path) != fingerprint
    ):
        raise ValueError("release manifest SBOM changed during read")
    return content, secure_relative


def create_manifest(
    release_directory: Path,
    artifacts: list[Path],
    sbom: Path,
    *,
    release_label: str,
    acceptance_result: Path,
    repository_root: Path,
    release_kind: str = RELEASE_KINDS[0],
) -> dict[str, Any]:
    acceptance_sha256, _ = _hash_file(acceptance_result)
    acceptance = json.loads(acceptance_result.read_text(encoding="utf-8"))
    if acceptance.get("status") != "PASS" or acceptance.get("tasks", 0) < 50:
        raise ValueError("release manifest requires a passing 50-task acceptance result")
    if release_kind not in RELEASE_KINDS:
        raise ValueError(f"unknown release kind: {release_kind!r}")
    sbom_bytes, sbom_path = _read_sbom_bytes(release_directory, sbom)
    try:
        sbom_document = json.loads(sbom_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("release manifest SBOM must be valid UTF-8 JSON") from exc
    validate_sbom_document(sbom_document)
    sbom_component = sbom_document["metadata"]["component"]
    if sbom_component["version"] != _PROJECT_VERSION:
        raise ValueError(
            f"release manifest SBOM application version must be {_PROJECT_VERSION}"
        )
    return {
        "manifest_version": 1,
        "project_version": _PROJECT_VERSION,
        "release_label": release_label,
        "release_kind": release_kind,
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
        "sbom": {
            "format": sbom_document["bomFormat"],
            "spec_version": sbom_document["specVersion"],
            "path": sbom_path,
            "sha256": hashlib.sha256(sbom_bytes).hexdigest(),
            "size": len(sbom_bytes),
        },
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
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-kind", choices=RELEASE_KINDS, default=RELEASE_KINDS[0])
    args = parser.parse_args()
    manifest = create_manifest(
        args.release_directory,
        args.artifact,
        args.sbom,
        release_label=args.release_label,
        acceptance_result=args.acceptance_result,
        repository_root=args.repository_root,
        release_kind=args.release_kind,
    )
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
