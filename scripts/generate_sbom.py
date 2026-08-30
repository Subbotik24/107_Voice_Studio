"""Generate a deterministic CycloneDX 1.6 SBOM from an exact-version lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.+_-]*[A-Za-z0-9])?$")
_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]|\\\\|(?:\.\.?)[\\/])")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA = "https://cyclonedx.org/schema/bom-1.6.schema.json"
_SCOPE = "windows-x64-release-environment"
_SOURCE_LOCK = "requirements-windows.lock"


@dataclass(frozen=True, order=True)
class LockedComponent:
    name: str
    version: str


def _normalise_name(name: str) -> str:
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"invalid package name: {name!r}")
    return re.sub(r"[-_.]+", "-", name).lower()


def _validate_version(version: str) -> str:
    if not _VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid package version: {version!r}")
    return version


def parse_locked_components(text: str) -> list[LockedComponent]:
    """Parse comments/blanks and only exact ``name==version`` lock rows."""
    if not isinstance(text, str):
        raise TypeError("lock text must be a string")

    components: list[LockedComponent] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(
            r"([A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)==([^\s]+)", raw_line
        )
        if match is None:
            raise ValueError(f"line {line_number}: expected an exact name==version row")
        name = _normalise_name(match.group(1))
        version = _validate_version(match.group(2))
        if name in seen:
            raise ValueError(f"line {line_number}: duplicate package name: {name}")
        seen.add(name)
        components.append(LockedComponent(name, version))
    return sorted(components)


def _purl(component: LockedComponent) -> str:
    return f"pkg:pypi/{quote(component.name, safe='')}@{quote(component.version, safe='')}"


def build_sbom(lock_text: str, *, project_name: str, project_version: str) -> dict[str, object]:
    components = parse_locked_components(lock_text)
    canonical_lock = "".join(
        f"{item.name}=={item.version}\n" for item in components
    ).encode("utf-8")
    lock_digest = hashlib.sha256(canonical_lock).hexdigest()
    document: dict[str, object] = {
        "$schema": _SCHEMA,
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "version": 1,
        "metadata": {
            "component": {
                "name": project_name,
                "type": "application",
                "version": project_version,
            }
        },
        "components": [
            {
                "bom-ref": _purl(item),
                "name": item.name,
                "purl": _purl(item),
                "type": "library",
                "version": item.version,
            }
            for item in components
        ],
        "properties": [
            {"name": "voice-studio:lock-sha256", "value": lock_digest},
            {"name": "voice-studio:sbom-scope", "value": _SCOPE},
            {"name": "voice-studio:source-lock", "value": _SOURCE_LOCK},
        ],
    }
    validate_sbom_document(document)
    return document


def _reject_path_string(value: str) -> None:
    if _PATH_RE.match(value):
        raise ValueError("SBOM contains an absolute or path-like string")


def _require_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    _reject_path_string(value)
    return value


def validate_sbom_document(document: object) -> None:
    """Validate the exact deterministic profile emitted by :func:`build_sbom`."""
    if not isinstance(document, dict):
        raise ValueError("SBOM must be an object")
    expected_top = {
        "$schema", "bomFormat", "specVersion", "version", "metadata", "components", "properties"
    }
    if set(document) != expected_top:
        raise ValueError("SBOM has unexpected or missing top-level fields")
    if (
        document["$schema"] != _SCHEMA
        or document["bomFormat"] != "CycloneDX"
        or document["specVersion"] != "1.6"
    ):
        raise ValueError("SBOM constants are invalid")
    if type(document["version"]) is not int or document["version"] != 1:
        raise ValueError("SBOM version must be integer 1")

    metadata = document["metadata"]
    if not isinstance(metadata, dict) or set(metadata) != {"component"}:
        raise ValueError("metadata must contain only component")
    app = metadata["component"]
    if not isinstance(app, dict) or set(app) != {"name", "type", "version"}:
        raise ValueError("metadata component fields are invalid")
    _require_string(app["name"], "metadata component name")
    _require_string(app["version"], "metadata component version")
    if app["type"] != "application":
        raise ValueError("metadata component type must be application")

    properties = document["properties"]
    if not isinstance(properties, list) or len(properties) != 3:
        raise ValueError("properties must contain exactly three entries")
    property_names: list[str] = []
    property_map: dict[str, str] = {}
    for index, item in enumerate(properties):
        if not isinstance(item, dict) or set(item) != {"name", "value"}:
            raise ValueError(f"property {index} is invalid")
        name = _require_string(item["name"], f"property {index} name")
        value = _require_string(item["value"], f"property {index} value")
        property_names.append(name)
        if name in property_map:
            raise ValueError(f"duplicate property: {name}")
        property_map[name] = value
    if property_names != sorted(property_names) or property_names != [
        "voice-studio:lock-sha256",
        "voice-studio:sbom-scope",
        "voice-studio:source-lock",
    ]:
        raise ValueError("properties are not the exact sorted profile")
    if not _SHA256_RE.fullmatch(property_map["voice-studio:lock-sha256"]):
        raise ValueError("lock digest must be a lowercase SHA-256 value")
    if (
        property_map["voice-studio:sbom-scope"] != _SCOPE
        or property_map["voice-studio:source-lock"] != _SOURCE_LOCK
    ):
        raise ValueError("SBOM properties contain invalid constants")

    components = document["components"]
    if not isinstance(components, list):
        raise ValueError("components must be a list")
    previous: tuple[str, str] | None = None
    seen: set[tuple[str, str]] = set()
    seen_names: set[str] = set()
    for index, item in enumerate(components):
        if not isinstance(item, dict) or set(item) != {
            "bom-ref", "name", "purl", "type", "version"
        }:
            raise ValueError(f"component {index} fields are invalid")
        name = _require_string(item["name"], f"component {index} name")
        version = _require_string(item["version"], f"component {index} version")
        if _normalise_name(name) != name or not _VERSION_RE.fullmatch(version):
            raise ValueError(f"component {index} name/version is invalid")
        if item["type"] != "library":
            raise ValueError(f"component {index} type must be library")
        expected_ref = _purl(LockedComponent(name, version))
        if item["bom-ref"] != expected_ref or item["purl"] != expected_ref:
            raise ValueError(f"component {index} purl or bom-ref is invalid")
        key = (name, version)
        if name in seen_names or key in seen:
            raise ValueError(f"duplicate component: {name}=={version}")
        if previous is not None and key < previous:
            raise ValueError("components are not sorted")
        seen.add(key)
        seen_names.add(name)
        previous = key


def _write_atomically(document: dict[str, object], output: Path) -> None:
    validate_sbom_document(document)
    output = output.resolve()
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=output.parent,
            prefix=f".{output.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--project-version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        lock_text = args.lock.read_text(encoding="utf-8")
        document = build_sbom(
            lock_text, project_name=args.project_name, project_version=args.project_version
        )
        _write_atomically(document, args.output)
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
