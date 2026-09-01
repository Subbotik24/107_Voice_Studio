from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from voice_studio.batch import (
    MAX_BATCH_ERROR_CHARS,
    MAX_BATCH_ITEMS,
    BatchItem,
    BatchQueue,
)


def media(directory: Path, name: str) -> Path:
    """Create an empty file with the given name.

    The queue is a pure model: it inspects the suffix and asks whether the path
    is a regular file. It never opens or decodes media, so an empty file is a
    faithful stand-in and keeps these tests free of fixture audio.
    """

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.touch()
    return path


def symlink_or_skip(link: Path, target: Path) -> Path:
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - platform dependent
        pytest.skip(f"symlinks are unavailable on this platform: {exc}")
    return link


def queue_with(*paths: Path) -> BatchQueue:
    queue = BatchQueue()
    added, rejected = queue.add_paths(paths)
    assert rejected == []
    assert len(added) == len(paths)
    return queue


def test_add_paths_accepts_supported_media_and_records_resolved_paths(tmp_path: Path) -> None:
    first = media(tmp_path, "a.wav")
    second = media(tmp_path, "b.mp3")

    queue = BatchQueue()
    added, rejected = queue.add_paths([first, second])

    assert added == [first.resolve(), second.resolve()]
    assert rejected == []
    assert [item.path for item in queue.items] == [first.resolve(), second.resolve()]
    assert [item.status for item in queue.items] == ["pending", "pending"]
    assert all(item.transcript_id is None and item.error is None for item in queue.items)
    assert all(item.seconds == 0.0 for item in queue.items)


def test_add_paths_dedupes_against_existing_items_and_within_one_call(tmp_path: Path) -> None:
    source = media(tmp_path, "a.wav")
    queue = queue_with(source)

    added, rejected = queue.add_paths([source, source, media(tmp_path, "b.wav")])

    assert added == [(tmp_path / "b.wav").resolve()]
    assert [path for path, _ in rejected] == [source.resolve(), source.resolve()]
    assert all("already queued" in reason for _, reason in rejected)
    assert len(queue.items) == 2


def test_add_paths_dedupes_relative_and_absolute_spellings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = media(tmp_path, "a.wav")
    queue = queue_with(source)

    monkeypatch.chdir(tmp_path)
    added, rejected = queue.add_paths([Path("a.wav"), Path("./a.wav")])

    assert added == []
    assert len(rejected) == 2
    assert len(queue.items) == 1


def test_add_paths_rejects_unsupported_suffix(tmp_path: Path) -> None:
    document = media(tmp_path, "notes.txt")
    bare = media(tmp_path, "recording")

    queue = BatchQueue()
    added, rejected = queue.add_paths([document, bare])

    assert added == []
    assert [path for path, _ in rejected] == [document.resolve(), bare.resolve()]
    assert all("unsupported" in reason for _, reason in rejected)
    assert queue.items == ()


def test_add_paths_rejects_missing_file_and_directory(tmp_path: Path) -> None:
    missing = tmp_path / "gone.wav"
    folder = tmp_path / "folder.wav"
    folder.mkdir()

    queue = BatchQueue()
    added, rejected = queue.add_paths([missing, folder])

    assert added == []
    assert [path for path, _ in rejected] == [missing.resolve(), folder.resolve()]
    assert all("regular file" in reason for _, reason in rejected)


def test_add_paths_accepts_symlink_to_file_and_stores_the_resolved_target(tmp_path: Path) -> None:
    target = media(tmp_path / "real", "a.wav")
    link = symlink_or_skip(tmp_path / "link.wav", target)

    queue = BatchQueue()
    added, rejected = queue.add_paths([link])

    assert rejected == []
    assert added == [target.resolve()]
    assert [item.path for item in queue.items] == [target.resolve()]

    # The link and its target are the same queue entry, not two jobs.
    _, second_rejected = queue.add_paths([target])
    assert len(second_rejected) == 1
    assert "already queued" in second_rejected[0][1]


