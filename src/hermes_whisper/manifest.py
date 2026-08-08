from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .tokenizer import TimestampedText, normalize_text


@dataclass(frozen=True)
class Provenance:
    source: str
    license: str
    consent: bool
    speaker_id: str | None = None

    def validate(self) -> None:
        if not self.source.strip() or not self.license.strip():
            raise ValueError("provenance.source and provenance.license are required")
        if not self.consent:
            raise ValueError("record is not approved for model training")


@dataclass(frozen=True)
class ManifestRecord:
    audio: str
    text: str
    language: str
    duration_seconds: float
    provenance: Provenance
    record_id: str | None = None
    split: str = "train"
    segments: tuple[TimestampedText, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(
        self,
        *,
        allowed_languages: Iterable[str] = ("uk", "cs"),
        require_audio_exists: bool = False,
    ) -> None:
        if not self.audio.strip():
            raise ValueError("audio path cannot be empty")
        if require_audio_exists and not Path(self.audio).is_file():
            raise ValueError(f"audio file does not exist: {self.audio}")
        if not normalize_text(self.text):
            raise ValueError("transcript cannot be empty")
        if self.language not in set(allowed_languages):
            raise ValueError(f"unsupported language: {self.language}")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        if self.split not in {"train", "validation", "test"}:
            raise ValueError("split must be train, validation, or test")
        self.provenance.validate()
        previous_end = 0.0
        for segment in self.segments:
            if not 0 <= segment.start <= segment.end <= self.duration_seconds + 1e-6:
                raise ValueError("segment lies outside the audio duration")
            if segment.start < previous_end:
                raise ValueError("segments must be ordered and non-overlapping")
            if not normalize_text(segment.text):
                raise ValueError("segment transcript cannot be empty")
            previous_end = segment.end
        if self.segments:
            joined = normalize_text(" ".join(segment.text for segment in self.segments))
            if joined != normalize_text(self.text):
                raise ValueError("record text must equal the normalized joined segment text")

    def to_json_dict(self, *, base_directory: Path | None = None) -> dict[str, Any]:
        payload = asdict(self)
        if base_directory is not None:
            try:
                payload["audio"] = str(
                    Path(self.audio).resolve().relative_to(base_directory.resolve())
                )
            except ValueError:
                payload["audio"] = self.audio
        return payload


def _parse_record(payload: dict[str, Any], base_directory: Path) -> ManifestRecord:
    required = {"audio", "text", "language", "duration_seconds", "provenance"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"missing required fields: {sorted(missing)}")
    audio_path = Path(str(payload["audio"]))
    if not audio_path.is_absolute():
        audio_path = (base_directory / audio_path).resolve()
    provenance_data = payload["provenance"]
    if not isinstance(provenance_data, dict):
        raise ValueError("provenance must be an object")
    segments = tuple(
        TimestampedText(
            start=float(item["start"]),
            end=float(item["end"]),
            text=str(item["text"]),
        )
        for item in payload.get("segments", [])
    )
    return ManifestRecord(
        audio=str(audio_path),
        text=normalize_text(str(payload["text"])),
        language=str(payload["language"]),
        duration_seconds=float(payload["duration_seconds"]),
        provenance=Provenance(**provenance_data),
        record_id=payload.get("record_id"),
        split=str(payload.get("split", "train")),
        segments=segments,
        metadata=dict(payload.get("metadata", {})),
    )


def iter_manifest(
    path: str | Path,
    *,
    allowed_languages: Iterable[str] = ("uk", "cs"),
    require_audio_exists: bool = False,
) -> Iterator[ManifestRecord]:
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("record must be a JSON object")
                record = _parse_record(payload, source.parent)
                record.validate(
                    allowed_languages=allowed_languages,
                    require_audio_exists=require_audio_exists,
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{source}:{line_number}: {exc}") from exc
            yield record


def load_manifest(path: str | Path, **kwargs: Any) -> list[ManifestRecord]:
    records = list(iter_manifest(path, **kwargs))
    if not records:
        raise ValueError("manifest contains no valid records")
    return records


def write_manifest(records: Iterable[ManifestRecord], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(record.to_json_dict(base_directory=target.parent), ensure_ascii=False)
        for record in records
    ]
    target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def manifest_fingerprint(records: Iterable[ManifestRecord]) -> str:
    digest = hashlib.sha256()
    count = 0
    for record in records:
        payload = json.dumps(record.to_json_dict(), ensure_ascii=False, sort_keys=True)
        digest.update(payload.encode("utf-8"))
        digest.update(b"\n")
        count += 1
    if count == 0:
        raise ValueError("cannot fingerprint an empty manifest")
    return digest.hexdigest()


def summarize_manifest(records: Iterable[ManifestRecord]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "records": 0,
        "duration_hours": 0.0,
        "languages": {},
        "splits": {},
        "sources": {},
    }
    for record in records:
        summary["records"] += 1
        summary["duration_hours"] += record.duration_seconds / 3600
        summary["languages"][record.language] = summary["languages"].get(record.language, 0) + 1
        summary["splits"][record.split] = summary["splits"].get(record.split, 0) + 1
        source = record.provenance.source
        summary["sources"][source] = summary["sources"].get(source, 0) + 1
    summary["duration_hours"] = round(summary["duration_hours"], 6)
    return summary
