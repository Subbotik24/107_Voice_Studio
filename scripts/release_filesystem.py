"""Filesystem boundaries shared by release assembly scripts."""

from __future__ import annotations

import argparse
import ctypes
import errno
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_READ_SIZE = 1024 * 1024


def file_fingerprint(info: os.stat_result) -> tuple[int, ...]:
    """Return identity plus observable content state for a regular file."""
    fingerprint = (
        getattr(info, "st_dev", 0),
        getattr(info, "st_ino", 0),
        stat.S_IFMT(info.st_mode),
        info.st_size,
        getattr(info, "st_mtime_ns", int(info.st_mtime * 1_000_000_000)),
        getattr(info, "st_file_attributes", 0),
    )
    if sys.platform == "win32":
        # CPython's Windows handle stat rounds creation/change time differently
        # from pathname stat. Exact bytes are compared separately.
        return fingerprint
    return (*fingerprint, getattr(info, "st_ctime_ns", int(info.st_ctime * 1_000_000_000)))


def _file_identity(info: os.stat_result) -> tuple[int, int]:
    return (getattr(info, "st_dev", 0), getattr(info, "st_ino", 0))


def _is_reparse_point(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _read_fd(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        block = os.read(descriptor, _READ_SIZE)
        if not block:
            return b"".join(chunks)
        chunks.append(block)


@dataclass
class _PosixChain:
    directories: list[int]
    directory_identities: list[tuple[int, int]]
    file_descriptor: int | None
    before: os.stat_result

    def close(self) -> None:
        if self.file_descriptor is not None:
            os.close(self.file_descriptor)
            self.file_descriptor = None
        while self.directories:
            os.close(self.directories.pop())


def _posix_chain_components(root: Path, relative: Path) -> list[str]:
    if root.anchor != os.sep:
        raise ValueError("release path has an unsupported filesystem anchor")
    return [*root.parts[1:], *relative.parts[:-1]]


def _open_posix_chain(root: Path, relative: Path) -> _PosixChain:
    required_flags = ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required_flags) or os.open not in os.supports_dir_fd:
        raise ValueError("release filesystem cannot enforce a safe path boundary")
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    directories: list[int] = []
    identities: list[tuple[int, int]] = []
    file_descriptor: int | None = None
    try:
        anchor = os.open(os.sep, directory_flags)
        directories.append(anchor)
        anchor_info = os.fstat(anchor)
        if _is_reparse_point(anchor_info) or not stat.S_ISDIR(anchor_info.st_mode):
            raise ValueError("release path anchor is not a safe directory")
        identities.append(_file_identity(anchor_info))
        for component in _posix_chain_components(root, relative):
            descriptor = os.open(component, directory_flags, dir_fd=directories[-1])
            directories.append(descriptor)
            info = os.fstat(descriptor)
            if _is_reparse_point(info) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("release path contains an unsafe directory")
            identities.append(_file_identity(info))
        file_descriptor = os.open(relative.name, file_flags, dir_fd=directories[-1])
        before = os.fstat(file_descriptor)
        if _is_reparse_point(before) or not stat.S_ISREG(before.st_mode):
            raise ValueError("release file is not a safe regular file")
        return _PosixChain(directories, identities, file_descriptor, before)
    except Exception:
        if file_descriptor is not None:
            os.close(file_descriptor)
        while directories:
            os.close(directories.pop())
        raise


if sys.platform == "win32":
    from ctypes import wintypes

    class _UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class _ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class _IoStatusBlockUnion(ctypes.Union):
        _fields_ = [("Status", wintypes.LONG), ("Pointer", wintypes.LPVOID)]

    class _IoStatusBlock(ctypes.Structure):
        _fields_ = [("value", _IoStatusBlockUnion), ("Information", ctypes.c_size_t)]

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation_time", _FileTime),
            ("last_access_time", _FileTime),
            ("last_write_time", _FileTime),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("number_of_links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _NTDLL = ctypes.WinDLL("ntdll", use_last_error=True)
    _CREATE_FILE = _KERNEL32.CreateFileW
    _CREATE_FILE.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _CREATE_FILE.restype = wintypes.HANDLE
    _CLOSE_HANDLE = _KERNEL32.CloseHandle
    _CLOSE_HANDLE.argtypes = [wintypes.HANDLE]
    _CLOSE_HANDLE.restype = wintypes.BOOL
    _GET_HANDLE_INFO = _KERNEL32.GetFileInformationByHandle
    _GET_HANDLE_INFO.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ByHandleFileInformation)]
    _GET_HANDLE_INFO.restype = wintypes.BOOL
    _MOVE_FILE_EX = _KERNEL32.MoveFileExW
    _MOVE_FILE_EX.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    _MOVE_FILE_EX.restype = wintypes.BOOL
    _NT_OPEN_FILE = _NTDLL.NtOpenFile
    _NT_OPEN_FILE.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatusBlock),
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _NT_OPEN_FILE.restype = wintypes.LONG
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _windows_handle_identity(handle: Any) -> tuple[int, int]:
    information = _ByHandleFileInformation()
    if not _GET_HANDLE_INFO(handle, ctypes.byref(information)):
        raise ValueError("release filesystem handle could not be inspected safely")
    file_index = (information.file_index_high << 32) | information.file_index_low
    return (information.volume_serial, file_index)


