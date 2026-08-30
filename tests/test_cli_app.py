import json
from pathlib import Path

from voice_studio import backup as backup_module
from voice_studio import cli
from voice_studio.cloud_cleanup import CleanupProposal
from voice_studio.models import Settings, Transcript
from voice_studio.storage import LocalStore

main = cli.main


def _write_model(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "model.bin").write_bytes(b"fixture model")
    (path / "config.json").write_text('{"model_type":"Whisper"}', encoding="utf-8")
    return path


def _storage_profile(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))
    return LocalStore(data)


def _stored_missing_cli_source(tmp_path, monkeypatch):
    store = _storage_profile(tmp_path, monkeypatch)
    original = tmp_path / "recording.wav"
    original.write_bytes(b"user-owned-original")
    managed, digest = store.import_source(original)
    item = Transcript(
        id="12345678-1234-5678-1234-567812345678",
        created_at="2026-08-30T00:00:00+00:00",
        source_name=original.name,
        source_sha256=digest,
        language="uk",
        engine="faster-whisper",
        model="small",
        raw_text="raw transcript",
        corrected_text="corrected transcript",
        source_path=str(managed),
    )
    store.save(item)
    managed.unlink()
    return store, item, original, managed


def _recursive_snapshot(root: Path):
    snapshot = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        snapshot[path.relative_to(root).as_posix()] = (
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
    return snapshot


def test_storage_audit_read_only_cli_refuses_missing_root_without_bootstrap(
    tmp_path, capsys, monkeypatch
):
    data = tmp_path / "missing-data"
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "missing-config"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "missing-cache"))

    assert main(["storage", "audit"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "storage root does not exist" in captured.err
    assert not data.exists()
    assert not (tmp_path / "missing-config").exists()
    assert not (tmp_path / "missing-cache").exists()


def test_storage_audit_read_only_cli_refuses_missing_database_without_bootstrap(
    tmp_path, capsys, monkeypatch
):
    data = tmp_path / "data-without-db"
    data.mkdir()
    marker = data / "marker.txt"
    marker.write_bytes(b"preserve")
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "missing-config"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "missing-cache"))
    before = _recursive_snapshot(tmp_path)

    assert main(["storage", "audit"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "storage database does not exist" in captured.err
    assert _recursive_snapshot(tmp_path) == before


def test_storage_audit_read_only_cli_preserves_store_and_restore_journal(
    tmp_path, capsys, monkeypatch
):
    store = _storage_profile(tmp_path, monkeypatch)
    staging = tmp_path / ".data.restore-staging"
    staging.mkdir()
    (staging / "marker.txt").write_bytes(b"unfinished restore")
    backup_module._write_json_atomic(
        backup_module.restore_journal_path(store.root),
        {
            "journal_version": backup_module.RESTORE_JOURNAL_VERSION,
            "backup_version": backup_module.BACKUP_VERSION,
            "created_at": "2026-08-30T00:00:00+00:00",
            "data_root": str(store.root.resolve()),
            "staging_path": str(staging.resolve()),
            "recovery_path": None,
            "expected_records": 0,
            "settings_target": None,
            "settings_payload_written": True,
            "stage": "swap_started",
        },
    )
    before = _recursive_snapshot(tmp_path)

    assert main(["storage", "audit"]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out)["records"] == 0
    assert "restore-journal:" not in captured.err
    assert _recursive_snapshot(tmp_path) == before


def test_storage_audit_drift_outputs_additive_sections(tmp_path, capsys, monkeypatch):
    store, item, _original, managed = _stored_missing_cli_source(tmp_path, monkeypatch)
    (store.models / "catalog.json").write_text(
        '{"version": 1, "models": [{"id": "tiny", "path": "tiny"}]}',
        encoding="utf-8",
    )
    stale_export = "87654321-4321-8765-4321-876543218765.srt"
    (store.exports / stale_export).write_text("stale", encoding="utf-8")
    orphan = store.sources / "orphan.wav"
    orphan.write_bytes(b"managed orphan")

    assert main(["storage", "audit"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["missing_records"] == [
        {"id": item.id, "path": str(managed.resolve())}
    ]
    assert payload["model_catalog"]["missing"] == [
        {
            "id": "tiny",
            "path": str(store.models / "tiny"),
            "reason": "catalogued model is absent",
        }
    ]
    assert payload["exports"]["canonical_stale"] == [stale_export]
    assert orphan.read_bytes() == b"managed orphan"


def test_storage_repair_missing_cli_requires_confirmation(tmp_path, capsys, monkeypatch):
    store, item, original, managed = _stored_missing_cli_source(tmp_path, monkeypatch)

    assert main(["storage", "repair-missing", item.id]) == 2

    assert "missing-source repair requires --yes" in capsys.readouterr().err
    assert store.get(item.id).source_path == str(managed)
    assert original.read_bytes() == b"user-owned-original"


def test_storage_repair_missing_cli_reports_unknown_id_without_mutation(
    tmp_path, capsys, monkeypatch
):
    store, item, original, managed = _stored_missing_cli_source(tmp_path, monkeypatch)

    assert main(["storage", "repair-missing", "unknown", "--yes"]) == 3

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "transcript not found: unknown"
    persisted = store.get(item.id)
    assert persisted.source_path == str(managed)
    assert persisted.audio_retained is True
    assert original.read_bytes() == b"user-owned-original"


def test_storage_repair_missing_cli_refuses_wrong_expected_path_without_mutation(
    tmp_path, capsys, monkeypatch
):
    store, item, original, managed = _stored_missing_cli_source(tmp_path, monkeypatch)

    assert (
        main(
            [
                "storage",
                "repair-missing",
                item.id,
                "--expected-path",
                str(tmp_path / "other.wav"),
                "--yes",
            ]
        )
        == 2
    )

    assert "expected path does not match" in capsys.readouterr().err
    persisted = store.get(item.id)
    assert persisted.source_path == str(managed)
    assert persisted.audio_retained is True
    assert original.read_bytes() == b"user-owned-original"


def test_storage_repair_missing_cli_detaches_exact_missing_source(
    tmp_path, capsys, monkeypatch
):
    store, item, original, managed = _stored_missing_cli_source(tmp_path, monkeypatch)

    assert (
        main(
            [
                "storage",
                "repair-missing",
                item.id,
                "--expected-path",
                str(managed),
                "--yes",
            ]
        )
        == 0
    )

    assert json.loads(capsys.readouterr().out) == {
        "repaired": True,
        "id": item.id,
        "path": str(managed),
        "action": "detached_missing_source",
    }
    repaired = store.get(item.id)
    assert repaired.source_path is None
    assert repaired.audio_retained is False
    assert repaired.raw_text == "raw transcript"
    assert repaired.corrected_text == "corrected transcript"
    assert original.read_bytes() == b"user-owned-original"


def test_models_reconcile_outputs_json_and_models_list_reports_repair(
    tmp_path, capsys, monkeypatch
):
    data = tmp_path / "data"
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))
    _write_model(data / "models" / "orphan")

    assert main(["models", "reconcile"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["adopted"] == ["orphan"]


def test_model_catalog_hook_is_limited_to_models_commands(tmp_path, capsys, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))
    _write_model(data / "models" / "orphan")

    assert main(["history"]) == 0
    assert "model-catalog:" not in capsys.readouterr().err
    assert main(["models", "list"]) == 0
    assert "model-catalog:" in capsys.readouterr().err


def test_models_reconcile_reports_failure_and_reuses_single_result(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))
    reconciliation = {
        "status": "FAIL",
        "action": "attention",
        "adopted": [],
        "dropped": [],
        "blocked": [],
        "staging_removed": [],
        "staging_kept": [],
        "residue_removed": [],
        "catalog_quarantined": None,
        "error": "cannot scan model catalog",
    }
    calls = 0

    class FailingCatalog:
        def __init__(self, _root):
            pass

        def reconcile(self):
            nonlocal calls
            calls += 1
            return reconciliation

    monkeypatch.setattr(cli, "ModelCatalog", FailingCatalog)

    assert main(["models", "reconcile"]) == 2

    captured = capsys.readouterr()
    assert json.loads(captured.out) == reconciliation
    assert captured.err.startswith("model-catalog:")
    assert json.loads(captured.err.removeprefix("model-catalog:")) == reconciliation
    assert calls == 1


def test_transcribe_cli_selects_local_ollama_profile(monkeypatch):
    monkeypatch.setattr(cli, "load_settings", Settings)
    args = cli.build_parser().parse_args(
        [
            "transcribe",
            "recording.wav",
            "--engine",
            "ollama",
            "--ollama-model",
            "gemma4:12b",
        ]
    )

    settings = cli._load_effective_settings(args)

    assert settings.profile == "ollama-local"
    assert settings.engine == "ollama"
    assert settings.ollama_model == "gemma4:12b"
    assert settings.cleanup_provider == "ollama"
    assert settings.automatic_cleanup is True
    assert settings.offline_only is True


def test_transcribe_cli_engine_override_applies_matching_profile(monkeypatch):
    monkeypatch.setattr(cli, "load_settings", Settings)
    whisper_args = cli.build_parser().parse_args(
        ["transcribe", "recording.wav", "--engine", "faster-whisper", "--model", "tiny"]
    )
    cloud_args = cli.build_parser().parse_args(
        ["transcribe", "recording.wav", "--engine", "openai-cloud"]
    )

    whisper = cli._load_effective_settings(whisper_args)
    cloud = cli._load_effective_settings(cloud_args)

    assert whisper.profile == "whisper-local"
    assert whisper.model == "tiny"
    assert whisper.automatic_cleanup is False
    assert cloud.profile == "openai-cloud"
    assert cloud.offline_only is False


def test_validate_cli(tmp_path, capsys, make_wav):
    source = make_wav(tmp_path / "sample.wav")
    assert main(["validate", str(source)]) == 0
    assert '"valid": true' in capsys.readouterr().out


def test_validate_cli_rejects_fake_wav(tmp_path, capsys):
    source = tmp_path / "fake.wav"
    source.write_bytes(b"not audio")
    assert main(["validate", str(source)]) == 2
    assert "cannot decode media file" in capsys.readouterr().err


def test_transcribe_keyboard_interrupt_returns_130_without_traceback(
    tmp_path, capsys, make_wav, monkeypatch
):
    source = make_wav(tmp_path / "sample.wav")
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))

    class InterruptedController:
        def __init__(self, *_args, **_kwargs):
            pass

        def run(self, *_args, **_kwargs):
            raise KeyboardInterrupt

        def close(self):
            pass

    monkeypatch.setattr(cli, "TranscriptionJobController", InterruptedController)

    assert main(["transcribe", str(source)]) == 130
    captured = capsys.readouterr()
    assert captured.err.strip() == "cancelled"
    assert "Traceback" not in captured.err


