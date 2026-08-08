#!/usr/bin/env python3
"""Build checked faster-whisper model assets for the separate models-v1 release.

Run only from trusted, manually downloaded upstream model directories. The
script intentionally does not download models or write any model binary to Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(directory: Path) -> tuple[dict[str, str], int]:
    files = {
        path.relative_to(directory).as_posix(): sha256(path)
        for path in directory.rglob("*")
        if path.is_file()
    }
    if "model.bin" not in files or "config.json" not in files:
        raise ValueError(f"{directory} is not a faster-whisper model directory")
    return dict(sorted(files.items())), sum(
        path.stat().st_size for path in directory.rglob("*") if path.is_file()
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", action="append", required=True, metavar="ID=PATH")
    parser.add_argument("--revision", action="append", default=[], metavar="ID=UPSTREAM_REVISION")
    parser.add_argument("--repository", required=True, metavar="OWNER/REPOSITORY")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    revisions = dict(item.split("=", 1) for item in args.revision)
    args.output.mkdir(parents=True, exist_ok=True)
    entries = []
    checksums = []
    for item in args.model:
        model_id, raw_path = item.split("=", 1)
        source = Path(raw_path).expanduser().resolve()
        files, unpacked = inventory(source)
        revision = revisions.get(model_id)
        if not revision:
            raise ValueError(f"missing --revision for {model_id}")
        archive_name = f"faster-whisper-{model_id}-{revision}.zip"
        archive = args.output / archive_name
        with zipfile.ZipFile(
            archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as output:
            for relative in files:
                output.write(source / relative, relative)
        digest = sha256(archive)
        checksums.append(f"{digest}  {archive_name}")
        entries.append(
            {
                "id": model_id,
                "revision": revision,
                "url": f"https://github.com/{args.repository}/releases/download/models-v1/{archive_name}",
                "sha256": digest,
                "archive_bytes": archive.stat().st_size,
                "unpacked_bytes": unpacked,
                "files": files,
                "provenance": f"Systran/faster-whisper-{model_id}",
            }
        )
    registry = {"version": 1, "release": "models-v1", "models": entries}
    (args.output / "model-registry-v1.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    checksums += [f"{sha256(args.output / 'model-registry-v1.json')}  model-registry-v1.json"]
    (args.output / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
