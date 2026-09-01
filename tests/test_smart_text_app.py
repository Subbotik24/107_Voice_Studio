from __future__ import annotations

import copy
import math

import pytest

from voice_studio.models import Segment, Transcript
from voice_studio.smart_text import (
    SPEAKER_LABELS_KEY,
    Paragraph,
    SmartTextOptions,
    build_document_paragraphs,
    build_paragraphs,
    format_timestamp,
    render_markdown,
    render_plain,
    speaker_labels_from_metadata,
    speaker_labels_to_metadata,
)


def segment(start: float, end: float, text: str, corrected: str | None = None) -> Segment:
    return Segment(start=start, end=end, text=text, corrected_text=corrected)


def transcript(segments: list[Segment], *, corrected: str = "", metadata=None) -> Transcript:
    return Transcript(
        id="t1",
        created_at="2026-09-01T00:00:00+00:00",
        source_name="запис.wav",
        source_sha256="0" * 64,
        language="uk",
        engine="ollama",
        model="gemma4:12b",
        raw_text="сирий текст",
        corrected_text=corrected,
        segments=segments,
        metadata={} if metadata is None else metadata,
    )


FIXTURE_SEGMENTS = [
    segment(0.0, 4.0, "Привіт, це перший запис."),
    segment(4.2, 9.0, "Продовжую ту саму думку."),
    segment(65.0, 70.0, "Тепер говорить інший."),
    segment(3725.0, 3730.0, "Останній абзац."),
]
FIXTURE_LABELS = {0: "Анна", 2: "Богдан"}


def fixture_transcript() -> Transcript:
    return transcript(
        [copy.deepcopy(item) for item in FIXTURE_SEGMENTS],
        corrected=" ".join(item.text for item in FIXTURE_SEGMENTS),
        metadata={SPEAKER_LABELS_KEY: dict(speaker_labels_to_metadata(FIXTURE_LABELS))},
    )


# --- format_timestamp -------------------------------------------------------


def test_format_timestamp_minutes_and_seconds():
    assert format_timestamp(0.0) == "0:00"
    assert format_timestamp(5.0) == "0:05"
    assert format_timestamp(65) == "1:05"
    assert format_timestamp(65.9) == "1:05"
    assert format_timestamp(599.0) == "9:59"


def test_format_timestamp_uses_hours_from_one_hour():
    assert format_timestamp(3600.0) == "1:00:00"
    assert format_timestamp(3725) == "1:02:05"
    assert format_timestamp(36_000.0) == "10:00:00"


def test_format_timestamp_rejects_unusable_values():
    assert format_timestamp(-1.0) == "0:00"
    assert format_timestamp(float("nan")) == "0:00"
    assert format_timestamp(float("inf")) == "0:00"
    assert format_timestamp(True) == "0:00"
    assert format_timestamp("65") == "0:00"


# --- options ----------------------------------------------------------------


def test_options_defaults():
    options = SmartTextOptions()
    assert options.paragraph_gap_seconds == 2.0
    assert options.timestamps is True
    assert options.speakers is True
    assert options.max_paragraph_seconds == 90.0


def test_options_reject_boolean_numbers():
    with pytest.raises(ValueError):
        SmartTextOptions(paragraph_gap_seconds=True)
    with pytest.raises(ValueError):
        SmartTextOptions(max_paragraph_seconds=False)


def test_options_reject_out_of_range_gap():
    with pytest.raises(ValueError):
        SmartTextOptions(paragraph_gap_seconds=-0.1)
    with pytest.raises(ValueError):
        SmartTextOptions(paragraph_gap_seconds=600.1)
    with pytest.raises(ValueError):
        SmartTextOptions(paragraph_gap_seconds=math.nan)
    assert SmartTextOptions(paragraph_gap_seconds=0.0).paragraph_gap_seconds == 0.0
    assert SmartTextOptions(paragraph_gap_seconds=600.0).paragraph_gap_seconds == 600.0


