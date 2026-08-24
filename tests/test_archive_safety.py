from __future__ import annotations

import hashlib
import stat
import struct
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
        "max_central_directory_bytes": 1_000_000,
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


def central_directory_size(path: Path) -> int:
    payload = path.read_bytes()
    eocd_offset = payload.rfind(b"PK\x05\x06")
    assert eocd_offset >= 0
    return int.from_bytes(payload[eocd_offset + 12 : eocd_offset + 16], "little")


def make_zip64_fixture(path: Path) -> None:
    write_zip(path, [("payload", b"x")])
    payload = path.read_bytes()
    eocd_offset = payload.rfind(b"PK\x05\x06")
    entries = int.from_bytes(payload[eocd_offset + 10 : eocd_offset + 12], "little")
    directory_size = int.from_bytes(payload[eocd_offset + 12 : eocd_offset + 16], "little")
    directory_offset = int.from_bytes(payload[eocd_offset + 16 : eocd_offset + 20], "little")
    zip64_eocd = struct.pack(
        "<4sQHHIIQQQQ",
        b"PK\x06\x06",
        44,
        45,
        45,
        0,
        0,
        entries,
        entries,
        directory_size,
        directory_offset,
    )
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, eocd_offset, 1)
    legacy_eocd = struct.pack(
        "<4s4H2LH",
        b"PK\x05\x06",
        0,
        0,
        0xFFFF,
        0xFFFF,
        0xFFFFFFFF,
        0xFFFFFFFF,
        0,
    )
    path.write_bytes(payload[:eocd_offset] + zip64_eocd + locator + legacy_eocd)


def make_nonsentinel_zip64_fixture(path: Path) -> None:
    write_zip(path, [("payload.bin", b"x" * 499_902)])
    payload = path.read_bytes()
    eocd_offset = payload.rfind(b"PK\x05\x06")
    assert eocd_offset == 500_000
    entries = int.from_bytes(payload[eocd_offset + 10 : eocd_offset + 12], "little")
    directory_size = int.from_bytes(payload[eocd_offset + 12 : eocd_offset + 16], "little")
    directory_offset = int.from_bytes(payload[eocd_offset + 16 : eocd_offset + 20], "little")
    zip64_eocd = struct.pack(
        "<4sQHHIIQQQQ",
        b"PK\x06\x06",
        44,
        45,
        45,
        0,
        0,
        entries,
        entries,
        500_000,
        0,
    )
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, eocd_offset, 1)
    legacy_eocd = struct.pack(
        "<4s4H2LH",
        b"PK\x05\x06",
        0,
        0,
        entries,
        entries,
        directory_size,
        directory_offset,
        0,
    )
    path.write_bytes(payload[:eocd_offset] + zip64_eocd + locator + legacy_eocd)


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


@pytest.mark.parametrize("name", ["a/./b", "a//b"])
def test_inspection_rejects_dot_and_empty_path_segments(tmp_path: Path, name: str) -> None:
    archive_path = tmp_path / "unsafe-segment.zip"
    write_zip(archive_path, [(name, b"x")])

    with pytest.raises(ValueError, match="unsafe|empty|dot"):
        inspect_zip(archive_path, make_budget())


@pytest.mark.parametrize(
    "name",
    [
        "plain.",
        "plain ",
        "stream:payload",
        "bad*name",
        "bad?name",
        "bad\x01name",
        "CON.txt",
        "folder/NUL",
    ],
)
def test_inspection_rejects_nonportable_windows_member_segments(
    tmp_path: Path, name: str
) -> None:
    archive_path = tmp_path / "nonportable.zip"
    write_zip(archive_path, [(name, b"x")])

    with pytest.raises(ValueError, match="unsafe|portable|reserved|ADS|character"):
        inspect_zip(archive_path, make_budget())


def test_inspection_rejects_casefolded_aliases_per_segment(tmp_path: Path) -> None:
    archive_path = tmp_path / "case-alias.zip"
    write_zip(archive_path, [("Dir/A.txt", b"one"), ("dir/a.TXT", b"two")])

    with pytest.raises(ValueError, match="alias|collision"):
        inspect_zip(archive_path, make_budget())