def _cleanup_profile(tmp_path, monkeypatch, **settings_values):
    """Point the CLI at a disposable profile carrying the given settings."""

    config = tmp_path / "config"
    config.mkdir(parents=True, exist_ok=True)
    (config / "settings.json").write_text(
        json.dumps(settings_values), encoding="utf-8"
    )
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(config))
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))


def _forbidden_cleanup(*_args, **_kwargs):
    raise AssertionError("propose_cleanup must not run before the cloud-text gate passes")


def test_openai_cleanup_refuses_without_explicit_cloud_text_consent(
    tmp_path, capsys, monkeypatch
):
    _cleanup_profile(
        tmp_path,
        monkeypatch,
        profile="openai-cloud",
        engine="openai-cloud",
        cleanup_provider="openai",
        automatic_cleanup=False,
        offline_only=False,
    )
    monkeypatch.setattr(cli, "propose_cleanup", _forbidden_cleanup)

    assert main(["cleanup", "any-transcript"]) == 2
    assert "--allow-cloud-text" in capsys.readouterr().err


def test_openai_cleanup_is_blocked_by_offline_only_even_with_consent(
    tmp_path, capsys, monkeypatch
):
    _cleanup_profile(tmp_path, monkeypatch, cleanup_provider="ollama", offline_only=True)
    monkeypatch.setattr(cli, "propose_cleanup", _forbidden_cleanup)

    assert (
        main(
            [
                "cleanup",
                "any-transcript",
                "--provider",
                "openai",
                "--allow-cloud-text",
            ]
        )
        == 2
    )
    assert "offline_only blocks AI cleanup" in capsys.readouterr().err


