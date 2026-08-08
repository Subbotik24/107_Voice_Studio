import sqlite3

import pytest

from hermes_voice_studio.models import Transcript
from hermes_voice_studio.storage import LocalStore


def transcript() -> Transcript:
    return Transcript(
        id="1",
        created_at="2026-07-26T00:00:00+00:00",
        source_name="note.wav",
        source_sha256="b" * 64,
        language="cs",
        engine="faster-whisper",
        model="small",
        raw_text="technická poznámka",
        corrected_text="technická poznámka",
    )


def test_history_search(tmp_path):
    store = LocalStore(tmp_path)
    item = transcript()
    store.save(item)
    assert store.list("technická") == [item]
    assert store.list("missing") == []


def test_store_connection_context_releases_sqlite_handle(tmp_path):
    """The store must release SQLite files before backup restore swaps directories."""

    store = LocalStore(tmp_path)
    with store._connect() as connection:
        connection.execute("SELECT 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_legacy_payload_defaults_engine():
    data = transcript().to_dict()
    data.pop("engine")
    restored = Transcript.from_dict(data)
    assert restored.engine == "faster-whisper"


def test_shared_source_is_not_deleted_while_another_record_references_it(tmp_path):
    store = LocalStore(tmp_path / "data")
    source = tmp_path / "same.wav"
    source.write_bytes(b"same-content")
    managed, digest = store.import_source(source)
    first = transcript()
    first.source_sha256 = digest
    first.source_path = str(managed)
    second = Transcript.from_dict(first.to_dict())
    second.id = "2"
    store.save(first)
    store.save(second)
    store.delete_audio(first)
    assert managed.exists()
    store.delete_audio(second)
    assert not managed.exists()


def test_raw_text_is_immutable_and_corrected_text_has_explicit_update(tmp_path):
    store = LocalStore(tmp_path)
    item = transcript()
    store.save(item)
    item.raw_text = "mutated"
    with pytest.raises(ValueError, match="raw_text"):
        store.save(item)
    updated = store.update_corrected_text("1", "corrected")
    assert updated.raw_text == "technická poznámka"
    assert updated.corrected_text == "corrected"


def test_source_name_can_be_renamed_without_changing_transcript_content(tmp_path):
    store = LocalStore(tmp_path)
    item = transcript()
    store.save(item)

    renamed = store.rename_source_name("1", "зустріч_з_командою.wav")

    assert renamed.source_name == "зустріч_з_командою.wav"
    assert renamed.raw_text == "technická poznámka"
    assert renamed.corrected_text == item.corrected_text
    assert store.get("1").source_name == "зустріч_з_командою.wav"


def test_editor_formatting_is_saved_without_changing_raw_or_corrected_text(tmp_path):
    store = LocalStore(tmp_path)
    item = transcript()
    store.save(item)

    updated = store.update_editor_formatting(
        "1", {"bold": [("1.0", "1.4")], "italic": [("1.5", "1.8")]}
    )

    assert updated.raw_text == item.raw_text
    assert updated.corrected_text == item.corrected_text
    assert updated.metadata["editor_formatting"]["bold"] == [["1.0", "1.4"]]


def test_schema_version_and_legacy_engine_migration(tmp_path):
    db_path = tmp_path / "history.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE transcripts (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                language TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
    LocalStore(tmp_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        columns = {row[1] for row in db.execute("PRAGMA table_info(transcripts)")}
    assert "engine" in columns


def test_storage_audit_and_explicit_orphan_cleanup(tmp_path):
    store = LocalStore(tmp_path / "data")
    orphan = store.sources / "orphan.wav"
    orphan.write_bytes(b"managed orphan")
    audit = store.audit()
    assert audit["status"] == "PASS"
    assert audit["orphans"] == [str(orphan.resolve())]
    with pytest.raises(ValueError, match="--yes"):
        store.cleanup_orphans()
    result = store.cleanup_orphans(confirmed=True)
    assert result["count"] == 1
    assert not orphan.exists()
