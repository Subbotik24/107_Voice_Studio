from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MAX_DICTIONARY_BYTES = 16 * 1024**2
MAX_HINT_TERMS = 256
MAX_HINT_PAYLOAD_BYTES = 8192


def _read_bounded_bytes(path: Path) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(MAX_DICTIONARY_BYTES + 1)
    if len(data) > MAX_DICTIONARY_BYTES:
        raise ValueError(
            f"dictionary file exceeds maximum size ({MAX_DICTIONARY_BYTES} bytes): {path}"
        )
    return data


def _parse_json(data: bytes, path: Path) -> Any:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid dictionary JSON {path}: {exc}") from exc


def _dictionary_from_payload(data: Any, path: Path) -> TerminologyDictionary:
    items = data.get("replacements", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("dictionary must be a list or an object with 'replacements'")
    rules: list[DictionaryRule] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"invalid dictionary rule {index}: rule must be an object")
        try:
            rules.append(DictionaryRule(**item))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid dictionary rule {index}: {exc}") from exc
    return TerminologyDictionary(rules)


@dataclass(frozen=True)
class DictionaryRule:
    source: str
    target: str
    case_sensitive: bool = False
    whole_word: bool = True
    use_as_hint: bool = True

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.source, str):
            raise ValueError("dictionary rule source must be a string")
        if not isinstance(self.target, str):
            raise ValueError("dictionary rule target must be a string")
        if not self.source:
            raise ValueError("dictionary rule source cannot be empty")
        for name in ("case_sensitive", "whole_word", "use_as_hint"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"dictionary rule {name} must be a boolean")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
        data: Any = _parse_json(_read_bounded_bytes(target), target)
        return _dictionary_from_payload(data, target)

    @property
    def version(self) -> str:
        if not self.rules:
            return "none"
        payload = json.dumps(
            [rule.to_dict() for rule in self.rules],
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:12]

    def to_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {"replacements": [rule.to_dict() for rule in self.rules]}

    def hint_terms(self) -> list[str]:
        terms: list[str] = []
        seen: set[str] = set()
        for rule in self.rules:
            if not rule.use_as_hint:
                continue
            term = rule.target.strip()
            if not term or term.casefold() in seen:
                continue
            candidate = ", ".join([*terms, term])
            if len(candidate.encode("utf-8")) > MAX_HINT_PAYLOAD_BYTES:
                break
            terms.append(term)
            seen.add(term.casefold())
            if len(terms) >= MAX_HINT_TERMS:
                break
        return terms

    def apply(self, text: str) -> str:
        result = text
        for rule in self.rules:
            pattern = re.escape(rule.source)
            if rule.whole_word:
                pattern = rf"(?<!\w){pattern}(?!\w)"
            flags = 0 if rule.case_sensitive else re.IGNORECASE
            result = re.sub(pattern, lambda _match, value=rule.target: value, result, flags=flags)
        return result


@dataclass(frozen=True)
class DictionaryMergeConflict:
    existing: DictionaryRule
    incoming: DictionaryRule

    @property
    def existing_rule(self) -> DictionaryRule:
        return self.existing

    @property
    def incoming_rule(self) -> DictionaryRule:
        return self.incoming


@dataclass(frozen=True)
class DictionaryMergePreview:
    merged: TerminologyDictionary
    added_count: int
    exact_skipped_count: int
    hint_update_count: int
    conflicts: tuple[DictionaryMergeConflict, ...]

    @property
    def rules(self) -> list[DictionaryRule]:
        return self.merged.rules

    @property
    def merged_rules(self) -> list[DictionaryRule]:
        return self.merged.rules

    @property
    def added(self) -> int:
        return self.added_count

    @property
    def exact_skipped(self) -> int:
        return self.exact_skipped_count

    @property
    def hint_updates(self) -> int:
        return self.hint_update_count


def merge_preview(
    existing: TerminologyDictionary, incoming: TerminologyDictionary
) -> DictionaryMergePreview:
    rules = list(existing.rules)
    conflicts: list[DictionaryMergeConflict] = []
    added = skipped = updates = 0

    def key(rule: DictionaryRule) -> tuple[str, bool, bool]:
        return (
            rule.source if rule.case_sensitive else rule.source.casefold(),
            rule.case_sensitive,
            rule.whole_word,
        )

    for candidate in incoming.rules:
        if any(candidate == rule for rule in rules):
            skipped += 1
            continue
        match = next((rule for rule in rules if key(rule) == key(candidate)), None)
        if match is None:
            rules.append(candidate)
            added += 1
        elif match.target != candidate.target:
            conflicts.append(DictionaryMergeConflict(match, candidate))
        elif match.use_as_hint != candidate.use_as_hint:
            index = rules.index(match)
            rules[index] = DictionaryRule(
                match.source,
                match.target,
                match.case_sensitive,
                match.whole_word,
                candidate.use_as_hint,
            )
            updates += 1
        else:
            # Same effective key/target but a different source spelling is a
            # valid non-exact incoming rule and remains visible in the merge.
            rules.append(candidate)
            added += 1
    return DictionaryMergePreview(
        TerminologyDictionary(rules), added, skipped, updates, tuple(conflicts)
    )


preview_merge = merge_preview
