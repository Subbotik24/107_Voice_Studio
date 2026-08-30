"""W3-E1 minimal subtitle consistency regression tests.

Rule: an edit does not create time. Manual document edits update only the
editable segment layer; edits crossing segment boundaries merge exactly the
touched neighbours into their existing outer interval. No timestamp is ever
created, interpolated, split or moved, and raw text is immutable.
"""

import pytest

from voice_studio.backup import create_backup, restore_backup, verify_backup
from voice_studio.dictionary import DictionaryRule, TerminologyDictionary
from voice_studio.exporters import export_transcript
from voice_studio.models import Segment, Transcript
from voice_studio.service import PreparedSource, TranscriptionService
from voice_studio.storage import LocalStore


def _segmented_transcript() -> Transcript:
    return Transcript(
        id="1",
        created_at="2026-08-30T00:00:00+00:00",
        source_name="note.wav",
        source_sha256="b" * 64,
        language="en",
        engine="faster-whisper",
        model="small",
        raw_text="hello small world",
        corrected_text="hello small world",
        segments=[
            Segment(
                start=0.0,
                end=1.0,
                text="hello",
                corrected_text="hello",
                language="en",
                confidence=0.91,
            ),
            Segment(
                start=1.0,
                end=2.0,
                text="small",
                corrected_text="small",
                language="en",
                confidence=0.82,
            ),
            Segment(
                start=2.0,
                end=3.0,
                text="world",
                corrected_text="world",
                language="en",
                confidence=0.73,
            ),
        ],
    )


def _save(store: LocalStore, item: Transcript | None = None) -> Transcript:
    item = item or _segmented_transcript()
    store.save(item)
    return item