def test_openai_provider_flag_still_requires_consent_over_local_settings(
    tmp_path, capsys, monkeypatch
):
    _cleanup_profile(tmp_path, monkeypatch, cleanup_provider="ollama")
    monkeypatch.setattr(cli, "propose_cleanup", _forbidden_cleanup)
    monkeypatch.setattr(
        cli,
        "list_ollama_models",
        lambda: (_ for _ in ()).throw(AssertionError("local models must not be listed")),
    )

    assert main(["cleanup", "any-transcript", "--provider", "openai"]) == 2
    assert "--allow-cloud-text" in capsys.readouterr().err


def test_local_ollama_cleanup_needs_no_cloud_consent_and_survives_offline_only(
    tmp_path, capsys, monkeypatch
):
    _cleanup_profile(tmp_path, monkeypatch, cleanup_provider="ollama", offline_only=True)
    reached: dict[str, str] = {}

    def fake_propose(_transcript, *, provider, model, **_kwargs):
        reached["provider"] = provider
        reached["model"] = model
        return CleanupProposal(corrected_text="ok", segments=[], changes=[])

    class SingleTranscriptStore:
        def __init__(self, *_args, **_kwargs):
            pass

        def get(self, _transcript_id):
            return object()

    monkeypatch.setattr(cli, "list_ollama_models", lambda: ["gemma4-code:latest"])
    monkeypatch.setattr(cli, "propose_cleanup", fake_propose)
    monkeypatch.setattr(cli, "LocalStore", SingleTranscriptStore)

    assert main(["cleanup", "any-transcript"]) == 0
    assert reached == {"provider": "ollama", "model": "gemma4-code:latest"}
    assert "corrected_text" in capsys.readouterr().out


