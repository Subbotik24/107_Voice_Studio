from __future__ import annotations

import json
from pathlib import Path

from .models import Transcript


def timestamp(seconds: float, separator: str = ",") -> str:
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    whole, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{whole:02}{separator}{millis:03}"


def export_transcript(transcript: Transcript, fmt: str, destination: Path) -> Path:
    fmt = fmt.lower()
    destination = destination.expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "txt":
        content = transcript.corrected_text + "\n"
    elif fmt == "md":
        rtf = "n/a" if transcript.real_time_factor is None else f"{transcript.real_time_factor:.3f}"
        content = (
            f"# {transcript.source_name}\n\n"
            f"- Language: `{transcript.language}`\n"
            f"- Engine: `{transcript.engine}`\n"
            f"- Model: `{transcript.model}`\n"
            f"- Audio: `{transcript.audio_seconds:.2f} s`\n"
            f"- RTF: `{rtf}`\n"
            f"- Source SHA-256: `{transcript.source_sha256}`\n\n"
            f"{transcript.corrected_text}\n"
        )
    elif fmt == "json":
        content = json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2) + "\n"
    elif fmt in {"srt", "vtt"}:
        lines = ["WEBVTT", ""] if fmt == "vtt" else []
        separator = "." if fmt == "vtt" else ","
        for index, segment in enumerate(transcript.segments, 1):
            if fmt == "srt":
                lines.append(str(index))
            lines.append(
                f"{timestamp(segment.start, separator)} --> "
                f"{timestamp(max(segment.start, segment.end), separator)}"
            )
            lines.extend([segment.display_text, ""])
        content = "\n".join(lines).rstrip() + "\n"
    else:
        raise ValueError(f"unsupported export format: {fmt}")
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(destination)
    return destination
