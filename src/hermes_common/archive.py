"""Generic, bounded ZIP inspection and filesystem copy primitives.

The module deliberately contains no product archive format or resource policy.
Callers provide a :class:`ZipBudget` for each format and operation.
"""

from __future__ import annotations

import math
import ntpath
import os
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_ALLOWED_COMPRESSION_METHODS = frozenset(
    {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
)
_SUPPORTED_COMPRESSION_METHODS = frozenset(
    method
    for method in (
        zipfile.ZIP_STORED,
        zipfile.ZIP_DEFLATED,
        getattr(zipfile, "ZIP_BZIP2", None),
        getattr(zipfile, "ZIP_LZMA", None),
        getattr(zipfile, "ZIP_ZSTANDARD", None),
    )
    if method is not None
)
_COPY_CHUNK_BYTES = 1024 * 1024


def _validate_limit(name: str, value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name} must be a non-negative integer or None")


def _validate_ratio(name: str, value: float | None) -> None:
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value <= 0
    ):
        raise ValueError(f"{name} must be a finite positive number or None")


@dataclass(frozen=True, slots=True, init=False)
class ZipBudget:
    """Immutable limits used by :func:`inspect_zip`.

    ``None`` means that a caller has chosen not to impose that particular
    limit.  No product-specific byte or member limits are selected here.
    ``max_archive_bytes``, ``max_member_ratio``, ``max_total_ratio`` and
    ``allowed_methods`` are accepted as compatibility aliases for the
    canonical field names.
    """

    max_container_bytes: int | None
    max_members: int | None
    max_member_bytes: int | None
    max_total_bytes: int | None
    max_member_compression_ratio: float | None
    max_total_compression_ratio: float | None
    allow_directories: bool
    allowed_compression_methods: frozenset[int]

    def __init__(
        self,
        max_container_bytes: int | None = None,
        max_members: int | None = None,
        max_member_bytes: int | None = None,
        max_total_bytes: int | None = None,
        max_member_compression_ratio: float | None = None,
        max_total_compression_ratio: float | None = None,
        allow_directories: bool = False,
        allowed_compression_methods: Iterable[int] | None = None,
        *,
        max_archive_bytes: int | None = None,
        max_member_ratio: float | None = None,
        max_total_ratio: float | None = None,
        allowed_methods: Iterable[int] | None = None,
    ) -> None:
        if max_archive_bytes is not None:
            if max_container_bytes is not None:
                raise TypeError("specify only one of max_container_bytes and max_archive_bytes")
            max_container_bytes = max_archive_bytes
        if max_member_ratio is not None:
            if max_member_compression_ratio is not None:
                raise TypeError(
                    "specify only one of max_member_compression_ratio and max_member_ratio"
                )
            max_member_compression_ratio = max_member_ratio
        if max_total_ratio is not None:
            if max_total_compression_ratio is not None:
                raise TypeError(
                    "specify only one of max_total_compression_ratio and max_total_ratio"
                )
            max_total_compression_ratio = max_total_ratio
        if allowed_methods is not None:
            if allowed_compression_methods is not None:
                raise TypeError(
                    "specify only one of allowed_compression_methods and allowed_methods"
                )
            allowed_compression_methods = allowed_methods

        for name, value in (
            ("max_container_bytes", max_container_bytes),
            ("max_members", max_members),
            ("max_member_bytes", max_member_bytes),
            ("max_total_bytes", max_total_bytes),
        ):
            _validate_limit(name, value)
        _validate_ratio("max_member_compression_ratio", max_member_compression_ratio)
        _validate_ratio("max_total_compression_ratio", max_total_compression_ratio)
        if type(allow_directories) is not bool:
            raise ValueError("allow_directories must be a boolean")

        methods = (
            _DEFAULT_ALLOWED_COMPRESSION_METHODS
            if allowed_compression_methods is None
            else frozenset(allowed_compression_methods)
        )
        if any(type(method) is not int or method < 0 for method in methods):
            raise ValueError("allowed_compression_methods must contain non-negative integers")

        object.__setattr__(self, "max_container_bytes", max_container_bytes)
        object.__setattr__(self, "max_members", max_members)
        object.__setattr__(self, "max_member_bytes", max_member_bytes)
        object.__setattr__(self, "max_total_bytes", max_total_bytes)
        object.__setattr__(self, "max_member_compression_ratio", max_member_compression_ratio)
        object.__setattr__(self, "max_total_compression_ratio", max_total_compression_ratio)
        object.__setattr__(self, "allow_directories", allow_directories)
        object.__setattr__(self, "allowed_compression_methods", methods)

    @property
    def max_archive_bytes(self) -> int | None:
        return self.max_container_bytes

    @property
    def max_member_ratio(self) -> float | None:
        return self.max_member_compression_ratio

    @property
    def max_total_ratio(self) -> float | None:
        return self.max_total_compression_ratio

    @property
    def allowed_methods(self) -> frozenset[int]:
        return self.allowed_compression_methods


