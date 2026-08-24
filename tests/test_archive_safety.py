from __future__ import annotations

import hashlib
import stat
import warnings
import zipfile
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_common import archive as archive_module
from hermes_common.archive import (
    ZipBudget,
    ZipInspection,
    ZipMember,
    copy_bounded,
    inspect_zip,
    require_free_space,
)


def make_budget(**overrides: object) -> ZipBudget:
    values: dict[str, object] = {
        "max_container_bytes": 1_000_000,
        "max_members": 10,
        "max_member_bytes": 100,
        "max_total_bytes": 500,
        "max_member_compression_ratio": 100.0,
        "max_total_compression_ratio": 100.0,
        "allow_directories": False,
        "allowed_compression_methods": frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}),
    }
    values.update(overrides)
    return ZipBudget(**values)


def write_zip(
    path: Path,
    entries: list[tuple[str, bytes]],
    *,
    compression: int = zipfile.ZIP_STORED,
) -> None:
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)


def write_special_member(path: Path, name: str, mode: int) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (mode | 0o700) << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, b"payload")


def test_value_objects_are_immutable_and_inspection_has_frozen_members(tmp_path: Path) -> None:
    archive_path = tmp_path / "valid.zip"
    write_zip(archive_path, [("hello.txt", b"hello")])

    budget = make_budget()
    inspection = inspect_zip(archive_path, budget)

    assert isinstance(inspection, ZipInspection)
    assert inspection.path == archive_path
    assert inspection.member_count == 1
    assert inspection.total_expanded_bytes == 5
    assert inspection.total_compressed_bytes == 5
    assert inspection.members == (
        ZipMember(
            name="hello.txt",
            compressed_bytes=5,
            expanded_bytes=5,
            compression_method=zipfile.ZIP_STORED,
        ),
    )
    with pytest.raises(FrozenInstanceError):
        budget.max_members = 2  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        inspection.members = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        inspection.members[0] = inspection.members[0]  # type: ignore[index]


@pytest.mark.parametrize(
    ("budget_field", "archive_entries", "message"),
    [
        ("max_container_bytes", [("payload", b"12345")], "container"),
        ("max_members", [("one", b"1"), ("two", b"2")], "member"),
        ("max_member_bytes", [("payload", b"12345")], "member"),
        ("max_total_bytes", [("one", b"123"), ("two", b"456")], "total"),
    ],
)
def test_inspection_rejects_size_budgets(
    tmp_path: Path,
    budget_field: str,
    archive_entries: list[tuple[str, bytes]],
    message: str,
) -> None:
    archive_path = tmp_path / "oversized.zip"
    write_zip(archive_path, archive_entries)
    value = {
        "max_container_bytes": archive_path.stat().st_size - 1,
        "max_members": 1,
        "max_member_bytes": 4,
        "max_total_bytes": 5,
    }[budget_field]
    with pytest.raises(ValueError, match=message):
        inspect_zip(archive_path, make_budget(**{budget_field: value}))


def test_inspection_accepts_exact_expanded_and_container_boundaries(tmp_path: Path) -> None:
    archive_path = tmp_path / "boundary.zip"
    write_zip(archive_path, [("payload", b"12345")])

    inspection = inspect_zip(
        archive_path,
        make_budget(
            max_container_bytes=archive_path.stat().st_size,
            max_member_bytes=5,
            max_total_bytes=5,
        ),
    )

    assert inspection.total_expanded_bytes == 5


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/drive", "//server/share"])
def test_inspection_rejects_unsafe_member_names(tmp_path: Path, name: str) -> None:
    archive_path = tmp_path / "unsafe-name.zip"
    write_zip(archive_path, [(name, b"x")])

    with pytest.raises(ValueError, match="unsafe|traversal|absolute|drive|UNC|backslash"):
        inspect_zip(archive_path, make_budget())


