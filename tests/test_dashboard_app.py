import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from voice_studio.dashboard import (
    DashboardStatistics,
    HistoryFilter,
    aggregate_statistics,
    daily_activity,
)
from voice_studio.models import Transcript
from voice_studio.storage import LocalStore

NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def payload(**overrides):
    data = {
        "id": overrides.pop("id", "1"),
        "created_at": "2026-08-30T09:00:00+00:00",
        "source_name": "note.wav",
        "source_sha256": "b" * 64,
        "language": "uk",
        "engine": "ollama",
        "model": "gemma4:12b",
        "raw_text": "raw",
        "corrected_text": "corrected",
        "segments": [],
        "dictionary_version": "none",
        "audio_retained": True,
        "source_path": None,
        "status": "completed",
        "audio_seconds": 0.0,
        "real_time_factor": None,
        "error": None,
        "metadata": {},
    }
    data.update(overrides)
    return data


def legacy_payload(**overrides):
    data = {
        "id": overrides.pop("id", "legacy"),
        "created_at": "2026-08-30T09:00:00+00:00",
        "source_name": "legacy.wav",
        "source_sha256": "c" * 64,
        "language": "cs",
        "engine": "faster-whisper",
        "model": "small",
        "raw_text": "stara poznamka",
        "corrected_text": "stara poznamka",
        "segments": [],
        "dictionary_version": "none",
        "audio_retained": True,
        "source_path": None,
        "status": "completed",
    }
    data.update(overrides)
    return data


