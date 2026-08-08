import json

from hermes_voice_studio.exporters import export_transcript, timestamp
from hermes_voice_studio.models import Segment, Transcript


def sample() -> Transcript:
    return Transcript(
        id="demo",
        created_at="2026-07-26T00:00:00+00:00",
        source_name="demo.wav",
        source_sha256="a" * 64,
        language="uk",
        engine="faster-whisper",
        model="small",
        raw_text="Привіт світ.",
        corrected_text="Привіт, світе.",
        segments=[
            Segment(
                0,
                1.25,
                "Привіт світ.",
                corrected_text="Привіт, світе.",
            )
        ],
    )


def test_timestamp():
    assert timestamp(3661.234) == "01:01:01,234"


def test_all_exports(tmp_path):
    for fmt in ("txt", "md", "json", "srt", "vtt"):
        target = export_transcript(sample(), fmt, tmp_path / f"demo.{fmt}")
        assert target.exists()
        assert target.read_text(encoding="utf-8").strip()
    assert json.loads((tmp_path / "demo.json").read_text(encoding="utf-8"))["id"] == "demo"
    subtitle = (tmp_path / "demo.srt").read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:01,250" in subtitle
    assert "Привіт, світе." in subtitle