def test_options_reject_out_of_range_max_paragraph():
    with pytest.raises(ValueError):
        SmartTextOptions(max_paragraph_seconds=4.9)
    with pytest.raises(ValueError):
        SmartTextOptions(max_paragraph_seconds=3600.1)
    assert SmartTextOptions(max_paragraph_seconds=5).max_paragraph_seconds == 5.0
    assert SmartTextOptions(max_paragraph_seconds=3600).max_paragraph_seconds == 3600.0


def test_options_reject_non_boolean_flags():
    with pytest.raises(ValueError):
        SmartTextOptions(timestamps=1)
    with pytest.raises(ValueError):
        SmartTextOptions(speakers="yes")


# --- paragraph building -----------------------------------------------------


def test_gap_at_threshold_starts_a_new_paragraph():
    segments = [segment(0.0, 10.0, "перше"), segment(12.0, 14.0, "друге")]
    paragraphs = build_paragraphs(segments, SmartTextOptions(paragraph_gap_seconds=2.0))
    assert [p.text for p in paragraphs] == ["перше", "друге"]
    assert paragraphs[0] == Paragraph(0.0, 10.0, None, "перше", (0,))
    assert paragraphs[1].segment_indexes == (1,)


def test_gap_below_threshold_joins_paragraph():
    segments = [segment(0.0, 10.0, "перше"), segment(11.9, 14.0, "друге")]
    paragraphs = build_paragraphs(segments, SmartTextOptions(paragraph_gap_seconds=2.0))
    assert len(paragraphs) == 1
    assert paragraphs[0].text == "перше друге"
    assert (paragraphs[0].start, paragraphs[0].end) == (0.0, 14.0)
    assert paragraphs[0].segment_indexes == (0, 1)


def test_speaker_change_splits_paragraph():
    segments = [
        segment(0.0, 1.0, "а"),
        segment(1.1, 2.0, "б"),
        segment(2.1, 3.0, "в"),
    ]
    paragraphs = build_paragraphs(
        segments, SmartTextOptions(), {0: "Анна", 1: "Анна", 2: "Богдан"}
    )
    assert [(p.speaker, p.text) for p in paragraphs] == [
        ("Анна", "а б"),
        ("Богдан", "в"),
    ]


def test_unlabelled_segment_inherits_the_current_speaker():
    segments = [segment(0.0, 1.0, "а"), segment(1.1, 2.0, "б")]
    paragraphs = build_paragraphs(segments, SmartTextOptions(), {0: "Анна"})
    assert len(paragraphs) == 1
    assert paragraphs[0].speaker == "Анна"
    assert paragraphs[0].text == "а б"


def test_speaker_is_inherited_across_a_pause_break():
    segments = [segment(0.0, 1.0, "а"), segment(30.0, 31.0, "б")]
    paragraphs = build_paragraphs(segments, SmartTextOptions(), {0: "Анна"})
    assert [(p.speaker, p.text) for p in paragraphs] == [("Анна", "а"), ("Анна", "б")]


def test_first_label_after_an_unlabelled_paragraph_splits():
    segments = [segment(0.0, 1.0, "а"), segment(1.1, 2.0, "б")]
    paragraphs = build_paragraphs(segments, SmartTextOptions(), {1: "Богдан"})
    assert [(p.speaker, p.text) for p in paragraphs] == [(None, "а"), ("Богдан", "б")]


def test_max_paragraph_seconds_closes_at_the_next_boundary():
    segments = [
        segment(0.0, 3.0, "один"),
        segment(3.5, 7.0, "два"),
        segment(7.5, 11.0, "три"),
        segment(11.5, 14.0, "чотири"),
    ]
    options = SmartTextOptions(paragraph_gap_seconds=2.0, max_paragraph_seconds=5.0)
    paragraphs = build_paragraphs(segments, options)
    assert [p.segment_indexes for p in paragraphs] == [(0, 1), (2, 3)]
    assert [(p.start, p.end) for p in paragraphs] == [(0.0, 7.0), (7.5, 14.0)]