@dataclass(frozen=True, slots=True)
class ZipMember:
    """Immutable, bounded metadata for one ZIP member."""

    name: str
    compressed_bytes: int
    expanded_bytes: int
    compression_method: int
    is_directory: bool = False

    @property
    def filename(self) -> str:
        return self.name

    @property
    def compress_size(self) -> int:
        return self.compressed_bytes

    @property
    def file_size(self) -> int:
        return self.expanded_bytes

    @property
    def compress_type(self) -> int:
        return self.compression_method

    @property
    def is_dir(self) -> bool:
        return self.is_directory

    @property
    def compression_ratio(self) -> float | None:
        if self.expanded_bytes == 0:
            return None
        if self.compressed_bytes == 0:
            return math.inf
        return self.expanded_bytes / self.compressed_bytes

    @property
    def ratio(self) -> float | None:
        return self.compression_ratio


@dataclass(frozen=True, slots=True)
class ZipInspection:
    """Immutable result of inspecting a ZIP without extracting it."""

    path: Path
    container_bytes: int
    members: tuple[ZipMember, ...]
    total_expanded_bytes: int
    total_compressed_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path))
        object.__setattr__(self, "members", tuple(self.members))

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def expanded_bytes(self) -> int:
        return self.total_expanded_bytes

    @property
    def compressed_bytes(self) -> int:
        return self.total_compressed_bytes

    @property
    def total_bytes(self) -> int:
        return self.total_expanded_bytes


def _unsafe_name_reason(name: str) -> str | None:
    if not name:
        return "empty"
    if "\x00" in name:
        return "NUL"
    if name.startswith("//") or name.startswith("\\\\"):
        return "UNC"
    if "\\" in name:
        return "backslash"
    if name.startswith("/"):
        return "absolute"
    drive, _ = ntpath.splitdrive(name)
    if drive:
        return "drive"
    if any(part == ".." for part in name.split("/")):
        return "path traversal"
    return None


def _member_kind(info: zipfile.ZipInfo) -> tuple[bool, str | None]:
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    if kind == stat.S_IFLNK:
        return False, "symlink"
    if kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
        return False, "special"
    return info.is_dir() or kind == stat.S_IFDIR, None


def inspect_zip(path: str | os.PathLike[str], budget: ZipBudget) -> ZipInspection:
    """Inspect ZIP metadata and enforce a caller-supplied resource budget.

    The archive is opened only for metadata inspection.  No member is read or
    extracted, and the source archive is never modified.
    """

    if not isinstance(budget, ZipBudget):
        raise TypeError("budget must be a ZipBudget")
    source = Path(path).expanduser()
    try:
        container_bytes = source.stat().st_size
    except FileNotFoundError:
        raise FileNotFoundError(f"ZIP archive not found: {source}") from None
    if budget.max_container_bytes is not None and container_bytes > budget.max_container_bytes:
        raise ValueError(
            "zip container exceeds max_container_bytes: "
            f"actual={container_bytes}, maximum={budget.max_container_bytes}"
        )

    try:
        archive = zipfile.ZipFile(source, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"invalid ZIP archive: {source}") from exc

    try:
        infos = archive.infolist()
        if budget.max_members is not None and len(infos) > budget.max_members:
            raise ValueError(
                "zip archive exceeds max_members: "
                f"actual={len(infos)}, maximum={budget.max_members}"
            )
        seen: set[str] = set()
        members: list[ZipMember] = []
        total_expanded = 0
        total_compressed = 0
        for info in infos:
            name = info.filename
            reason = _unsafe_name_reason(name)
            if reason:
                raise ValueError(f"zip archive member has unsafe {reason} name: {name!r}")
            if name in seen:
                raise ValueError(f"zip archive contains duplicate member: {name!r}")
            seen.add(name)

            is_directory, invalid_kind = _member_kind(info)
            if invalid_kind:
                raise ValueError(
                    f"zip archive member is a {invalid_kind} or special entry: {name!r}"
                )
            if is_directory and not budget.allow_directories:
                raise ValueError(f"zip archive directory member is not allowed: {name!r}")
            if info.compress_type not in _SUPPORTED_COMPRESSION_METHODS:
                raise ValueError(
                    "zip archive uses unsupported compression method: "
                    f"name={name!r}, method={info.compress_type}"
                )
            if info.compress_type not in budget.allowed_compression_methods:
                raise ValueError(
                    "zip archive uses unsupported compression method: "
                    f"name={name!r}, method={info.compress_type}"
                )
            expanded = info.file_size
            compressed = info.compress_size
            if (
                type(expanded) is not int
                or expanded < 0
                or type(compressed) is not int
                or compressed < 0
            ):
                raise ValueError(f"zip archive member has invalid size metadata: {name!r}")
            if expanded > 0 and compressed == 0:
                raise ValueError(
                    "zip member has zero compressed bytes for non-empty data: "
                    f"name={name!r}, expanded={expanded}"
                )
            if budget.max_member_bytes is not None and expanded > budget.max_member_bytes:
                raise ValueError(
                    "zip member exceeds max_member_bytes: "
                    f"name={name!r}, actual={expanded}, maximum={budget.max_member_bytes}"
                )
            total_expanded += expanded
            total_compressed += compressed
            if budget.max_total_bytes is not None and total_expanded > budget.max_total_bytes:
                raise ValueError(
                    "zip members exceed max_total_bytes: "
                    f"actual={total_expanded}, maximum={budget.max_total_bytes}"
                )
            ratio = expanded / compressed if compressed else None
            if (
                ratio is not None
                and budget.max_member_compression_ratio is not None
                and ratio > budget.max_member_compression_ratio
            ):
                raise ValueError(
                    "zip member compression ratio exceeds max_member_compression_ratio: "
                    f"name={name!r}, ratio={ratio:.6g}, "
                    f"maximum={budget.max_member_compression_ratio}"
                )
            members.append(
                ZipMember(
                    name=name,
                    compressed_bytes=compressed,
                    expanded_bytes=expanded,
                    compression_method=info.compress_type,
                    is_directory=is_directory,
                )
            )

        if (
            total_expanded > 0
            and total_compressed == 0
            and budget.max_total_compression_ratio is not None
        ):
            raise ValueError(
                "zip total compression ratio cannot be bounded with zero compressed bytes"
            )
        if total_expanded > 0 and total_compressed > 0:
            total_ratio = total_expanded / total_compressed
            if (
                budget.max_total_compression_ratio is not None
                and total_ratio > budget.max_total_compression_ratio
            ):
                raise ValueError(
                    "zip total compression ratio exceeds max_total_compression_ratio: "
                    f"ratio={total_ratio:.6g}, maximum={budget.max_total_compression_ratio}"
                )
        return ZipInspection(
            path=source,
            container_bytes=container_bytes,
            members=tuple(members),
            total_expanded_bytes=total_expanded,
            total_compressed_bytes=total_compressed,
        )
    finally:
        archive.close()