def test_edit_inside_segment_changes_only_that_segment(tmp_path):
    store = LocalStore(tmp_path)
    _save(store)

    updated = store.update_editor_state("1", "hello small wurld", {})

    assert updated.corrected_text == "hello small wurld"
    assert [s.display_text for s in updated.segments] == ["hello", "small", "wurld"]
    assert [(s.start, s.end) for s in updated.segments] == [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    assert [s.text for s in updated.segments] == ["hello", "small", "world"]
    assert [s.language for s in updated.segments] == ["en", "en", "en"]
    assert [s.confidence for s in updated.segments] == [0.91, 0.82, 0.73]
    assert updated.raw_text == "hello small world"


def test_insertion_exactly_at_a_boundary_extends_the_previous_segment(tmp_path):
    store = LocalStore(tmp_path)
    _save(store)

    updated = store.update_editor_state("1", "hello brave small world", {})

    assert [s.display_text for s in updated.segments] == ["hello brave", "small", "world"]
    assert [(s.start, s.end) for s in updated.segments] == [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    assert [s.text for s in updated.segments] == ["hello", "small", "world"]


def test_edit_across_one_boundary_merges_exactly_the_touched_segments(tmp_path):
    store = LocalStore(tmp_path)
    _save(store)

    updated = store.update_editor_state("1", "hello big earth", {})

    assert len(updated.segments) == 2
    merged = updated.segments[1]
    assert (merged.start, merged.end) == (1.0, 3.0)
    assert merged.text == "small world"
    assert merged.corrected_text == "big earth"
    assert updated.segments[0].display_text == "hello"
    assert updated.raw_text == "hello small world"


def test_edit_across_several_boundaries_merges_the_outer_interval(tmp_path):
    store = LocalStore(tmp_path)
    _save(store)

    updated = store.update_editor_state("1", "completely new wording", {})

    assert len(updated.segments) == 1
    merged = updated.segments[0]
    assert (merged.start, merged.end) == (0.0, 3.0)
    assert merged.text == "hello small world"
    assert merged.corrected_text == "completely new wording"
    assert updated.raw_text == "hello small world"


def test_independent_edits_do_not_merge_untouched_middle_segment(tmp_path):
    store = LocalStore(tmp_path)
    _save(store)

    updated = store.update_editor_state("1", "hallo small wurld", {})

    assert [s.display_text for s in updated.segments] == ["hallo", "small", "wurld"]
    assert [(s.start, s.end) for s in updated.segments] == [
        (0.0, 1.0),
        (1.0, 2.0),
        (2.0, 3.0),
    ]
    assert [s.confidence for s in updated.segments] == [0.91, 0.82, 0.73]


def test_independent_edits_in_adjacent_segments_keep_both_cues(tmp_path):
    store = LocalStore(tmp_path)
    _save(store)

    updated = store.update_editor_state("1", "hallo tiny world", {})

    assert [s.display_text for s in updated.segments] == ["hallo", "tiny", "world"]
    assert [(s.start, s.end) for s in updated.segments] == [
        (0.0, 1.0),
        (1.0, 2.0),
        (2.0, 3.0),
    ]
    assert [s.confidence for s in updated.segments] == [0.91, 0.82, 0.73]


def test_independent_edits_in_every_segment_do_not_look_like_replace_all(tmp_path):
    store = LocalStore(tmp_path)
    _save(store)

    updated = store.update_editor_state("1", "hallo tiny wurld", {})

    assert [s.display_text for s in updated.segments] == ["hallo", "tiny", "wurld"]
    assert [(s.start, s.end) for s in updated.segments] == [
        (0.0, 1.0),
        (1.0, 2.0),
        (2.0, 3.0),
    ]


def test_ambiguous_boundary_insertion_belongs_to_previous_segment(tmp_path):
    store = LocalStore(tmp_path)
    item = _segmented_transcript()
    item.raw_text = "abc def"
    item.corrected_text = "abc def"
    item.segments = [
        Segment(start=0.0, end=1.0, text="abc", corrected_text="abc"),
        Segment(start=1.0, end=2.0, text="def", corrected_text="def"),
    ]
    _save(store, item)

    updated = store.update_editor_state("1", "abc XYZ def", {})

    assert [s.display_text for s in updated.segments] == ["abc XYZ", "def"]
    assert [(s.start, s.end) for s in updated.segments] == [(0.0, 1.0), (1.0, 2.0)]


def test_deleting_a_segment_text_removes_only_that_segment(tmp_path):
    store = LocalStore(tmp_path)
    _save(store)

    updated = store.update_editor_state("1", "hello world", {})

    assert [s.display_text for s in updated.segments] == ["hello", "world"]
    assert [(s.start, s.end) for s in updated.segments] == [(0.0, 2.0), (2.0, 3.0)]
    assert [s.text for s in updated.segments] == ["hello small", "world"]
    assert " ".join(s.text for s in updated.segments) == updated.raw_text


def test_raw_segment_sequence_survives_an_edit_after_deletion(tmp_path):
    store = LocalStore(tmp_path)
    _save(store)

    store.update_editor_state("1", "hello world", {})
    updated = store.update_editor_state("1", "hello planet", {})

    assert " ".join(s.text for s in updated.segments) == updated.raw_text
    assert [s.display_text for s in updated.segments] == ["hello", "planet"]


def test_delete_all_removes_every_segment_and_interval(tmp_path):
    store = LocalStore(tmp_path)
    _save(store)

    updated = store.update_editor_state("1", "", {})

    assert updated.corrected_text == ""
    assert updated.segments == []
    assert updated.raw_text == "hello small world"


def test_no_segments_keeps_document_only_behaviour(tmp_path):
    store = LocalStore(tmp_path)
    item = _segmented_transcript()
    item.segments = []
    store.save(item)

    updated = store.update_editor_state("1", "free text without timing", {})

    assert updated.corrected_text == "free text without timing"
    assert updated.segments == []


def test_noop_save_is_idempotent_and_stores_no_undo(tmp_path):
    store = LocalStore(tmp_path)
    item = _save(store)

    first = store.update_editor_state("1", item.corrected_text, {})
    second = store.update_editor_state("1", item.corrected_text, {})

    assert [s.to_dict() for s in first.segments] == [s.to_dict() for s in item.segments]
    assert [s.to_dict() for s in second.segments] == [s.to_dict() for s in item.segments]
    assert "manual_edit_undo" not in second.metadata
    assert "manual_edit_undo" not in store.get("1").metadata


def test_undo_restores_the_previous_segment_list(tmp_path):
    store = LocalStore(tmp_path)
    item = _save(store)

    store.update_editor_state("1", "hello big earth", {"bold": [("1.0", "1.4")]})
    restored = store.undo_last_manual_edit("1")

    assert restored.corrected_text == item.corrected_text
    assert [s.to_dict() for s in restored.segments] == [s.to_dict() for s in item.segments]
    persisted = store.get("1")
    assert [s.to_dict() for s in persisted.segments] == [s.to_dict() for s in item.segments]
    assert "manual_edit_undo" not in persisted.metadata
    with pytest.raises(ValueError, match="manual edit"):
        store.undo_last_manual_edit("1")


def test_undo_rejects_invalid_segment_snapshot_without_mutation(tmp_path):
    store = LocalStore(tmp_path)
    item = _segmented_transcript()
    invalid_segments = [segment.to_dict() for segment in item.segments]
    invalid_segments[0]["start"] = "not-a-timestamp"
    item.corrected_text = "current text"
    item.metadata["manual_edit_undo"] = {
        "version": 1,
        "corrected_text": "hello small world",
        "segments": invalid_segments,
        "editor_formatting": None,
    }
    store.save(item)

    with pytest.raises(ValueError, match="stored manual edit revision is invalid"):
        store.undo_last_manual_edit("1")

    persisted = store.get("1")
    assert persisted.corrected_text == "current text"
    assert persisted.metadata["manual_edit_undo"]["segments"][0]["start"] == (
        "not-a-timestamp"
    )


def test_undo_rejects_document_segment_mismatch_without_mutation(tmp_path):
    store = LocalStore(tmp_path)
    item = _segmented_transcript()
    item.corrected_text = "current text"
    item.metadata["manual_edit_undo"] = {
        "version": 1,
        "corrected_text": "different document",
        "segments": [segment.to_dict() for segment in item.segments],
        "editor_formatting": None,
    }
    store.save(item)

    with pytest.raises(ValueError, match="stored manual edit revision is invalid"):
        store.undo_last_manual_edit("1")

    persisted = store.get("1")
    assert persisted.corrected_text == "current text"
    assert persisted.metadata["manual_edit_undo"]["corrected_text"] == (
        "different document"
    )


def test_backup_round_trip_preserves_merged_segments(tmp_path):
    store = LocalStore(tmp_path / "data")
    _save(store)
    store.update_editor_state("1", "hello big earth", {})
    settings_file = tmp_path / "settings.json"
    settings_file.write_text("{}", encoding="utf-8")
    backup_file = tmp_path / "backup.zip"

    create_backup(store, backup_file, settings_file=settings_file)
    assert verify_backup(backup_file)["status"] == "PASS"

    restored_root = tmp_path / "restored"
    result = restore_backup(
        backup_file,
        restored_root,
        settings_target=tmp_path / "restored-settings.json",
    )
    assert result["status"] == "PASS"

    restored = LocalStore(restored_root).get("1")
    assert restored.corrected_text == "hello big earth"
    assert [s.to_dict() for s in restored.segments] == [
        s.to_dict() for s in store.get("1").segments
    ]
    assert restored.raw_text == "hello small world"


def test_txt_md_srt_vtt_carry_the_same_corrected_wording(tmp_path):
    store = LocalStore(tmp_path)
    _save(store)
    updated = store.update_editor_state("1", "hello big earth", {})

    txt = (tmp_path / "out.txt")
    md = tmp_path / "out.md"
    srt = tmp_path / "out.srt"
    vtt = tmp_path / "out.vtt"
    export_transcript(updated, "txt", txt)
    export_transcript(updated, "md", md)
    export_transcript(updated, "srt", srt)
    export_transcript(updated, "vtt", vtt)

    document = " ".join(s.display_text for s in updated.segments)
    assert document == "hello big earth"
    assert "hello big earth" in txt.read_text(encoding="utf-8")
    assert "hello big earth" in md.read_text(encoding="utf-8")
    srt_text = srt.read_text(encoding="utf-8")
    vtt_text = vtt.read_text(encoding="utf-8")
    assert "hello big earth" not in srt_text  # merged cue carries its own corrected line
    assert "big earth" in srt_text
    assert "big earth" in vtt_text
    assert "small world" not in srt_text
    assert "00:00:01,000 --> 00:00:03,000" in srt_text
    assert "00:00:01.000 --> 00:00:03.000" in vtt_text


def test_multiword_dictionary_rule_stays_consistent_between_document_and_subtitles(tmp_path):
    store = LocalStore(tmp_path)
    dictionary = TerminologyDictionary(
        [DictionaryRule(source="small world", target="earth")]
    )
    service = TranscriptionService(store, engine=None, dictionary=dictionary)
    result_segments = [
        Segment(start=0.0, end=1.0, text="hello"),
        Segment(start=1.0, end=2.0, text="small"),
        Segment(start=2.0, end=3.0, text="world"),
    ]
    from voice_studio.engines.base import EngineResult

    result = EngineResult(
        engine="faster-whisper",
        model="small",
        language="en",
        segments=result_segments,
    )
    source = tmp_path / "note.wav"
    source.write_bytes(b"RIFF-note")
    managed, digest = store.import_source(source)
    prepared = PreparedSource(source, managed, digest)

    transcript = service.finalize(prepared, result, "en", "keep")

    document = transcript.corrected_text
    subtitles = " ".join(s.display_text for s in transcript.segments)
    assert document == subtitles
    assert "earth" not in document  # the rule no longer fires silently in one view only
    txt = tmp_path / "doc.txt"
    srt = tmp_path / "doc.srt"
    export_transcript(transcript, "txt", txt)
    export_transcript(transcript, "srt", srt)
    assert "hello small world" in txt.read_text(encoding="utf-8")
    assert "hello small world" not in srt.read_text(encoding="utf-8")
    assert "small" in srt.read_text(encoding="utf-8")


def test_multiword_dictionary_rule_applies_identically_within_one_segment(tmp_path):
    store = LocalStore(tmp_path)
    dictionary = TerminologyDictionary(
        [DictionaryRule(source="small world", target="earth")]
    )
    service = TranscriptionService(store, engine=None, dictionary=dictionary)
    from voice_studio.engines.base import EngineResult

    result = EngineResult(
        engine="faster-whisper",
        model="small",
        language="en",
        segments=[Segment(start=0.0, end=2.0, text="hello small world")],
    )
    source = tmp_path / "note.wav"
    source.write_bytes(b"RIFF-note")
    managed, digest = store.import_source(source)
    prepared = PreparedSource(source, managed, digest)

    transcript = service.finalize(prepared, result, "en", "keep")

    assert transcript.corrected_text == "hello earth"
    assert [s.display_text for s in transcript.segments] == ["hello earth"]


def test_raw_text_and_timestamps_are_byte_identical_after_a_sequence_of_edits(tmp_path):
    store = LocalStore(tmp_path)
    item = _save(store)
    raw_before = item.raw_text.encode("utf-8")
    original_intervals = {(s.start, s.end) for s in item.segments}

    store.update_editor_state("1", "hello brave small world", {})
    store.update_editor_state("1", "hello brave big earth", {})
    final = store.update_editor_state("1", "final wording", {})

    assert store.get("1").raw_text.encode("utf-8") == raw_before
    raw_join = " ".join(s.text.strip() for s in final.segments if s.text.strip())
    assert raw_join == item.raw_text
    for segment in final.segments:
        # A surviving interval is either an untouched original interval or the
        # outer interval of merged neighbours; both already existed in the data.
        assert (segment.start, segment.end) == (0.0, 3.0)
    assert original_intervals == {(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)}
