import json
from pathlib import Path

import pytest

from voice_studio.models import Transcript
from voice_studio.sync_folder import (
    SyncFolderError,
    SyncSummary,
    mirror_all,
    mirror_transcript,
    transcript_mirror_names,
    validate_sync_root,
)


def _transcript(**overrides) -> Transcript:
    fields = dict(
        id="12345678-aaaa-bbbb-cccc-1234567890ab",
        created_at="2026-08-30T12:34:56+00:00",
        source_name="Interview.wav",
        source_sha256="a" * 64,
        language="uk",
        engine="ollama",
        model="gemma4:12b",
        raw_text="raw text",
        corrected_text="corrected text",
        segments=[],
        dictionary_version="none",
        audio_retained=True,
        source_path=None,
        status="completed",
        audio_seconds=12.5,
        real_time_factor=0.4,
        error=None,
        metadata={},
    )
    fields.update(overrides)
    return Transcript(**fields)


def _all_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


# --- validate_sync_root -----------------------------------------------------


def test_validate_sync_root_accepts_a_real_directory_outside_data_root(tmp_path):
    sync_root = tmp_path / "drive"
    sync_root.mkdir()
    data_root = tmp_path / "data"
    data_root.mkdir()

    resolved = validate_sync_root(sync_root, data_root=data_root)

    assert resolved == sync_root.resolve()


def test_validate_sync_root_rejects_missing_directory(tmp_path):
    with pytest.raises(SyncFolderError):
        validate_sync_root(tmp_path / "does-not-exist", data_root=tmp_path / "data")


def test_validate_sync_root_rejects_a_file(tmp_path):
    target = tmp_path / "not-a-dir.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(SyncFolderError):
        validate_sync_root(target, data_root=tmp_path / "data")


def test_validate_sync_root_rejects_a_symlink_directory(tmp_path):
    real_target = tmp_path / "real_target"
    real_target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real_target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not supported in this environment")

    with pytest.raises(SyncFolderError):
        validate_sync_root(link, data_root=tmp_path / "data")


def test_validate_sync_root_rejects_a_folder_inside_data_root(tmp_path):
    data_root = tmp_path / "data"
    sync_root = data_root / "sub" / "drive"
    sync_root.mkdir(parents=True)

    with pytest.raises(SyncFolderError, match="inside the application data folder"):
        validate_sync_root(sync_root, data_root=data_root)


def test_validate_sync_root_rejects_when_data_root_is_inside_it(tmp_path):
    sync_root = tmp_path / "drive"
    data_root = sync_root / "appdata"
    data_root.mkdir(parents=True)

    with pytest.raises(SyncFolderError, match="must not contain"):
        validate_sync_root(sync_root, data_root=data_root)


