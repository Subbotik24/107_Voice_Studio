from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DictionaryRule:
    source: str
    target: str
    case_sensitive: bool = False
    whole_word: bool = True

    def validate(self) -> None:
        if not self.source:
            raise ValueError("dictionary rule source cannot be empty")


class TerminologyDictionary:
    def __init__(self, rules: list[DictionaryRule] | None = None):
        self.rules = rules or []
        for rule in self.rules:
            rule.validate()

    @classmethod
    def load(cls, path: str | Path | None) -> TerminologyDictionary:
        if not path:
            return cls()
        target = Path(path).expanduser()
        if not target.is_file():
            raise FileNotFoundError(f"dictionary does not exist: {target}")
        try:
            data: Any = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid dictionary JSON {target}: {exc}") from exc
        items = data.get("replacements", data) if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ValueError("dictionary must be a list or an object with 'replacements'")
        rules: list[DictionaryRule] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"dictionary rule {index} must be an object")
            try:
                rule = DictionaryRule(**item)
            except TypeError as exc:
                raise ValueError(f"invalid dictionary rule {index}: {exc}") from exc
            rule.validate()
            rules.append(rule)
        return cls(rules)

    @property
    def version(self) -> str:
        if not self.rules:
            return "none"
        payload = json.dumps(
            [asdict(rule) for rule in self.rules],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:12]

    def apply(self, text: str) -> str:
        result = text
        for rule in self.rules:
            pattern = re.escape(rule.source)
            if rule.whole_word:
                pattern = rf"(?<!\w){pattern}(?!\w)"
            flags = 0 if rule.case_sensitive else re.IGNORECASE
            result = re.sub(pattern, lambda _match, value=rule.target: value, result, flags=flags)
        return result