def test_inspection_rejects_unicode_normalization_aliases(tmp_path: Path) -> None:
    archive_path = tmp_path / "unicode-alias.zip"
    write_zip(archive_path, [("café.txt", b"one"), ("cafe\u0301.txt", b"two")])

    with pytest.raises(ValueError, match="alias|collision"):
        inspect_zip(archive_path, make_budget())


def test_member_count_is_rejected_before_zipfile_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "member-count-preflight.zip"
    write_zip(archive_path, [("one", b"1"), ("two", b"2")])

    def zipfile_must_not_load(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile must not load before member-count preflight")

    monkeypatch.setattr(archive_module.zipfile, "ZipFile", zipfile_must_not_load)
    with pytest.raises(ValueError, match="max_members"):
        inspect_zip(archive_path, make_budget(max_members=1))


def test_central_directory_size_is_rejected_before_zipfile_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "central-directory-preflight.zip"
    write_zip(archive_path, [("payload", b"payload")])
    directory_size = central_directory_size(archive_path)

    def zipfile_must_not_load(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile must not load before central-size preflight")

    monkeypatch.setattr(archive_module.zipfile, "ZipFile", zipfile_must_not_load)
    with pytest.raises(ValueError, match="central directory"):
        inspect_zip(
            archive_path,
            make_budget(max_central_directory_bytes=directory_size - 1),
        )


def test_eocd_preflight_uses_last_signature_candidate_before_zipfile_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "forged-last-eocd.zip"
    write_zip(archive_path, [("payload.bin", b"x" * 500_000)])
    payload = bytearray(archive_path.read_bytes())
    eocd_offset = payload.rfind(b"PK\x05\x06")
    assert eocd_offset >= 0
    assert int.from_bytes(payload[eocd_offset + 12 : eocd_offset + 16], "little") == 57

    forged_eocd = struct.pack(
        "<4s4H2LH",
        b"PK\x05\x06",
        0,
        0,
        1,
        1,
        500_000,
        0,
        1,
    )
    payload[eocd_offset + 20 : eocd_offset + 22] = len(forged_eocd).to_bytes(2, "little")
    archive_path.write_bytes(bytes(payload) + forged_eocd)

    def zipfile_must_not_load(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile must not load a forged last EOCD")

    monkeypatch.setattr(archive_module.zipfile, "ZipFile", zipfile_must_not_load)
    with pytest.raises(ValueError, match="central directory"):
        inspect_zip(
            archive_path,
            make_budget(max_central_directory_bytes=100),
        )


@pytest.mark.parametrize(
    "entries",
    [
        [("parent", b"one"), ("parent/child", b"two")],
        [("parent/child", b"two"), ("parent", b"one")],
        [("Dir", b"one"), ("dir/child", b"two")],
        [("dir/child", b"two"), ("Dir", b"one")],
    ],
)
def test_inspection_rejects_regular_file_ancestor_conflicts(
    tmp_path: Path, entries: list[tuple[str, bytes]]
) -> None:
    archive_path = tmp_path / "file-ancestor-conflict.zip"
    write_zip(archive_path, entries)

    with pytest.raises(ValueError, match="ancestor|hierarchy|file|path"):
        inspect_zip(archive_path, make_budget())


def test_hierarchy_check_does_not_scan_all_prior_identities() -> None:
    trie = archive_module._CanonicalPathTrie()
    for index in range(2_000):
        trie.add((f"sibling-{index}",), f"sibling-{index}", is_directory=False)

    assert trie.node_count == 2_001


def test_deep_canonical_path_uses_one_trie_node_per_component() -> None:
    components = tuple(f"component-{index}" for index in range(4_000))
    trie = archive_module._CanonicalPathTrie()

    trie.add(components, "deep-member", is_directory=False)

    assert trie.node_count == len(components) + 1
    node = trie.root
    for component in components:
        node = node.children[component]
    assert node.is_member is True
    assert node.is_regular_file is True
    assert node.is_directory is False


def test_deep_member_metadata_does_not_slice_prefix_tuples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "deep-metadata.zip"
    write_zip(archive_path, [("placeholder", b"x")])
    deep_name = "/".join(f"part-{index}" for index in range(4_000))
    slice_count = [0]

    class CountingIdentity(tuple):
        def __new__(cls, values: tuple[str, ...]):
            instance = super().__new__(cls, values)
            instance.slice_count = slice_count
            return instance

        def __getitem__(self, item):  # type: ignore[no-untyped-def]
            result = super().__getitem__(item)
            if isinstance(item, slice):
                self.slice_count[0] += 1
                return CountingIdentity(result)
            return result

    identity = CountingIdentity(tuple(f"component-{index}" for index in range(4_000)))
    original_infolist = zipfile.ZipFile.infolist

    def deep_infolist(self: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        info = original_infolist(self)[0]
        info.filename = deep_name
        return [info]

    monkeypatch.setattr(zipfile.ZipFile, "infolist", deep_infolist)
    monkeypatch.setattr(
        archive_module,
        "_portable_member_identity",
        lambda name, is_directory: identity,
    )

    inspection = inspect_zip(archive_path, make_budget())

    assert inspection.member_count == 1
    assert slice_count[0] == 0


def test_inspection_rejects_truncated_eocd_before_zipfile_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "truncated-eocd.zip"
    write_zip(archive_path, [("payload", b"x")])
    archive_path.write_bytes(archive_path.read_bytes()[:-8])

    def zipfile_must_not_load(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile must not load malformed EOCD")

    monkeypatch.setattr(archive_module.zipfile, "ZipFile", zipfile_must_not_load)
    with pytest.raises(ValueError, match="EOCD|end-of-central"):
        inspect_zip(archive_path, make_budget())


def test_inspection_rejects_contradictory_eocd_bounds(tmp_path: Path) -> None:
    archive_path = tmp_path / "contradictory-eocd.zip"
    write_zip(archive_path, [("payload", b"x")])
    payload = bytearray(archive_path.read_bytes())
    eocd_offset = payload.rfind(b"PK\x05\x06")
    payload[eocd_offset + 16 : eocd_offset + 20] = (0xFFFFFFFF).to_bytes(4, "little")
    archive_path.write_bytes(payload)

    with pytest.raises(ValueError, match="central directory|ZIP64|EOCD"):
        inspect_zip(archive_path, make_budget())


def test_inspection_accepts_bounded_zip64_metadata(tmp_path: Path) -> None:
    archive_path = tmp_path / "zip64.zip"
    make_zip64_fixture(archive_path)

    inspection = inspect_zip(archive_path, make_budget())

    assert inspection.member_count == 1


def test_inspection_rejects_nonsentinel_zip64_budget_before_zipfile_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_path = tmp_path / "nonsentinel-zip64.zip"
    make_nonsentinel_zip64_fixture(archive_path)
    assert central_directory_size(archive_path) == 57
    with archive_path.open("rb") as stream:
        stdlib_end_record = zipfile._EndRecData(stream)
    assert stdlib_end_record is not None
    assert stdlib_end_record[zipfile._ECD_ENTRIES_TOTAL] == 1
    assert stdlib_end_record[zipfile._ECD_SIZE] == 500_000

    def zipfile_must_not_load(*args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile must not load before nonsentinel ZIP64 preflight")

    monkeypatch.setattr(archive_module.zipfile, "ZipFile", zipfile_must_not_load)
    with pytest.raises(ValueError, match="central directory"):
        inspect_zip(
            archive_path,
            make_budget(max_central_directory_bytes=100),
        )


def test_inspection_rejects_zip64_sentinel_without_locator(tmp_path: Path) -> None:
    archive_path = tmp_path / "missing-zip64-locator.zip"
    write_zip(archive_path, [("payload", b"x")])
    payload = bytearray(archive_path.read_bytes())
    eocd_offset = payload.rfind(b"PK\x05\x06")
    payload[eocd_offset + 10 : eocd_offset + 12] = (0xFFFF).to_bytes(2, "little")
    archive_path.write_bytes(payload)

    with pytest.raises(ValueError, match="ZIP64"):
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