def test_cli_settles_an_interrupted_restore_before_opening_the_store(
    tmp_path, capsys, monkeypatch
):
    """A killed restore must be settled and reported, never silently ignored."""

    data = tmp_path / "data"
    staging = tmp_path / f".{data.name}.restore-abcdef"
    LocalStore(staging).save(
        Transcript(
            id="restored",
            created_at="2026-08-28T00:00:00+00:00",
            source_name="a.wav",
            source_sha256="a" * 64,
            language="uk",
            engine="fixture",
            model="fixture",
            raw_text="raw",
            corrected_text="corrected",
        )
    )
    backup_module._write_json_atomic(
        backup_module.restore_journal_path(data),
        {
            "journal_version": backup_module.RESTORE_JOURNAL_VERSION,
            "backup_version": backup_module.BACKUP_VERSION,
            "created_at": "2026-08-28T00:00:00+00:00",
            "data_root": str(data.resolve()),
            "staging_path": str(staging.resolve()),
            "recovery_path": None,
            "expected_records": 1,
            "settings_target": None,
            "settings_payload_written": True,
            "stage": "swap_started",
        },
    )
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(data))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))

    assert main(["history"]) == 0

    captured = capsys.readouterr()
    assert "restore-journal:" in captured.err
    assert '"action": "completed"' in captured.err
    assert "restored" in captured.out
    assert not backup_module.restore_journal_path(data).exists()


def test_cli_reports_a_clean_start_without_noise(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("VOICE_STUDIO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VOICE_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("VOICE_STUDIO_CACHE_DIR", str(tmp_path / "cache"))

    assert main(["history"]) == 0

    assert "restore-journal:" not in capsys.readouterr().err