def test_validate_sync_root_rejects_the_data_root_itself(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()

    with pytest.raises(SyncFolderError):
        validate_sync_root(data_root, data_root=data_root)


# --- transcript_mirror_names -------------------------------------------------


def test_transcript_mirror_names_are_deterministic():
    transcript = _transcript()

    md_name, json_name = transcript_mirror_names(transcript)

    assert md_name == "2026-08-30_1234_Interview-wav_12345678.md"
    assert json_name == "2026-08-30_1234_Interview-wav_12345678.json"
    # calling again with an equal transcript yields the exact same names
    assert transcript_mirror_names(_transcript()) == (md_name, json_name)


def test_transcript_mirror_names_slug_collapses_and_strips_separators():
    transcript = _transcript(source_name="  --Hello_World 123--  ")

    md_name, _ = transcript_mirror_names(transcript)

    assert md_name.split("_", 2)[2] == "Hello-World-123_12345678.md"


def test_transcript_mirror_names_slug_keeps_unicode_letters_and_digits():
    transcript = _transcript(source_name="Нарада: № 5")

    md_name, _ = transcript_mirror_names(transcript)

    assert md_name.split("_", 2)[2] == "Нарада-5_12345678.md"


def test_transcript_mirror_names_slug_is_cut_to_60_characters():
    transcript = _transcript(source_name="A" * 200)

    md_name, _ = transcript_mirror_names(transcript)

    slug = md_name.split("_", 2)[2].rsplit("_", 1)[0]
    assert slug == "A" * 60


def test_transcript_mirror_names_fall_back_to_undated_subfolder(tmp_path):
    transcript = _transcript(created_at="not-a-timestamp")
    root = tmp_path / "drive"
    sources = tmp_path / "sources"
    sources.mkdir()

    paths = mirror_transcript(transcript, root, include_audio=False, sources_root=sources)

    assert paths[0].parent.name == "undated"


# --- mirror_transcript: content ----------------------------------------------


def test_mirror_transcript_markdown_has_no_absolute_path_or_source_path(tmp_path):
    root = tmp_path / "drive"
    sources = tmp_path / "sources"
    sources.mkdir()
    transcript = _transcript(
        source_name="Meeting notes.wav",
        corrected_text="Hello, this is the cleaned transcript text.",
        source_path=str(sources / "secret-original.wav"),
    )

    paths = mirror_transcript(transcript, root, include_audio=False, sources_root=sources)
    md_path = next(path for path in paths if path.suffix == ".md")
    content = md_path.read_text(encoding="utf-8")

    assert "Hello, this is the cleaned transcript text." in content
    assert "source_path" not in content
    assert "secret-original.wav" not in content
    assert str(tmp_path) not in content
    assert str(sources) not in content


def test_mirror_transcript_json_has_no_source_path(tmp_path):
    root = tmp_path / "drive"
    sources = tmp_path / "sources"
    sources.mkdir()
    transcript = _transcript(source_path=str(sources / "secret-original.wav"))

    paths = mirror_transcript(transcript, root, include_audio=False, sources_root=sources)
    json_path = next(path for path in paths if path.suffix == ".json")
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert "source_path" not in payload
    assert payload["id"] == transcript.id
    assert payload["corrected_text"] == transcript.corrected_text


# --- mirror_transcript: audio -------------------------------------------------


def test_mirror_transcript_include_audio_copies_managed_retained_file(tmp_path):
    root = tmp_path / "drive"
    sources = tmp_path / "sources"
    sources.mkdir()
    audio = sources / "clip.wav"
    audio.write_bytes(b"managed-audio-bytes")
    transcript = _transcript(source_path=str(audio), audio_retained=True)

    paths = mirror_transcript(transcript, root, include_audio=True, sources_root=sources)

    assert len(paths) == 3
    audio_copy = next(path for path in paths if path.suffix == ".wav")
    assert audio_copy.read_bytes() == b"managed-audio-bytes"
    assert audio_copy != audio


def test_mirror_transcript_include_audio_skips_when_audio_not_retained(tmp_path):
    root = tmp_path / "drive"
    sources = tmp_path / "sources"
    sources.mkdir()
    audio = sources / "clip.wav"
    audio.write_bytes(b"managed-audio-bytes")
    transcript = _transcript(source_path=str(audio), audio_retained=False)

    paths = mirror_transcript(transcript, root, include_audio=True, sources_root=sources)

    assert len(paths) == 2


def test_mirror_transcript_include_audio_skips_external_source(tmp_path):
    root = tmp_path / "drive"
    sources = tmp_path / "sources"
    sources.mkdir()
    external = tmp_path / "outside" / "original.wav"
    external.parent.mkdir()
    external.write_bytes(b"external-original-bytes")
    transcript = _transcript(source_path=str(external), audio_retained=True)

    paths = mirror_transcript(transcript, root, include_audio=True, sources_root=sources)

    assert len(paths) == 2
    assert external.read_bytes() == b"external-original-bytes"


def test_mirror_transcript_include_audio_skips_missing_file(tmp_path):
    root = tmp_path / "drive"
    sources = tmp_path / "sources"
    sources.mkdir()
    transcript = _transcript(source_path=str(sources / "missing.wav"), audio_retained=True)

    paths = mirror_transcript(transcript, root, include_audio=True, sources_root=sources)

    assert len(paths) == 2


# --- mirror_transcript: idempotency and safety --------------------------------


def test_mirror_transcript_is_idempotent_on_rerun(tmp_path):
    root = tmp_path / "drive"
    sources = tmp_path / "sources"
    sources.mkdir()
    audio = sources / "clip.wav"
    audio.write_bytes(b"first-bytes")
    transcript = _transcript(source_path=str(audio), audio_retained=True)

    first = mirror_transcript(transcript, root, include_audio=True, sources_root=sources)
    files_after_first = sorted(_all_files(root))

    second = mirror_transcript(transcript, root, include_audio=True, sources_root=sources)
    files_after_second = sorted(_all_files(root))

    assert first == second
    assert files_after_first == files_after_second


def test_mirror_transcript_never_deletes_stray_files_in_root(tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    stray = root / "my-own-note.txt"
    stray.write_text("do not touch", encoding="utf-8")
    sources = tmp_path / "sources"
    sources.mkdir()
    transcript = _transcript()

    mirror_transcript(transcript, root, include_audio=False, sources_root=sources)

    assert stray.exists()
    assert stray.read_text(encoding="utf-8") == "do not touch"


def test_mirror_transcript_leaves_no_leftover_temp_files(tmp_path):
    root = tmp_path / "drive"
    sources = tmp_path / "sources"
    sources.mkdir()
    audio = sources / "clip.wav"
    audio.write_bytes(b"bytes")
    transcript = _transcript(source_path=str(audio), audio_retained=True)

    mirror_transcript(transcript, root, include_audio=True, sources_root=sources)

    leftovers = [path for path in _all_files(root) if path.suffix == ".tmp"]
    assert leftovers == []


# --- mirror_all ---------------------------------------------------------------


def test_mirror_all_mirrors_every_transcript(tmp_path):
    root = tmp_path / "drive"
    sources = tmp_path / "sources"
    sources.mkdir()
    transcripts = [
        _transcript(id="11111111-1111-1111-1111-111111111111", source_name="One.wav"),
        _transcript(
            id="22222222-2222-2222-2222-222222222222",
            source_name="Two.wav",
            created_at="2026-07-01T00:00:00+00:00",
        ),
    ]

    summary = mirror_all(transcripts, root, include_audio=False, sources_root=sources)

    assert summary == SyncSummary(written=2, audio=0, failed=())
    assert len(_all_files(root)) == 4


def test_mirror_all_captures_a_failing_transcript_and_continues(tmp_path):
    root = tmp_path / "drive"
    root.mkdir()
    sources = tmp_path / "sources"
    sources.mkdir()

    good = _transcript(
        id="11111111-1111-1111-1111-111111111111",
        source_name="Good.wav",
        created_at="2026-07-01T00:00:00+00:00",
    )
    bad = _transcript(
        id="22222222-2222-2222-2222-222222222222",
        source_name="Bad.wav",
        created_at="2026-08-30T12:34:56+00:00",
    )
    # Block the subfolder the "bad" transcript needs by pre-creating it as a
    # plain file, so writing into it raises a real, non-monkeypatched error.
    (root / "2026-08").write_text("blocking file", encoding="utf-8")

    summary = mirror_all([good, bad], root, include_audio=False, sources_root=sources)

    assert summary.written == 1
    assert summary.audio == 0
    assert len(summary.failed) == 1
    failed_id, message = summary.failed[0]
    assert failed_id == bad.id
    assert isinstance(message, str) and message
    # the good transcript's files were still written despite the failure
    good_md, _ = transcript_mirror_names(good)
    assert (root / "2026-07" / good_md).exists()


def test_mirror_all_returns_empty_summary_for_no_transcripts(tmp_path):
    root = tmp_path / "drive"
    sources = tmp_path / "sources"
    sources.mkdir()

    summary = mirror_all([], root, include_audio=False, sources_root=sources)

    assert summary == SyncSummary(written=0, audio=0, failed=())