def test_inspection_rejects_backslash_member_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "unsafe-backslash.zip"
    write_zip(archive_path, [("safe-name", b"x")])
    original_infolist = zipfile.ZipFile.infolist

    def lying_infolist(self: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        info = original_infolist(self)[0]
        info.filename = r"safe\name"
        return [info]

    monkeypatch.setattr(zipfile.ZipFile, "infolist", lying_infolist)
    with pytest.raises(ValueError, match="backslash"):
        inspect_zip(archive_path, make_budget())


def test_inspection_rejects_duplicate_names(tmp_path: Path) -> None:
    archive_path = tmp_path / "duplicate.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        write_zip(archive_path, [("same.txt", b"one"), ("same.txt", b"two")])

    with pytest.raises(ValueError, match="duplicate"):
        inspect_zip(archive_path, make_budget())


def test_inspection_rejects_directories_when_budget_disallows_them(tmp_path: Path) -> None:
    archive_path = tmp_path / "directory.zip"
    write_zip(archive_path, [("nested/", b"")])

    with pytest.raises(ValueError, match="directory"):
        inspect_zip(archive_path, make_budget(allow_directories=False))


def test_inspection_can_allow_safe_directories(tmp_path: Path) -> None:
    archive_path = tmp_path / "allowed-directory.zip"
    write_zip(archive_path, [("nested/", b""), ("nested/file.txt", b"x")])

    inspection = inspect_zip(archive_path, make_budget(allow_directories=True))

    assert inspection.member_count == 2
    assert inspection.members[0].is_directory is True


@pytest.mark.parametrize("mode", [stat.S_IFLNK, stat.S_IFIFO, stat.S_IFCHR])
def test_inspection_rejects_symlink_and_special_external_attributes(
    tmp_path: Path, mode: int
) -> None:
    archive_path = tmp_path / "special.zip"
    write_special_member(archive_path, "entry", mode)

    with pytest.raises(ValueError, match="symlink|special"):
        inspect_zip(archive_path, make_budget())


def test_inspection_rejects_unsupported_compression_method(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsupported.zip"
    write_zip(archive_path, [("payload", b"payload")], compression=zipfile.ZIP_BZIP2)

    with pytest.raises(ValueError, match="unsupported compression"):
        inspect_zip(
            archive_path,
            make_budget(allowed_compression_methods=frozenset({zipfile.ZIP_STORED})),
        )


def test_inspection_rejects_zero_compressed_size_for_non_empty_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "zero-compressed.zip"
    write_zip(archive_path, [("payload", b"x")])
    original_infolist = zipfile.ZipFile.infolist

    def lying_infolist(self: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        info = original_infolist(self)[0]
        info.compress_size = 0
        return [info]

    monkeypatch.setattr(zipfile.ZipFile, "infolist", lying_infolist)
    with pytest.raises(ValueError, match="zero compressed"):
        inspect_zip(archive_path, make_budget())


def test_inspection_rejects_per_member_compression_ratio(tmp_path: Path) -> None:
    archive_path = tmp_path / "member-ratio.zip"
    write_zip(archive_path, [("payload", b"a" * 80)], compression=zipfile.ZIP_DEFLATED)

    with pytest.raises(ValueError, match="compression ratio"):
        inspect_zip(archive_path, make_budget(max_member_compression_ratio=1.0))


def test_inspection_rejects_total_compression_ratio(tmp_path: Path) -> None:
    archive_path = tmp_path / "total-ratio.zip"
    write_zip(
        archive_path,
        [("one", b"a" * 40), ("two", b"b" * 40)],
        compression=zipfile.ZIP_DEFLATED,
    )

    with pytest.raises(ValueError, match="compression ratio"):
        inspect_zip(
            archive_path,
            make_budget(
                max_member_compression_ratio=100.0,
                max_total_compression_ratio=1.0,
            ),
        )


def test_copy_bounded_uses_streamed_bytes_and_cleans_owned_destination_on_overflow(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    destination = tmp_path / "owned.bin"
    payload = b"source bytes are larger than the declared metadata"
    source.write_bytes(payload)
    source_before = hashlib.sha256(source.read_bytes()).hexdigest()

    with pytest.raises(ValueError, match="max_bytes"):
        copy_bounded(source, destination, max_bytes=4, expected_bytes=4)

    assert not destination.exists()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_before


def test_copy_bounded_accepts_exact_boundary_and_expected_size(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    destination = tmp_path / "nested" / "destination.bin"
    payload = b"exact boundary"
    source.write_bytes(payload)

    result = copy_bounded(
        source,
        destination,
        max_bytes=len(payload),
        expected_bytes=len(payload),
    )

    assert result == len(payload)
    assert destination.read_bytes() == payload


def test_copy_bounded_rejects_expected_size_mismatch_and_removes_partial(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    source.write_bytes(b"payload")

    with pytest.raises(ValueError, match="expected_bytes"):
        copy_bounded(source, destination, max_bytes=100, expected_bytes=99)

    assert not destination.exists()


def test_copy_bounded_preserves_existing_destination_when_promotion_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    destination = tmp_path / "destination.bin"
    source.write_bytes(b"new payload")
    destination.write_bytes(b"old destination")

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise OSError("promotion failed")

    monkeypatch.setattr(archive_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="promotion failed"):
        copy_bounded(source, destination, max_bytes=100, expected_bytes=11)

    assert destination.read_bytes() == b"old destination"
    assert not any(path.name.startswith(f".{destination.name}.") for path in tmp_path.iterdir())


def test_copy_bounded_rejects_same_source_and_destination_without_mutation(tmp_path: Path) -> None:
    source = tmp_path / "same.bin"
    source.write_bytes(b"original")

    with pytest.raises(ValueError, match="source and destination"):
        copy_bounded(source, source, max_bytes=100)

    assert source.read_bytes() == b"original"


def test_require_free_space_reports_shortage_and_available_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        archive_module.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=9),
    )

    with pytest.raises(OSError, match=r"required=15.*available=9"):
        require_free_space(tmp_path, 10, margin_bytes=5)


def test_require_free_space_returns_available_bytes_when_preflight_is_sufficient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        archive_module.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=20),
    )

    assert require_free_space(tmp_path, 10, margin_bytes=5) == 20
