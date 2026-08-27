from voice_studio import cli

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
