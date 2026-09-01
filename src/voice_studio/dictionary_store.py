"""Bounded, atomic persistence and interchange for managed dictionaries."""

from __future__ import annotations

import csv
import io
import json
import os
import stat
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from .dictionary import (
    DictionaryRule,
    TerminologyDictionary,
    _dictionary_from_payload,
    _parse_json,
    _read_bounded_bytes,
)

CSV_HEADER = "source,target,case_sensitive,whole_word,use_as_hint"
_CSV_FIELD_LIMIT_LOCK = threading.RLock()


def _reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attrs = getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0)
        return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except FileNotFoundError:
        return False


def _parse(data: bytes, path: Path) -> TerminologyDictionary:
    return _dictionary_from_payload(_parse_json(data, path), path)


@contextmanager
def _csv_field_limit(limit: int):
    with _CSV_FIELD_LIMIT_LOCK:
        previous = csv.field_size_limit()
        csv.field_size_limit(max(previous, limit))
        try:
            yield
        finally:
            csv.field_size_limit(previous)


def _atomic_json(dictionary: TerminologyDictionary, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _reparse(destination):
        raise ValueError(f"refusing symlink/reparse dictionary destination: {destination}")
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
            json.dump(dictionary.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
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


class DictionaryRepository:
    def __init__(self, config_directory: str | Path):
        self.config_directory = Path(config_directory).expanduser()
        self.managed_path = self.config_directory / "dictionary.json"

    def is_managed(self, path: str | Path) -> bool:
        return Path(path).expanduser().absolute() == self.managed_path.absolute()

    def load(self, path: str | Path | None = None) -> TerminologyDictionary:
        if not path:
            return TerminologyDictionary()
        target = Path(path).expanduser()
        if self.is_managed(target) and _reparse(target):
            raise ValueError(f"refusing symlink/reparse managed dictionary: {target}")
        if not target.is_file():
            raise FileNotFoundError(f"dictionary does not exist: {target}")
        return _parse(_read_bounded_bytes(target), target)

    def save_managed(self, dictionary: TerminologyDictionary) -> None:
        if _reparse(self.managed_path):
            raise ValueError(f"refusing symlink/reparse managed dictionary: {self.managed_path}")
        _atomic_json(dictionary, self.managed_path)

    def load_csv(self, path: str | Path) -> TerminologyDictionary:
        target = Path(path).expanduser()
        raw = _read_bounded_bytes(target)
        try:
            text = raw.decode("utf-8")
            with _csv_field_limit(len(raw) + 1):
                reader = csv.DictReader(io.StringIO(text))
                if reader.fieldnames != CSV_HEADER.split(","):
                    raise ValueError(f"dictionary CSV header must be exactly {CSV_HEADER}")
                rules = []
                for row in reader:
                    if None in row:
                        raise ValueError("dictionary CSV contains extra cells")
                    values = {key: row.get(key) for key in CSV_HEADER.split(",")}
                    booleans = {}
                    for key in ("case_sensitive", "whole_word", "use_as_hint"):
                        if values[key] not in ("true", "false"):
                            raise ValueError(f"dictionary CSV {key} must be true or false")
                        booleans[key] = values[key] == "true"
                    rules.append(DictionaryRule(values["source"], values["target"], **booleans))
                return TerminologyDictionary(rules)
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ValueError(f"invalid dictionary CSV {target}: {exc}") from exc

    def export_csv(self, dictionary: TerminologyDictionary, destination: str | Path) -> None:
        target = Path(destination).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        if _reparse(target):
            raise ValueError(f"refusing symlink/reparse dictionary destination: {target}")
        content = io.StringIO(newline="")
        writer = csv.DictWriter(content, fieldnames=CSV_HEADER.split(","), lineterminator="\n")
        writer.writeheader()
        for rule in dictionary.rules:
            row = rule.to_dict()
            for key in ("case_sensitive", "whole_word", "use_as_hint"):
                row[key] = str(row[key]).lower()
            writer.writerow(row)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_name = handle.name
                handle.write(content.getvalue())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
            temp_name = None
        finally:
            if temp_name:
                try:
                    Path(temp_name).unlink()
                except FileNotFoundError:
                    pass

    def export_json(self, dictionary: TerminologyDictionary, destination: str | Path) -> None:
        _atomic_json(dictionary, Path(destination).expanduser())