def test_segments_without_editable_text_are_skipped():
    segments = [
        segment(0.0, 1.0, "а"),
        segment(1.1, 2.0, "   "),
        segment(2.1, 2.8, "видалено", corrected=""),
        segment(2.9, 4.0, "б"),
    ]
    paragraphs = build_paragraphs(segments, SmartTextOptions())
    assert len(paragraphs) == 1
    assert paragraphs[0].text == "а б"
    assert paragraphs[0].segment_indexes == (0, 3)
    assert (paragraphs[0].start, paragraphs[0].end) == (0.0, 4.0)


def test_skipped_segment_does_not_hide_a_paragraph_gap():
    segments = [
        segment(0.0, 1.0, "а"),
        segment(1.1, 2.0, "", corrected=""),
        segment(10.0, 11.0, "б"),
    ]
    paragraphs = build_paragraphs(segments, SmartTextOptions(paragraph_gap_seconds=2.0))
    assert [p.segment_indexes for p in paragraphs] == [(0,), (2,)]


def test_corrected_text_wins_over_raw_text():
    segments = [
        segment(0.0, 1.0, "сире", corrected="виправлене"),
        segment(1.1, 2.0, "друге сире", corrected=None),
    ]
    paragraphs = build_paragraphs(segments, SmartTextOptions())
    assert paragraphs[0].text == "виправлене друге сире"


def test_empty_segment_list_builds_no_paragraphs():
    assert build_paragraphs([], SmartTextOptions()) == ()


# --- speaker labels in metadata --------------------------------------------


def test_speaker_labels_from_metadata_is_tolerant():
    metadata = {
        SPEAKER_LABELS_KEY: {
            "0": "Анна",
            "1": "   ",
            "x": "Ігор",
            "2": 5,
            "3": " Богдан ",
            "-1": "Хтось",
            "4": "Н" * 80,
            "5": "Дві\nстрічки",
        }
    }
    assert speaker_labels_from_metadata(metadata) == {
        0: "Анна",
        3: "Богдан",
        4: "Н" * 64,
        5: "Дві стрічки",
    }


def test_speaker_labels_from_metadata_handles_missing_or_wrong_shapes():
    assert speaker_labels_from_metadata({}) == {}
    assert speaker_labels_from_metadata({SPEAKER_LABELS_KEY: []}) == {}
    assert speaker_labels_from_metadata({SPEAKER_LABELS_KEY: "Анна"}) == {}
    assert speaker_labels_from_metadata(None) == {}
    assert speaker_labels_from_metadata({SPEAKER_LABELS_KEY: {True: "Анна"}}) == {}


def test_speaker_labels_to_metadata_normalises_and_sorts():
    stored = speaker_labels_to_metadata({2: "Богдан", 0: " Анна ", 1: "  ", 3: "Н" * 80})
    assert list(stored) == ["0", "2", "3"]
    assert stored == {"0": "Анна", "2": "Богдан", "3": "Н" * 64}


def test_speaker_labels_round_trip():
    labels = {0: "Анна", 5: "Богдан"}
    assert speaker_labels_from_metadata(
        {SPEAKER_LABELS_KEY: speaker_labels_to_metadata(labels)}
    ) == labels


# --- document paragraphs ----------------------------------------------------


def test_build_document_paragraphs_uses_metadata_labels():
    paragraphs = build_document_paragraphs(fixture_transcript(), SmartTextOptions())
    assert [(p.speaker, p.start, p.segment_indexes) for p in paragraphs] == [
        ("Анна", 0.0, (0, 1)),
        ("Богдан", 65.0, (2,)),
        ("Богдан", 3725.0, (3,)),
    ]


def test_build_document_paragraphs_falls_back_to_blank_lines():
    document = "Перший абзац\nз двох рядків.\n\n\n  Другий абзац.  \n\n"
    paragraphs = build_document_paragraphs(
        transcript([], corrected=document), SmartTextOptions()
    )
    assert paragraphs == (
        Paragraph(0.0, 0.0, None, "Перший абзац з двох рядків.", ()),
        Paragraph(0.0, 0.0, None, "Другий абзац.", ()),
    )


