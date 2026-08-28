from voice_studio import cli
from voice_studio.models import Settings

main = cli.main


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