def _same_path(source: Path, destination: Path) -> bool:
    try:
        return os.path.samefile(source, destination)
    except (FileNotFoundError, OSError):
        return source.resolve() == destination.resolve()


def copy_bounded(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    max_bytes: int,
    expected_bytes: int | None = None,
) -> int:
    """Stream ``source`` into an atomic destination subject to byte limits.

    Bytes are counted as they are read rather than taken from filesystem
    metadata.  A temporary sibling is owned by this call and is removed on
    every failure; an existing destination is replaced only after success.
    """

    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    if expected_bytes is not None and (type(expected_bytes) is not int or expected_bytes < 0):
        raise ValueError("expected_bytes must be a non-negative integer or None")
    if expected_bytes is not None and expected_bytes > max_bytes:
        raise ValueError(
            f"expected_bytes={expected_bytes} exceeds max_bytes={max_bytes}"
        )

    source_path = Path(source).expanduser()
    destination_path = Path(destination).expanduser()
    if not source_path.is_file():
        raise FileNotFoundError(f"copy source not found: {source_path}")
    if _same_path(source_path, destination_path):
        raise ValueError("source and destination must be different paths")
    if destination_path.exists() and destination_path.is_dir():
        raise IsADirectoryError(f"copy destination is a directory: {destination_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination_path.name}.",
        suffix=".partial",
        dir=destination_path.parent,
    )
    temporary = Path(temporary_name)
    descriptor_open = True
    copied = 0
    try:
        with source_path.open("rb") as input_stream, os.fdopen(descriptor, "wb") as output_stream:
            descriptor_open = False
            while True:
                block = input_stream.read(_COPY_CHUNK_BYTES)
                if not block:
                    break
                copied += len(block)
                if copied > max_bytes:
                    raise ValueError(
                        f"bounded copy exceeds max_bytes={max_bytes}: actual={copied}"
                    )
                output_stream.write(block)
            if expected_bytes is not None and copied != expected_bytes:
                raise ValueError(
                    f"bounded copy expected_bytes={expected_bytes}, actual={copied}"
                )
        os.replace(temporary, destination_path)
        return copied
    finally:
        if descriptor_open:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _disk_usage_path(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def require_free_space(
    path: str | os.PathLike[str],
    required_bytes: int,
    *,
    margin_bytes: int,
) -> int:
    """Perform an advisory free-space preflight and return available bytes.

    The check is intentionally advisory: callers must still handle a later
    write failure because free space can change immediately after this call.
    """

    if type(required_bytes) is not int or required_bytes < 0:
        raise ValueError("required_bytes must be a non-negative integer")
    if type(margin_bytes) is not int or margin_bytes < 0:
        raise ValueError("margin_bytes must be a non-negative integer")
    probe = _disk_usage_path(Path(path).expanduser())
    available = int(shutil.disk_usage(probe).free)
    required = required_bytes + margin_bytes
    if available < required:
        raise OSError(
            "not enough free space: "
            f"required={required} bytes (payload={required_bytes}, margin={margin_bytes}), "
            f"available={available} bytes at {probe}"
        )
    return available