def _windows_handle_attributes(handle: Any) -> int:
    information = _ByHandleFileInformation()
    if not _GET_HANDLE_INFO(handle, ctypes.byref(information)):
        raise ValueError("release filesystem handle could not be inspected safely")
    return information.attributes


def _close_windows_handle(handle: Any) -> None:
    if not _CLOSE_HANDLE(handle):
        raise ValueError("release filesystem handle could not be closed safely")


def _open_windows_anchor(anchor: str) -> Any:
    desired_access = 0x00100000 | 0x00000080 | 0x00000020
    share_all = 0x1 | 0x2 | 0x4
    open_existing = 3
    flags = 0x02000000 | 0x00200000
    handle = _CREATE_FILE(anchor, desired_access, share_all, None, open_existing, flags, None)
    if handle == _INVALID_HANDLE_VALUE:
        raise ValueError("release path anchor could not be opened safely")
    return handle


def _nt_open_relative(parent: Any, name: str, *, directory: bool) -> Any:
    if not name or name in {".", ".."} or "\\" in name or "/" in name:
        raise ValueError("release path contains an invalid component")
    buffer = ctypes.create_unicode_buffer(name)
    encoded_length = len(name.encode("utf-16-le"))
    object_name = _UnicodeString(
        encoded_length,
        encoded_length + 2,
        ctypes.cast(buffer, wintypes.LPWSTR),
    )
    attributes = _ObjectAttributes(
        ctypes.sizeof(_ObjectAttributes),
        parent,
        ctypes.pointer(object_name),
        0x40 | 0x1000,
        None,
        None,
    )
    status_block = _IoStatusBlock()
    handle = wintypes.HANDLE()
    desired_access = 0x00100000 | 0x00000080
    desired_access |= 0x00000001 if directory else 0x00000001
    options = 0x00200000 | 0x00000020
    options |= 0x00000001 if directory else 0x00000040
    status = _NT_OPEN_FILE(
        ctypes.byref(handle),
        desired_access,
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        0x1 | 0x2 | 0x4,
        options,
    )
    if status < 0:
        raise ValueError("release path component could not be opened safely")
    return handle


@dataclass
class _WindowsChain:
    directories: list[Any]
    directory_identities: list[tuple[int, int]]
    file_descriptor: int | None
    before: os.stat_result

    def close(self) -> None:
        if self.file_descriptor is not None:
            os.close(self.file_descriptor)
            self.file_descriptor = None
        while self.directories:
            _close_windows_handle(self.directories.pop())


