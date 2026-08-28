from voice_studio.diagnostics import diagnostics, module_available
from voice_studio.models import Settings


def test_module_available_performs_a_real_import(monkeypatch):
    def fail_import(_name):
        raise ModuleNotFoundError("synthetic missing native module")

    monkeypatch.setattr("voice_studio.diagnostics.importlib.import_module", fail_import)
    assert not module_available("tkinter")


def test_diagnostics_exposes_capabilities(monkeypatch):
    monkeypatch.setattr(
        "voice_studio.diagnostics._microphone_status",
        lambda _available: (True, None),
    )
    monkeypatch.setattr(
        "voice_studio.diagnostics._hotkey_status",
        lambda _combination, _available: (True, None),
    )
    result = diagnostics(
        Settings(
            profile="whisper-local",
            engine="faster-whisper",
            model="/missing/local/model",
            automatic_cleanup=False,
        )
    )
    assert set(result["capabilities"]) == {
        "gui_ready",
        "microphone_ready",
        "hotkey_ready",
        "ffmpeg_ready",
        "model_ready",
    }
    assert result["capabilities"]["model_ready"] is False


def test_diagnostics_checks_the_selected_local_ollama_audio_model(monkeypatch):
    monkeypatch.setattr(
        "voice_studio.diagnostics._ollama_model_status",
        lambda model: (model == "gemma4:12b", None),
    )

    result = diagnostics(Settings(ollama_model="gemma4:12b"))

    assert result["selected_engine"] == "ollama"
    assert result["selected_model"] == "gemma4:12b"
    assert result["capabilities"]["model_ready"] is True
