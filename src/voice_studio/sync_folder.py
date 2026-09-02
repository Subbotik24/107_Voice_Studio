"""Local mirror export into a user-chosen sync folder ("Папка синхронізації").

This module mirrors transcripts (Markdown + JSON, optionally the retained
managed audio copy) into a folder the user points at their own cloud client's
desktop folder (Google Drive, OneDrive, ...), so *that* client uploads the
files. VOICE Studio itself makes no network call here and never reads an
external (unmanaged) original file.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import Transcript

_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_MAX_SLUG_LENGTH = 60


class SyncFolderError(ValueError):
    """Raised when a sync folder or a mirror write cannot proceed safely."""


@dataclass
class SyncSummary:
    written: int
    audio: int
    failed: tuple[tuple[str, str], ...]


def _is_reparse_point(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def validate_sync_root(root: Path, *, data_root: Path) -> Path:
    """Validate a user-chosen sync folder and return its resolved path.

    The folder must already exist as a real directory — not a symlink or a
    Windows reparse point — and must neither sit inside, nor contain, the
    application's private data root, so a mirrored copy can never land inside
    (or beside) the managed database/sources/backups directory tree.
    """

    candidate = Path(root).expanduser()
    try:
        entry = candidate.lstat()
    except OSError as exc:
        raise SyncFolderError(f"sync folder does not exist: {candidate}") from exc
    if _is_reparse_point(entry):
        raise SyncFolderError(
            f"sync folder must not be a symlink or reparse point: {candidate}"
        )
    if not stat.S_ISDIR(entry.st_mode):
        raise SyncFolderError(f"sync folder is not a directory: {candidate}")

    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise SyncFolderError(f"sync folder could not be resolved: {candidate}") from exc
    resolved_info = resolved.lstat()
    if _is_reparse_point(resolved_info) or not stat.S_ISDIR(resolved_info.st_mode):
        raise SyncFolderError(f"sync folder is not a real directory: {resolved}")

    try:
        resolved_data_root = Path(data_root).expanduser().resolve()
    except OSError as exc:
        raise SyncFolderError(
            f"application data folder could not be resolved: {data_root}"
        ) from exc

    if _is_within(resolved, resolved_data_root):
        raise SyncFolderError(
            f"sync folder must not be inside the application data folder: {resolved}"
        )
    if _is_within(resolved_data_root, resolved):
        raise SyncFolderError(
            f"sync folder must not contain the application data folder: {resolved}"
        )
    return resolved


def _slugify(source_name: str) -> str:
    """Keep Unicode letters/digits; collapse other runs to a single '-'."""

    pieces: list[str] = []
    in_gap = False
    for character in source_name or "":
        if character.isalpha() or character.isdigit():
            pieces.append(character)
            in_gap = False
        elif not in_gap:
            pieces.append("-")
            in_gap = True
    slug = "".join(pieces).strip("-")
    return slug[:_MAX_SLUG_LENGTH] or "untitled"


def _parse_created_at(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _subfolder(transcript: Transcript) -> str:
    parsed = _parse_created_at(transcript.created_at)
    return parsed.strftime("%Y-%m") if parsed is not None else "undated"


def transcript_mirror_names(transcript: Transcript) -> tuple[str, str]:
    """Return the deterministic (markdown, json) mirror file names."""

    parsed = _parse_created_at(transcript.created_at)
    if parsed is not None:
        date_part = parsed.strftime("%Y-%m-%d")
        time_part = parsed.strftime("%H%M")
    else:
        date_part, time_part = "0000-00-00", "0000"
    slug = _slugify(transcript.source_name)
    id8 = (transcript.id or "")[:8]
    base = f"{date_part}_{time_part}_{slug}_{id8}"
    return f"{base}.md", f"{base}.json"


def _render_markdown(transcript: Transcript) -> str:
    rtf = (
        "n/a"
        if transcript.real_time_factor is None
        else f"{transcript.real_time_factor:.3f}"
    )
    lines = [
        f"# {transcript.source_name}",
        "",
        f"- Created: {transcript.created_at}",
        f"- Language: `{transcript.language}` · Engine: `{transcript.engine}` "
        f"· Model: `{transcript.model}`",
        f"- Audio: `{transcript.audio_seconds:.2f} s` · RTF: `{rtf}`",
        "",
        transcript.corrected_text,
        "",
    ]
    return "\n".join(lines)


def _existing_mirror_root(root: Path, data_root: Path | None) -> Path:
    """Return ``root`` only if it is still a safe, existing mirror target.

    With ``data_root`` the full :func:`validate_sync_root` contract is applied
    again at write time, so a hand-edited or restored ``settings.json`` cannot
    bypass the containment check that Settings → Save performs. Without it the
    folder must at least exist as a real directory: the mirror never creates
    the root itself, so a removed or unmounted folder is reported instead of
    being silently recreated on the local disk.
    """

    if data_root is not None:
        return validate_sync_root(root, data_root=data_root)
    candidate = Path(root).expanduser()
    try:
        entry = candidate.lstat()
    except OSError as exc:
        raise SyncFolderError(f"sync folder does not exist: {candidate}") from exc
    if _is_reparse_point(entry) or not stat.S_ISDIR(entry.st_mode):
        raise SyncFolderError(f"sync folder is not a real directory: {candidate}")
    return candidate


def _atomic_write_text(destination: Path, content: str) -> None:
    # Only the ``YYYY-MM`` sub-folder may be created here; the mirror root has
    # been checked by ``_existing_mirror_root`` and is never (re)created.
    destination.parent.mkdir(exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
        temp_name = None
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass


def _atomic_copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
    )
    os.close(fd)
    try:
        shutil.copyfile(source, temp_name)
        # Windows refuses fsync on a read-only handle (EBADF), so reopen the
        # copy for update: no bytes are written, the data is only flushed.
        with open(temp_name, "r+b") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
        temp_name = None
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink()
            except FileNotFoundError:
                pass


def _resolved_audio_source(transcript: Transcript, sources_root: Path) -> Path | None:
    """Return the managed audio file path, or None if it is unavailable/unsafe.

    Only a path that resolves inside ``sources_root`` and names a real
    (non-symlink) regular file is returned. An external original outside the
    managed sources directory is never opened or read.
    """

    if not transcript.source_path:
        return None
    try:
        resolved_root = Path(sources_root).expanduser().resolve()
        resolved = Path(transcript.source_path).expanduser().resolve()
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    try:
        info = resolved.lstat()
    except OSError:
        return None
    if _is_reparse_point(info) or not stat.S_ISREG(info.st_mode):
        return None
    return resolved


def mirror_transcript(
    transcript: Transcript,
    root: Path,
    *,
    include_audio: bool,
    sources_root: Path,
    data_root: Path | None = None,
) -> tuple[Path, ...]:
    """Write (overwrite) one transcript's mirror files under ``root``.

    Writes are atomic (temp file + fsync + replace) and idempotent — re-running
    with the same transcript overwrites the same deterministic file names.
    Nothing already present under ``root`` is ever deleted, and the root itself
    is never created: it must already exist (and, when ``data_root`` is given,
    pass :func:`validate_sync_root` again) or :class:`SyncFolderError` is raised.
    """

    root = _existing_mirror_root(Path(root), data_root)
    md_name, json_name = transcript_mirror_names(transcript)
    base_name = md_name[: -len(".md")]
    target_dir = root / _subfolder(transcript)
    md_path = target_dir / md_name
    json_path = target_dir / json_name

    _atomic_write_text(md_path, _render_markdown(transcript))
    payload = dict(transcript.to_dict())
    payload.pop("source_path", None)
    _atomic_write_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    written = [md_path, json_path]
    if include_audio and transcript.audio_retained:
        source = _resolved_audio_source(transcript, sources_root)
        if source is not None:
            audio_path = target_dir / f"{base_name}{source.suffix}"
            _atomic_copy_file(source, audio_path)
            written.append(audio_path)
    return tuple(written)


def mirror_all(
    transcripts: Iterable[Transcript],
    root: Path,
    *,
    include_audio: bool,
    sources_root: Path,
    data_root: Path | None = None,
) -> SyncSummary:
    """Mirror every transcript, capturing per-transcript failures instead of raising.

    The root is checked once up front (see :func:`mirror_transcript`); an
    unsafe or missing root raises before any transcript is touched.
    """

    root = _existing_mirror_root(Path(root), data_root)
    written = 0
    audio = 0
    failed: list[tuple[str, str]] = []
    for transcript in transcripts:
        try:
            paths = mirror_transcript(
                transcript,
                root,
                include_audio=include_audio,
                sources_root=sources_root,
                data_root=data_root,
            )
        except Exception as exc:  # noqa: BLE001 - captured per transcript, never raised
            failed.append((transcript.id, str(exc)))
            continue
        written += 1
        if len(paths) > 2:
            audio += 1
    return SyncSummary(written=written, audio=audio, failed=tuple(failed))