def _open_windows_chain(root: Path, relative: Path) -> _WindowsChain:
    import msvcrt

    if not root.anchor:
        raise ValueError("release path has an unsupported filesystem anchor")
    directories: list[Any] = []
    identities: list[tuple[int, int]] = []
    file_descriptor: int | None = None
    file_handle: Any | None = None
    try:
        anchor = _open_windows_anchor(root.anchor)
        directories.append(anchor)
        attributes = _windows_handle_attributes(anchor)
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT or not (
            attributes & _FILE_ATTRIBUTE_DIRECTORY
        ):
            raise ValueError("release path anchor is not a safe directory")
        identities.append(_windows_handle_identity(anchor))
        components = [*root.parts[1:], *relative.parts[:-1]]
        for component in components:
            descriptor = _nt_open_relative(directories[-1], component, directory=True)
            directories.append(descriptor)
            attributes = _windows_handle_attributes(descriptor)
            if attributes & _FILE_ATTRIBUTE_REPARSE_POINT or not (
                attributes & _FILE_ATTRIBUTE_DIRECTORY
            ):
                raise ValueError("release path contains an unsafe directory")
            identities.append(_windows_handle_identity(descriptor))
        file_handle = _nt_open_relative(directories[-1], relative.name, directory=False)
        attributes = _windows_handle_attributes(file_handle)
        if attributes & (_FILE_ATTRIBUTE_REPARSE_POINT | _FILE_ATTRIBUTE_DIRECTORY):
            raise ValueError("release file is not a safe regular file")
        raw_file_handle = file_handle.value
        if raw_file_handle is None:
            raise ValueError("release file handle is unavailable")
        file_descriptor = msvcrt.open_osfhandle(
            raw_file_handle, os.O_RDONLY | getattr(os, "O_BINARY", 0)
        )
        file_handle = None
        before = os.fstat(file_descriptor)
        return _WindowsChain(directories, identities, file_descriptor, before)
    except Exception:
        if file_descriptor is not None:
            os.close(file_descriptor)
        elif file_handle is not None:
            _close_windows_handle(file_handle)
        while directories:
            _close_windows_handle(directories.pop())
        raise


def _read_and_close_chain(chain: _PosixChain | _WindowsChain) -> tuple[bytes, tuple[int, ...]]:
    descriptor = chain.file_descriptor
    if descriptor is None:
        raise ValueError("release file descriptor is unavailable")
    try:
        content = _read_fd(descriptor)
        after = os.fstat(descriptor)
        if file_fingerprint(after) != file_fingerprint(chain.before):
            raise ValueError("release file changed during read")
        return content, file_fingerprint(after)
    finally:
        chain.close()


def _open_chain(root: Path, relative: Path) -> _PosixChain | _WindowsChain:
    try:
        if sys.platform == "win32":
            return _open_windows_chain(root, relative)
        if os.name == "posix":
            return _open_posix_chain(root, relative)
    except OSError as exc:
        raise ValueError("release file could not be opened safely") from exc
    raise ValueError("release filesystem platform is unsupported")


def read_file_within_root(
    release_directory: Path, source: Path
) -> tuple[bytes, str, tuple[int, ...]]:
    """Read a regular file through a pinned, no-reparse directory chain."""
    root = Path(release_directory).absolute()
    raw_source = Path(source)
    if ".." in raw_source.parts:
        raise ValueError("release file path contains lexical traversal")
    target = raw_source.absolute()
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ValueError("release file must be inside release directory") from exc
    if not relative.parts:
        raise ValueError("release file must be a regular file")

    initial = _open_chain(root, relative)
    initial_directory_identities = list(initial.directory_identities)
    content, fingerprint = _read_and_close_chain(initial)

    verification = _open_chain(root, relative)
    if verification.directory_identities != initial_directory_identities:
        verification.close()
        raise ValueError("release path changed during read")
    verified_content, verified_fingerprint = _read_and_close_chain(verification)
    if verified_fingerprint != fingerprint or verified_content != content:
        raise ValueError("release file changed during read")
    return content, relative.as_posix(), verified_fingerprint