def insert_payloads(store, *payloads):
    """Insert rows directly so invalid payloads bypass save() validation."""

    connection = sqlite3.connect(store.db_path)
    try:
        with connection:
            for item in payloads:
                if isinstance(item, str):
                    body = item
                    columns = {}
                else:
                    body = json.dumps(item, ensure_ascii=False)
                    columns = item
                connection.execute(
                    """
                    INSERT OR REPLACE INTO transcripts
                    (id, created_at, source_sha256, language, engine, model, status,
                     payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(columns.get("id", body[:16])),
                        str(columns.get("created_at", "2026-08-30T09:00:00+00:00")),
                        str(columns.get("source_sha256", "0" * 64)),
                        str(columns.get("language", "")),
                        str(columns.get("engine", "")),
                        str(columns.get("model", "")),
                        str(columns.get("status", "")),
                        body,
                    ),
                )
    finally:
        connection.close()


def statistics_of(*payloads, now=NOW):
    return aggregate_statistics(
        (item if isinstance(item, str) else json.dumps(item) for item in payloads),
        now=now,
    )


def transcript_of(**overrides):
    return Transcript.from_dict(payload(**overrides))


def test_empty_store_statistics_are_zero(tmp_path):
    stats = LocalStore(tmp_path).statistics(now=NOW)

    assert isinstance(stats, DashboardStatistics)
    assert stats.total_records == 0
    assert stats.completed_records == 0
    assert stats.failed_records == 0
    assert stats.invalid_records == 0
    assert stats.audio_seconds_total == 0.0
    assert stats.word_count_total == 0
    assert stats.retained_audio_records == 0
    assert stats.records_last_7_days == 0
    assert stats.records_last_30_days == 0
    assert stats.language_counts == ()
    assert stats.engine_counts == ()
    assert stats.model_counts == ()
    assert stats.weighted_real_time_factor is None
    assert stats.speed_multiplier is None


def test_statistics_and_filtered_list_ignore_any_ui_row_limit(tmp_path):
    store = LocalStore(tmp_path)
    rows = [
        payload(
            id=f"new-{index:03d}",
            created_at=f"2026-08-{(index % 20) + 10:02d}T{index % 24:02d}:00:00+00:00",
            source_name=f"recent-{index}.wav",
        )
        for index in range(260)
    ]
    rows.append(
        payload(
            id="old-needle",
            created_at="2020-01-01T00:00:00+00:00",
            source_name="needle.wav",
        )
    )
    insert_payloads(store, *rows)

    stats = store.statistics(now=NOW)
    assert stats.total_records == 261
    assert stats.completed_records == 261

    found = store.list(filters=HistoryFilter(text="needle"), limit=10)
    assert [item.id for item in found] == ["old-needle"]


def test_legacy_payload_without_optional_fields_is_valid(tmp_path):
    store = LocalStore(tmp_path)
    insert_payloads(store, legacy_payload())

    stats = store.statistics(now=NOW)
    assert stats.total_records == 1
    assert stats.invalid_records == 0
    assert stats.completed_records == 1
    assert stats.engine_counts == (("faster-whisper", 1),)
    assert stats.audio_seconds_total == 0.0
    assert stats.weighted_real_time_factor is None
    assert stats.word_count_total == 2


def test_invalid_payloads_count_only_in_total_and_never_raise(tmp_path):
    store = LocalStore(tmp_path)
    insert_payloads(
        store,
        payload(id="good"),
        "not json",
        {"id": "incomplete", "created_at": "2026-08-30T09:00:00+00:00"},
    )

    stats = store.statistics(now=NOW)
    assert stats.total_records == 3
    assert stats.invalid_records == 2
    assert stats.completed_records == 1
    assert stats.language_counts == (("uk", 1),)

    listed = store.list(filters=HistoryFilter(), limit=50)
    assert [item.id for item in listed] == ["good"]


def test_exact_seven_and_thirty_day_boundaries_are_inclusive():
    stats = statistics_of(
        payload(id="at-7d", created_at=(NOW - timedelta(days=7)).isoformat()),
        payload(
            id="just-outside-7d",
            created_at=(NOW - timedelta(days=7, seconds=1)).isoformat(),
        ),
        payload(id="at-30d", created_at=(NOW - timedelta(days=30)).isoformat()),
        payload(
            id="just-outside-30d",
            created_at=(NOW - timedelta(days=30, seconds=1)).isoformat(),
        ),
        payload(id="unparseable", created_at="not-a-date"),
    )

    assert stats.total_records == 5
    assert stats.invalid_records == 0
    assert stats.records_last_7_days == 1
    assert stats.records_last_30_days == 3


def test_naive_created_at_and_now_are_treated_as_utc():
    stats = statistics_of(
        payload(id="naive", created_at="2026-08-30T12:00:00"),
        now=datetime(2026, 9, 1, 12, 0, 0),
    )

    assert stats.records_last_7_days == 1
    assert stats.records_last_30_days == 1


def test_unicode_word_counting():
    stats = statistics_of(
        payload(id="a", corrected_text="п'ять шість-сім вісім"),
        payload(id="b", corrected_text="mother-in-law п’ять пʼять 2026 a_b"),
        payload(id="c", corrected_text="   ---   _ _ "),
    )

    assert stats.word_count_total == 9


def test_failed_records_are_not_completed():
    stats = statistics_of(
        payload(id="ok", status="completed"),
        payload(id="bad", status="failed"),
        payload(id="other", status="running"),
    )

    assert stats.total_records == 3
    assert stats.completed_records == 1
    assert stats.failed_records == 1


def test_weighted_real_time_factor_and_speed_multiplier():
    stats = statistics_of(
        payload(id="a", audio_seconds=10, real_time_factor=0.5),
        payload(id="b", audio_seconds=30, real_time_factor=1.0),
        payload(id="none", audio_seconds=5, real_time_factor=None),
        payload(id="zero", audio_seconds=5, real_time_factor=0),
        payload(id="negative", audio_seconds=5, real_time_factor=-1.0),
        payload(id="nan", audio_seconds=5, real_time_factor=float("nan")),
        payload(id="no-audio", audio_seconds=0, real_time_factor=0.5),
        payload(id="bool", audio_seconds=True, real_time_factor=True),
    )

    assert stats.audio_seconds_total == pytest.approx(60.0)
    assert stats.weighted_real_time_factor == pytest.approx(0.875)
    assert stats.speed_multiplier == pytest.approx(1 / 0.875)


def test_weighted_real_time_factor_is_none_without_qualifying_rows():
    stats = statistics_of(payload(id="a", audio_seconds=0, real_time_factor=0.5))

    assert stats.weighted_real_time_factor is None
    assert stats.speed_multiplier is None


def test_counts_are_sorted_by_count_then_key():
    stats = statistics_of(
        payload(id="1", language="uk", engine="ollama", model="b"),
        payload(id="2", language="cs", engine="ollama", model="a"),
        payload(id="3", language="en", engine="faster-whisper", model="a"),
    )

    assert stats.language_counts == (("cs", 1), ("en", 1), ("uk", 1))
    assert stats.engine_counts == (("ollama", 2), ("faster-whisper", 1))
    assert stats.model_counts == (("a", 2), ("b", 1))


def test_retained_audio_records_require_true():
    stats = statistics_of(
        payload(id="kept", audio_retained=True),
        payload(id="dropped", audio_retained=False),
    )

    assert stats.retained_audio_records == 1


def test_filter_matches_text_case_insensitively_across_fields():
    item = transcript_of(
        source_name="Ranni Poznamka.wav", raw_text="RAW body", corrected_text="Опис"
    )

    assert HistoryFilter(text="ranni").matches(item)
    assert HistoryFilter(text="raw BODY").matches(item)
    assert HistoryFilter(text="опис").matches(item)
    assert not HistoryFilter(text="missing").matches(item)
    assert HistoryFilter().matches(item)


def test_filter_matches_exact_equality_fields():
    item = transcript_of(language="uk", engine="ollama", model="small", status="failed")

    assert HistoryFilter(language="uk").matches(item)
    assert not HistoryFilter(language="cs").matches(item)
    assert HistoryFilter(engine="ollama").matches(item)
    assert not HistoryFilter(engine="faster-whisper").matches(item)
    assert HistoryFilter(model="small").matches(item)
    assert not HistoryFilter(model="tiny").matches(item)
    assert HistoryFilter(status="failed").matches(item)
    assert not HistoryFilter(status="completed").matches(item)


def test_filter_matches_retained_audio():
    kept = transcript_of(audio_retained=True)
    dropped = transcript_of(audio_retained=False)

    assert HistoryFilter(retained_audio=True).matches(kept)
    assert not HistoryFilter(retained_audio=True).matches(dropped)
    assert HistoryFilter(retained_audio=False).matches(dropped)
    assert HistoryFilter(retained_audio=None).matches(dropped)


def test_filter_date_bounds_are_inclusive_and_utc_aware():
    item = transcript_of(created_at="2026-08-30T12:00:00+00:00")
    moment = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)

    assert HistoryFilter(created_from=moment).matches(item)
    assert HistoryFilter(created_to=moment).matches(item)
    assert HistoryFilter(created_from=moment.replace(tzinfo=None)).matches(item)
    assert HistoryFilter(created_to=moment.replace(tzinfo=None)).matches(item)
    assert not HistoryFilter(created_from=moment + timedelta(seconds=1)).matches(item)
    assert not HistoryFilter(created_to=moment - timedelta(seconds=1)).matches(item)


def test_filter_naive_record_timestamp_is_treated_as_utc():
    item = transcript_of(created_at="2026-08-30T12:00:00")
    moment = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC)

    assert HistoryFilter(created_from=moment, created_to=moment).matches(item)


def test_filter_unparseable_timestamp_fails_only_active_date_bounds():
    item = transcript_of(created_at="whenever", language="uk")

    assert HistoryFilter(language="uk").matches(item)
    assert not HistoryFilter(created_from=datetime(2020, 1, 1, tzinfo=UTC)).matches(item)
    assert not HistoryFilter(created_to=datetime(2030, 1, 1, tzinfo=UTC)).matches(item)


def test_filter_combination_requires_every_condition():
    item = transcript_of(
        source_name="meeting.wav",
        language="uk",
        status="completed",
        created_at="2026-08-30T12:00:00+00:00",
        audio_retained=True,
    )
    combined = HistoryFilter(
        text="meeting",
        language="uk",
        status="completed",
        created_from=datetime(2026, 8, 1, tzinfo=UTC),
        created_to=datetime(2026, 9, 1, tzinfo=UTC),
        retained_audio=True,
    )

    assert combined.matches(item)
    assert not combined.matches(transcript_of(source_name="other.wav", language="uk"))


def test_filtered_list_orders_newest_first_and_applies_limit_after_filtering(tmp_path):
    store = LocalStore(tmp_path)
    insert_payloads(
        store,
        payload(id="a", created_at="2026-08-01T00:00:00+00:00", language="uk"),
        payload(id="b", created_at="2026-08-02T00:00:00+00:00", language="cs"),
        payload(id="c", created_at="2026-08-03T00:00:00+00:00", language="uk"),
        payload(id="d", created_at="2026-08-04T00:00:00+00:00", language="uk"),
    )

    assert [item.id for item in store.list(filters=HistoryFilter(language="uk"))] == [
        "d",
        "c",
        "a",
    ]
    assert [
        item.id for item in store.list(filters=HistoryFilter(language="uk"), limit=2)
    ] == ["d", "c"]


def test_filtered_list_combines_column_and_python_filters(tmp_path):
    store = LocalStore(tmp_path)
    insert_payloads(
        store,
        payload(
            id="match",
            created_at="2026-08-15T00:00:00+00:00",
            source_name="quarterly review.wav",
            language="uk",
            status="completed",
            audio_retained=True,
        ),
        payload(
            id="wrong-date",
            created_at="2026-07-15T00:00:00+00:00",
            source_name="quarterly review.wav",
            language="uk",
            status="completed",
            audio_retained=True,
        ),
        payload(
            id="wrong-audio",
            created_at="2026-08-16T00:00:00+00:00",
            source_name="quarterly review.wav",
            language="uk",
            status="completed",
            audio_retained=False,
        ),
        payload(
            id="wrong-status",
            created_at="2026-08-17T00:00:00+00:00",
            source_name="quarterly review.wav",
            language="uk",
            status="failed",
            audio_retained=True,
        ),
    )

    found = store.list(
        filters=HistoryFilter(
            text="QUARTERLY",
            language="uk",
            status="completed",
            created_from=datetime(2026, 8, 1, tzinfo=UTC),
            created_to=datetime(2026, 8, 15, 12, tzinfo=UTC),
            retained_audio=True,
        )
    )

    assert [item.id for item in found] == ["match"]


def test_filtered_list_rejects_legacy_query_and_non_positive_limit(tmp_path):
    store = LocalStore(tmp_path)

    with pytest.raises(ValueError):
        store.list("text", filters=HistoryFilter(language="uk"))
    with pytest.raises(ValueError):
        store.list(filters=HistoryFilter(), limit=0)
    with pytest.raises(ValueError):
        store.list(limit=0)


def test_daily_activity_returns_days_oldest_first_with_today_last():
    activity = daily_activity(
        (json.dumps(payload(id="a", created_at=NOW.isoformat())),), now=NOW, days=3
    )

    assert [day for day, _count in activity] == [
        "2026-08-30",
        "2026-08-31",
        "2026-09-01",
    ]
    assert activity[-1] == ("2026-09-01", 1)
    assert activity[0] == ("2026-08-30", 0)


def test_daily_activity_counts_per_day_and_ignores_dates_outside_the_window():
    activity = daily_activity(
        (
            json.dumps(payload(id="in-1", created_at=NOW.isoformat())),
            json.dumps(payload(id="in-2", created_at=NOW.isoformat())),
            json.dumps(
                payload(
                    id="yesterday",
                    created_at=(NOW - timedelta(days=1)).isoformat(),
                )
            ),
            json.dumps(
                payload(
                    id="too-old",
                    created_at=(NOW - timedelta(days=30)).isoformat(),
                )
            ),
        ),
        now=NOW,
        days=14,
    )

    by_day = dict(activity)
    assert len(activity) == 14
    assert by_day["2026-09-01"] == 2
    assert by_day["2026-08-31"] == 1
    assert sum(by_day.values()) == 3


def test_daily_activity_tolerates_bad_rows_without_raising():
    activity = daily_activity(
        (
            "not json",
            json.dumps({"id": "incomplete"}),
            json.dumps(payload(id="unparseable-date", created_at="not-a-date")),
            json.dumps(payload(id="good", created_at=NOW.isoformat())),
        ),
        now=NOW,
        days=7,
    )

    assert dict(activity)["2026-09-01"] == 1
    assert sum(count for _day, count in activity) == 1


def test_daily_activity_treats_naive_timestamps_as_utc():
    activity = daily_activity(
        (json.dumps(payload(id="naive", created_at="2026-09-01T12:00:00")),),
        now=NOW,
        days=1,
    )

    assert activity == (("2026-09-01", 1),)


def test_daily_activity_zero_or_negative_days_is_empty():
    assert daily_activity((), now=NOW, days=0) == ()
    assert daily_activity((), now=NOW, days=-1) == ()


def test_store_daily_activity_reads_every_stored_record(tmp_path):
    store = LocalStore(tmp_path)
    insert_payloads(
        store,
        payload(id="today", created_at=NOW.isoformat()),
        payload(id="yesterday", created_at=(NOW - timedelta(days=1)).isoformat()),
    )

    activity = store.daily_activity(now=NOW, days=3)

    assert [day for day, _count in activity] == [
        "2026-08-30",
        "2026-08-31",
        "2026-09-01",
    ]
    by_day = dict(activity)
    assert by_day["2026-09-01"] == 1
    assert by_day["2026-08-31"] == 1


def test_unfiltered_list_keeps_legacy_behaviour(tmp_path):
    store = LocalStore(tmp_path)
    insert_payloads(
        store,
        payload(id="a", created_at="2026-08-01T00:00:00+00:00", raw_text="alpha"),
        payload(id="b", created_at="2026-08-02T00:00:00+00:00", raw_text="beta"),
    )

    assert [item.id for item in store.list()] == ["b", "a"]
    assert [item.id for item in store.list("alpha")] == ["a"]
    assert [item.id for item in store.list(limit=1)] == ["b"]