def test_build_document_paragraphs_without_segments_or_text():
    assert build_document_paragraphs(transcript([], corrected="  \n\n "), SmartTextOptions()) == ()


# --- rendering --------------------------------------------------------------


def test_render_markdown_exact_output():
    rendered = render_markdown(fixture_transcript(), SmartTextOptions())
    assert rendered == (
        "# запис.wav\n"
        "\n"
        "[0:00] **Анна:** Привіт, це перший запис. Продовжую ту саму думку.\n"
        "\n"
        "[1:05] **Богдан:** Тепер говорить інший.\n"
        "\n"
        "[1:02:05] **Богдан:** Останній абзац.\n"
    )


def test_render_markdown_without_timestamps_and_speakers():
    options = SmartTextOptions(timestamps=False, speakers=False)
    rendered = render_markdown(fixture_transcript(), options, title="Нарада")
    assert rendered == (
        "# Нарада\n"
        "\n"
        "Привіт, це перший запис. Продовжую ту саму думку.\n"
        "\n"
        "Тепер говорить інший.\n"
        "\n"
        "Останній абзац.\n"
    )


def test_render_markdown_escapes_the_speaker_name():
    segments = [segment(0.0, 1.0, "текст")]
    document = transcript(
        segments,
        corrected="текст",
        metadata={SPEAKER_LABELS_KEY: {"0": "*Boss*_#"}},
    )
    rendered = render_markdown(document, SmartTextOptions())
    assert rendered == "# запис.wav\n\n[0:00] **\\*Boss\\*\\_#:** текст\n"
    leading = transcript(
        segments, corrected="текст", metadata={SPEAKER_LABELS_KEY: {"0": "#Boss"}}
    )
    assert render_markdown(leading, SmartTextOptions()).endswith("**\\#Boss:** текст\n")


def test_render_plain_exact_output():
    rendered = render_plain(fixture_transcript(), SmartTextOptions())
    assert rendered == (
        "[0:00] Анна: Привіт, це перший запис. Продовжую ту саму думку.\n"
        "\n"
        "[1:05] Богдан: Тепер говорить інший.\n"
        "\n"
        "[1:02:05] Богдан: Останній абзац.\n"
    )
    assert "**" not in rendered


def test_render_plain_keeps_the_speaker_name_literal():
    document = transcript(
        [segment(0.0, 1.0, "текст")],
        corrected="текст",
        metadata={SPEAKER_LABELS_KEY: {"0": "*Boss*"}},
    )
    assert render_plain(document, SmartTextOptions()) == "[0:00] *Boss*: текст\n"


def test_rendering_without_segments_omits_timestamps():
    document = transcript([], corrected="Перший абзац.\n\nДругий абзац.")
    assert render_markdown(document, SmartTextOptions()) == (
        "# запис.wav\n\nПерший абзац.\n\nДругий абзац.\n"
    )
    assert render_plain(document, SmartTextOptions()) == "Перший абзац.\n\nДругий абзац.\n"


def test_rendering_an_empty_document():
    document = transcript([], corrected="")
    assert render_markdown(document, SmartTextOptions()) == "# запис.wav\n"
    assert render_plain(document, SmartTextOptions()) == ""


# --- purity -----------------------------------------------------------------


def test_inputs_are_never_modified():
    document = fixture_transcript()
    before_segments = copy.deepcopy(document.segments)
    before_metadata = copy.deepcopy(document.metadata)
    before_raw = document.raw_text
    labels = {0: "Анна"}
    build_paragraphs(document.segments, SmartTextOptions(), labels)
    build_document_paragraphs(document, SmartTextOptions())
    render_markdown(document, SmartTextOptions())
    render_plain(document, SmartTextOptions(timestamps=False))
    speaker_labels_from_metadata(document.metadata)
    assert document.segments == before_segments
    assert document.metadata == before_metadata
    assert document.raw_text == before_raw
    assert labels == {0: "Анна"}