def _promote_windows(source: Path, destination: Path) -> None:
    if not _MOVE_FILE_EX(str(source), str(destination), 0):
        error = ctypes.get_last_error()
        if error in {80, 183}:
            raise FileExistsError("release destination already exists")
        raise ValueError("release promotion failed safely")


def _open_posix_parent(parent: Path) -> int:
    required_flags = ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required_flags) or os.open not in os.supports_dir_fd:
        raise ValueError("release filesystem cannot enforce a safe path boundary")
    if parent.anchor != os.sep:
        raise ValueError("release path has an unsupported filesystem anchor")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    directories: list[int] = []
    try:
        descriptor = os.open(os.sep, flags)
        directories.append(descriptor)
        for component in parent.parts[1:]:
            descriptor = os.open(component, flags, dir_fd=directories[-1])
            directories.append(descriptor)
            info = os.fstat(descriptor)
            if _is_reparse_point(info) or not stat.S_ISDIR(info.st_mode):
                raise ValueError("release path contains an unsafe directory")
        result = directories.pop()
        while directories:
            os.close(directories.pop())
        return result
    except Exception:
        while directories:
            os.close(directories.pop())
        raise


def _promote_macos(source: Path, destination: Path) -> None:
    parent_descriptor = _open_posix_parent(source.parent)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        try:
            rename = libc.renameatx_np
        except AttributeError as exc:
            raise ValueError(
                "release filesystem cannot enforce atomic no-replace promotion"
            ) from exc
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(
            parent_descriptor,
            os.fsencode(source.name),
            parent_descriptor,
            os.fsencode(destination.name),
            0x00000004,
        )
        if result != 0:
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FileExistsError("release destination already exists")
            raise ValueError("release promotion failed safely")
    finally:
        os.close(parent_descriptor)


def _promote_linux(source: Path, destination: Path) -> None:
    parent_descriptor = _open_posix_parent(source.parent)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise ValueError("release filesystem cannot enforce atomic no-replace promotion")
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(
            parent_descriptor,
            os.fsencode(source.name),
            parent_descriptor,
            os.fsencode(destination.name),
            0x1,
        )
        if result != 0:
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FileExistsError("release destination already exists")
            raise ValueError("release promotion failed safely")
    finally:
        os.close(parent_descriptor)


def _safe_lstat(path: Path, *, label: str) -> os.stat_result:
    try:
        return os.lstat(path)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"release {label} does not exist") from exc
    except OSError as exc:
        raise ValueError(f"release {label} could not be inspected safely") from exc


def promote_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically rename one staged directory to an absent sibling path."""
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    if source.parent != destination.parent or source.name == destination.name:
        raise ValueError("release promotion requires distinct sibling directories")
    source_info = _safe_lstat(source, label="source")
    if _is_reparse_point(source_info) or not stat.S_ISDIR(source_info.st_mode):
        raise ValueError("release source is not a safe directory")
    try:
        os.lstat(destination)
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ValueError("release destination could not be inspected safely") from exc
    else:
        raise FileExistsError("release destination already exists")

    if sys.platform == "win32":
        _promote_windows(source, destination)
    elif sys.platform == "darwin":
        _promote_macos(source, destination)
    elif sys.platform.startswith("linux"):
        _promote_linux(source, destination)
    else:
        raise ValueError("release filesystem platform is unsupported")

    try:
        os.lstat(source)
    except FileNotFoundError:
        pass
    else:
        raise ValueError("release source remains after promotion")
    promoted = _safe_lstat(destination, label="destination")
    if (
        _is_reparse_point(promoted)
        or not stat.S_ISDIR(promoted.st_mode)
        or _file_identity(promoted) != _file_identity(source_info)
    ):
        raise ValueError("release destination does not match the staged directory")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    promote = subparsers.add_parser("promote")
    promote.add_argument("--source", type=Path, required=True)
    promote.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        promote_directory_no_replace(args.source, args.destination)
    except (FileExistsError, FileNotFoundError, OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("release promotion complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
