import json

from voice_studio import cli
from voice_studio.cloud_cleanup import CleanupProposal

main = cli.main


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
    _cleanup_profile(tmp_path, monkeypatch, cleanup_provider="openai")
    monkeypatch.setattr(cli, "propose_cleanup", _forbidden_cleanup)

    assert main(["cleanup", "any-transcript"]) == 2
    assert "--allow-cloud-text" in capsys.readouterr().err


def test_openai_cleanup_is_blocked_by_offline_only_even_with_consent(
    tmp_path, capsys, monkeypatch
):
    _cleanup_profile(tmp_path, monkeypatch, cleanup_provider="openai", offline_only=True)
    monkeypatch.setattr(cli, "propose_cleanup", _forbidden_cleanup)

    assert main(["cleanup", "any-transcript", "--allow-cloud-text"]) == 2
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