def test_add_paths_rejects_symlink_to_directory(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    link = symlink_or_skip(tmp_path / "link.wav", folder)

    queue = BatchQueue()
    added, rejected = queue.add_paths([link])

    assert added == []
    assert len(rejected) == 1
    assert "regular file" in rejected[0][1]
    assert queue.items == ()


def test_add_paths_caps_the_queue_at_max_batch_items(tmp_path: Path) -> None:
    sources = [media(tmp_path, f"{index:04d}.wav") for index in range(MAX_BATCH_ITEMS + 2)]

    queue = BatchQueue()
    added, rejected = queue.add_paths(sources)

    assert len(added) == MAX_BATCH_ITEMS
    assert len(queue.items) == MAX_BATCH_ITEMS
    assert [path for path, _ in rejected] == [source.resolve() for source in sources[-2:]]
    assert all("full" in reason for _, reason in rejected)

    # A finished item still occupies a slot until it is cleared.
    queue.mark_skipped(sources[0])
    _, still_rejected = queue.add_paths([sources[-1]])
    assert len(still_rejected) == 1
    queue.clear_finished()
    accepted, _ = queue.add_paths([sources[-1]])
    assert accepted == [sources[-1].resolve()]


def test_add_folder_is_sorted_and_shallow_by_default(tmp_path: Path) -> None:
    media(tmp_path, "b.wav")
    media(tmp_path, "a.mp3")
    media(tmp_path, "notes.txt")
    media(tmp_path / "nested", "c.wav")

    queue = BatchQueue()
    added, rejected = queue.add_folder(tmp_path)

    assert added == [(tmp_path / "a.mp3").resolve(), (tmp_path / "b.wav").resolve()]
    assert rejected == []


def test_add_folder_recursive_walks_nested_folders_in_a_stable_order(tmp_path: Path) -> None:
    media(tmp_path, "a.wav")
    media(tmp_path, "b.wav")
    media(tmp_path / "nested", "d.wav")
    media(tmp_path / "nested", "c.wav")
    media(tmp_path / "nested" / "deeper", "e.wav")
    media(tmp_path / "nested", "ignored.txt")

    queue = BatchQueue()
    added, _ = queue.add_folder(tmp_path, recursive=True)

    assert added == [
        (tmp_path / "a.wav").resolve(),
        (tmp_path / "b.wav").resolve(),
        (tmp_path / "nested" / "c.wav").resolve(),
        (tmp_path / "nested" / "d.wav").resolve(),
        (tmp_path / "nested" / "deeper" / "e.wav").resolve(),
    ]
    assert queue.add_folder(tmp_path, recursive=True)[0] == []


def test_add_folder_rejects_a_path_that_is_not_a_folder(tmp_path: Path) -> None:
    source = media(tmp_path, "a.wav")

    queue = BatchQueue()
    added, rejected = queue.add_folder(source)

    assert added == []
    assert [path for path, _ in rejected] == [source.resolve()]
    assert "folder" in rejected[0][1]
    assert queue.items == ()


def test_marks_move_an_item_through_the_full_lifecycle(tmp_path: Path) -> None:
    source = media(tmp_path, "a.wav")
    queue = queue_with(source)

    running = queue.mark_running(source)
    assert running.status == "running"
    assert queue.running() == running

    done = queue.mark_done(source, "transcript-1", 12.5)
    assert done.status == "done"
    assert done.transcript_id == "transcript-1"
    assert done.seconds == 12.5
    assert done.error is None
    assert queue.running() is None
    assert queue.items == (done,)


def test_only_one_item_may_run_at_a_time(tmp_path: Path) -> None:
    first = media(tmp_path, "a.wav")
    second = media(tmp_path, "b.wav")
    queue = queue_with(first, second)

    queue.mark_running(first)
    with pytest.raises(ValueError, match="already running"):
        queue.mark_running(second)

    queue.mark_failed(first, "boom", 1.0)
    assert queue.mark_running(second).status == "running"


def test_illegal_transitions_raise_value_error(tmp_path: Path) -> None:
    source = media(tmp_path, "a.wav")
    queue = queue_with(source)

    with pytest.raises(ValueError, match="pending"):
        queue.mark_done(source, "transcript-1", 1.0)
    with pytest.raises(ValueError, match="pending"):
        queue.mark_failed(source, "boom", 1.0)

    queue.mark_running(source)
    with pytest.raises(ValueError, match="running"):
        queue.mark_skipped(source)

    queue.mark_done(source, "transcript-1", 1.0)
    for illegal in (
        lambda: queue.mark_running(source),
        lambda: queue.mark_done(source, "transcript-2", 1.0),
        lambda: queue.mark_failed(source, "boom", 1.0),
        lambda: queue.mark_skipped(source),
    ):
        with pytest.raises(ValueError, match="done"):
            illegal()
    assert queue.items[0].transcript_id == "transcript-1"


def test_marks_reject_unknown_paths_and_invalid_arguments(tmp_path: Path) -> None:
    source = media(tmp_path, "a.wav")
    queue = queue_with(source)

    with pytest.raises(KeyError):
        queue.mark_running(tmp_path / "b.wav")

    queue.mark_running(source)
    with pytest.raises(ValueError, match="transcript id"):
        queue.mark_done(source, "  ", 1.0)
    with pytest.raises(ValueError, match="seconds"):
        queue.mark_done(source, "transcript-1", -1.0)
    with pytest.raises(ValueError, match="seconds"):
        queue.mark_failed(source, "boom", float("nan"))
    assert queue.items[0].status == "running"


def test_next_pending_keeps_order_and_never_changes_state(tmp_path: Path) -> None:
    first = media(tmp_path, "a.wav")
    second = media(tmp_path, "b.wav")
    third = media(tmp_path, "c.wav")
    queue = queue_with(first, second, third)

    assert queue.next_pending() is not None
    assert queue.next_pending().path == first.resolve()
    assert queue.next_pending() == queue.next_pending()
    assert [item.status for item in queue.items] == ["pending"] * 3

    queue.mark_running(first)
    assert queue.next_pending().path == second.resolve()

    queue.mark_failed(first, "boom", 2.0)
    queue.mark_skipped(second)
    assert queue.next_pending().path == third.resolve()

    queue.mark_running(third)
    queue.mark_done(third, "transcript-1", 3.0)
    assert queue.next_pending() is None


def test_remove_skips_missing_paths_and_refuses_the_running_item(tmp_path: Path) -> None:
    first = media(tmp_path, "a.wav")
    second = media(tmp_path, "b.wav")
    queue = queue_with(first, second)

    assert queue.remove(tmp_path / "c.wav") is False

    queue.mark_running(first)
    with pytest.raises(ValueError, match="running"):
        queue.remove(first)

    assert queue.remove(second) is True
    assert [item.path for item in queue.items] == [first.resolve()]


def test_clear_finished_removes_only_finished_items(tmp_path: Path) -> None:
    first = media(tmp_path, "a.wav")
    second = media(tmp_path, "b.wav")
    third = media(tmp_path, "c.wav")
    fourth = media(tmp_path, "d.wav")
    queue = queue_with(first, second, third, fourth)

    queue.mark_running(first)
    queue.mark_done(first, "transcript-1", 1.0)
    queue.mark_running(second)
    queue.mark_failed(second, "boom", 2.0)
    queue.mark_skipped(third)

    assert queue.clear_finished() == 3
    assert [item.path for item in queue.items] == [fourth.resolve()]
    assert queue.clear_finished() == 0


def test_clear_requires_that_nothing_is_running(tmp_path: Path) -> None:
    first = media(tmp_path, "a.wav")
    second = media(tmp_path, "b.wav")
    queue = queue_with(first, second)

    queue.mark_running(first)
    with pytest.raises(ValueError, match="running"):
        queue.clear()
    assert len(queue.items) == 2

    queue.mark_done(first, "transcript-1", 1.0)
    queue.clear()
    assert queue.items == ()
    assert queue.summary().total == 0


def test_summary_counts_every_status_and_totals_job_seconds(tmp_path: Path) -> None:
    paths = [media(tmp_path, f"{name}.wav") for name in "abcde"]
    queue = queue_with(*paths)

    queue.mark_running(paths[0])
    queue.mark_done(paths[0], "transcript-1", 1.5)
    queue.mark_running(paths[1])
    queue.mark_failed(paths[1], "boom", 2.25)
    queue.mark_skipped(paths[2])
    queue.mark_running(paths[3])

    summary = queue.summary()
    assert (summary.total, summary.done, summary.failed) == (5, 1, 1)
    assert (summary.skipped, summary.running, summary.pending) == (1, 1, 1)
    assert summary.seconds == pytest.approx(3.75)
    assert summary.done + summary.failed + summary.skipped + summary.running + summary.pending == (
        summary.total
    )


def test_pause_is_a_plain_flag_that_does_not_touch_the_queue(tmp_path: Path) -> None:
    source = media(tmp_path, "a.wav")
    queue = queue_with(source)

    assert queue.paused is False
    queue.pause()
    assert queue.paused is True
    # Pausing is advisory: the model still reports the next item, the GUI decides.
    assert queue.next_pending().path == source.resolve()
    queue.pause()
    assert queue.paused is True
    queue.resume()
    assert queue.paused is False


def test_error_text_is_bounded(tmp_path: Path) -> None:
    source = media(tmp_path, "a.wav")
    queue = queue_with(source)

    queue.mark_running(source)
    item = queue.mark_failed(source, "e" * (MAX_BATCH_ERROR_CHARS + 250), 1.0)

    assert MAX_BATCH_ERROR_CHARS == 500
    assert item.error is not None
    assert len(item.error) == MAX_BATCH_ERROR_CHARS
    assert item.error == "e" * MAX_BATCH_ERROR_CHARS


def test_items_are_immutable_snapshots(tmp_path: Path) -> None:
    source = media(tmp_path, "a.wav")
    queue = queue_with(source)

    before = queue.items
    item = before[0]
    with pytest.raises(FrozenInstanceError):
        item.status = "done"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        queue.summary().total = 99  # type: ignore[misc]

    queue.mark_running(source)
    # The snapshot taken earlier is unaffected by the transition.
    assert before[0].status == "pending"
    assert queue.items[0].status == "running"
    assert queue.items is not before


def test_batch_item_defaults_are_pending_and_empty() -> None:
    item = BatchItem(path=Path("/tmp/a.wav"))

    assert (item.status, item.transcript_id, item.error, item.seconds) == (
        "pending",
        None,
        None,
        0.0,
    )
