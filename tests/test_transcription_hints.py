import inspect
import sys
from types import SimpleNamespace

import pytest

from voice_studio.dictionary import DictionaryRule, TerminologyDictionary
from voice_studio.engines.base import EngineResult, SpeechEngine, TranscriptionHints
from voice_studio.engines.faster_whisper import FasterWhisperEngine
from voice_studio.engines.ollama_audio import OllamaAudioEngine
from voice_studio.engines.openai_cloud import OpenAICloudEngine
from voice_studio.jobs import TranscriptionJobController, _engine_worker
from voice_studio.models import Segment, Settings
from voice_studio.service import TranscriptionService
from voice_studio.storage import LocalStore


def _assert_provider_payload_omits_hints(value, marker):
    assert not isinstance(value, TranscriptionHints)
    if isinstance(value, str):
        assert marker not in value
    elif isinstance(value, bytes):
        assert marker.encode("utf-8") not in value
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_provider_payload_omits_hints(key, marker)
            _assert_provider_payload_omits_hints(item, marker)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_provider_payload_omits_hints(item, marker)


def test_hints_are_immutable_and_validate_public_limits_without_terms_in_errors():
    hints = TranscriptionHints(("VOICE", "Codex"))
    assert hints.terms == ("VOICE", "Codex")
    assert TranscriptionHints(("x",) * 256).terms == ("x",) * 256
    assert TranscriptionHints(("x" * 8192,)).terms == ("x" * 8192,)
    with pytest.raises(ValueError) as error:
        TranscriptionHints(("secret-term",) * 257)
    assert "256" in str(error.value)
    assert "secret-term" not in str(error.value)
    with pytest.raises(ValueError) as error:
        TranscriptionHints(("hidden-term" + "x" * 8192,))
    assert "8192" in str(error.value)
    assert "hidden-term" not in str(error.value)


def test_all_engine_transcribe_methods_keep_keyword_only_hints_contract():
    for engine in (SpeechEngine, FasterWhisperEngine, OllamaAudioEngine, OpenAICloudEngine):
        parameter = inspect.signature(engine.transcribe).parameters["hints"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is None


def test_faster_whisper_passes_nonempty_hints_as_hotwords_and_reuses_model(monkeypatch, tmp_path):
    calls = []
    constructed = []
    monkeypatch.setitem(
        sys.modules,
        "ctranslate2",
        SimpleNamespace(
            get_cuda_device_count=lambda: 0,
            get_supported_compute_types=lambda _device=None: ("int8",),
        ),
    )

    def transcribe(*args, **kwargs):
        calls.append(kwargs)
        return (
            iter([SimpleNamespace(start=0, end=1, text="raw", avg_logprob=None)]),
            SimpleNamespace(language="uk", duration=1),
        )

    model = SimpleNamespace(transcribe=transcribe)

    def construct(*_args, **_kwargs):
        constructed.append(True)
        return model

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=construct),
    )
    engine = FasterWhisperEngine("tiny")
    engine.transcribe(tmp_path / "input.wav", "uk", hints=TranscriptionHints(("VOICE", "Codex")))
    engine.transcribe(tmp_path / "input.wav", "uk", hints=TranscriptionHints(("Changed",)))
    engine.transcribe(tmp_path / "input.wav", "uk")
    assert calls[0]["hotwords"] == "VOICE, Codex"
    assert calls[1]["hotwords"] == "Changed"
    assert "hotwords" not in calls[2]
    assert engine._model is model
    assert constructed == [True]


def test_non_whisper_engines_ignore_hints(tmp_path):
    marker = "PRIVATE_HINT_7D2"
    ollama_calls = []

    class OllamaClient:
        def show_model(self, *args, **kwargs):
            ollama_calls.append((args, kwargs))
            return {"capabilities": ["audio"]}

        def audio_chat(self, *args, **kwargs):
            ollama_calls.append((args, kwargs))
            return "raw"

    ollama = OllamaAudioEngine(
        "model",
        client=OllamaClient(),
        converter=lambda _source: (b"wav", 1),
    )
    assert (
        ollama.transcribe(
            tmp_path / "input.wav", "uk", hints=TranscriptionHints((marker,))
        ).text
        == "raw"
    )
    assert len(ollama_calls) == 2
    assert ollama_calls[0] == (("model",), {})
    audio_args, audio_kwargs = ollama_calls[1]
    assert audio_args[:2] == ("model", b"wav")
    assert len(audio_args) == 3
    assert audio_kwargs == {}
    _assert_provider_payload_omits_hints(ollama_calls, marker)

    cloud_calls = []

    class Transcriptions:
        def create(self, *args, **kwargs):
            cloud_calls.append((args, kwargs))
            return {"text": "raw"}

    cloud = OpenAICloudEngine(
        "model",
        client=SimpleNamespace(audio=SimpleNamespace(transcriptions=Transcriptions())),
    )
    source = tmp_path / "input.wav"
    source.write_bytes(b"wav")
    assert cloud.transcribe(source, "uk", hints=TranscriptionHints((marker,))).text == "raw"
    assert len(cloud_calls) == 1
    cloud_args, cloud_kwargs = cloud_calls[0]
    assert cloud_args == ()
    assert set(cloud_kwargs) == {"file", "language", "model"}
    assert cloud_kwargs["model"] == "model"
    assert cloud_kwargs["language"] == "uk"
    assert cloud_kwargs["file"].closed is True
    _assert_provider_payload_omits_hints(cloud_calls, marker)


def test_service_passes_bounded_dictionary_hints_and_preserves_raw_text(
    tmp_path, make_wav, monkeypatch
):
    observed = []

    class Engine:
        name = "fake"
        model_name = "fake"

        def transcribe(self, source, language, *, hints=None):
            observed.append(hints)
            return EngineResult("fake", "fake", "uk", [Segment(0, 1, "voice")])

    dictionary = TerminologyDictionary([DictionaryRule("voice", "VOICE")])
    monkeypatch.setattr("voice_studio.service.validate_media_file", lambda *_a, **_k: None)
    transcript = TranscriptionService(LocalStore(tmp_path / "data"), Engine(), dictionary).run(
        make_wav(tmp_path / "input.wav"), "uk", "keep"
    )
    assert observed == [TranscriptionHints(("VOICE",))]
    assert transcript.raw_text == "voice"
    assert transcript.corrected_text == "VOICE"
    assert "VOICE" not in repr(transcript.metadata)


def test_worker_reconstructs_bounded_hints_without_dictionary_path(monkeypatch, tmp_path):
    received = []

    class Requests:
        def __init__(self):
            self.items = iter(
                (
                    {
                        "job_id": "job",
                        "settings": {},
                        "source": str(tmp_path / "input.wav"),
                        "language": "uk",
                        "hints": ["VOICE"],
                    },
                    None,
                )
            )

        def get(self):
            return next(self.items)

    class Results:
        def put(self, value): received.append(value)

    class Engine:
        def transcribe(self, source, language, *, hints=None):
            assert hints == TranscriptionHints(("VOICE",))
            return EngineResult("fake", "fake", "uk", [Segment(0, 1, "raw")])

    class Manager:
        def __init__(self, *_args): pass
        def get(self, _settings): return Engine()

    import voice_studio.engines as engines
    monkeypatch.setattr(engines, "EngineManager", Manager)
    _engine_worker(Requests(), Results(), str(tmp_path / "cache"), str(tmp_path / "models"))
    assert received[0]["ok"] is True
    assert "VOICE" not in repr(received[0]["result"].metadata)


@pytest.mark.parametrize(
    "hints",
    ["PRIVATE_HINT_7D2", [1], ["x"] * 257, ["x" * 8193]],
)
def test_worker_rejects_invalid_hint_payload_without_engine_access(monkeypatch, tmp_path, hints):
    received = []
    manager_calls = []

    class Requests:
        def __init__(self):
            self.items = iter(
                (
                    {
                        "job_id": "job",
                        "settings": {},
                        "source": str(tmp_path / "input.wav"),
                        "language": "uk",
                        "hints": hints,
                    },
                    None,
                )
            )

        def get(self):
            return next(self.items)

    class Results:
        def put(self, value):
            received.append(value)

    class Manager:
        def __init__(self, *_args):
            pass

        def get(self, _settings):
            manager_calls.append(True)
            raise AssertionError("manager must not receive invalid hints")

    import voice_studio.engines as engines

    monkeypatch.setattr(engines, "EngineManager", Manager)
    _engine_worker(Requests(), Results(), str(tmp_path / "cache"), str(tmp_path / "models"))
    assert received[0]["ok"] is False
    assert manager_calls == []
    assert "PRIVATE_HINT_7D2" not in received[0]["error"]


def test_worker_rejects_dictionary_path_before_manager_access(monkeypatch, tmp_path):
    received = []
    manager_calls = []

    class Requests:
        def __init__(self):
            self.items = iter(
                (
                    {
                        "job_id": "job",
                        "settings": {"dictionary_path": "PRIVATE_PATH_7D2"},
                        "source": str(tmp_path / "input.wav"),
                        "language": "uk",
                        "hints": [],
                    },
                    None,
                )
            )

        def get(self):
            return next(self.items)

    class Results:
        def put(self, value):
            received.append(value)

    class Manager:
        def __init__(self, *_args):
            pass

        def get(self, _settings):
            manager_calls.append(True)
            raise AssertionError("manager must not receive dictionary settings")

    import voice_studio.engines as engines

    monkeypatch.setattr(engines, "EngineManager", Manager)
    _engine_worker(Requests(), Results(), str(tmp_path / "cache"), str(tmp_path / "models"))
    assert received[0]["ok"] is False
    assert manager_calls == []
    assert received[0]["error"] == "ValueError: worker settings must not include dictionary_path"
    assert "PRIVATE_PATH_7D2" not in received[0]["error"]


def test_worker_redacts_engine_errors_that_echo_all_hint_markers(monkeypatch, tmp_path):
    received = []
    markers = ("PRIVATE_HINT_7D2", "PRIVATE_HINT_8E3")

    class Requests:
        def __init__(self):
            self.items = iter(
                (
                    {
                        "job_id": "job",
                        "settings": {},
                        "source": str(tmp_path / "input.wav"),
                        "language": "uk",
                        "hints": list(markers),
                    },
                    None,
                )
            )

        def get(self):
            return next(self.items)

    class Results:
        def put(self, value):
            received.append(value)

    class Engine:
        def transcribe(self, _source, _language, *, hints=None):
            raise RuntimeError(f"runtime rejected {', '.join(hints.terms)}")

    class Manager:
        def __init__(self, *_args):
            pass

        def get(self, _settings):
            return Engine()

    import voice_studio.engines as engines

    monkeypatch.setattr(engines, "EngineManager", Manager)
    _engine_worker(Requests(), Results(), str(tmp_path / "cache"), str(tmp_path / "models"))
    assert received[0]["error"] == (
        "RuntimeError: transcription engine failed while using recognition hints"
    )
    assert all(marker not in received[0]["error"] for marker in markers)


def test_controller_serializes_only_bounded_dictionary_terms(monkeypatch, tmp_path, make_wav):
    submitted = []
    controller = TranscriptionJobController(LocalStore(tmp_path / "data"), tmp_path / "cache")
    generation = object()
    monkeypatch.setattr("voice_studio.service.validate_media_file", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_ensure_worker", lambda **_kwargs: generation)
    monkeypatch.setattr(
        controller,
        "_submit",
        lambda _generation, request: submitted.append(request),
    )
    monkeypatch.setattr(
        controller,
        "_wait_for_result",
        lambda _generation, _job, _budget, _phase: {
            "ok": True,
            "result": EngineResult("fake", "fake", "uk", [Segment(0, 1, "raw")]),
        },
    )
    dictionary = TerminologyDictionary([DictionaryRule("source", "VOICE")])
    controller.run(make_wav(tmp_path / "input.wav"), Settings(model="fake"), dictionary)
    request = submitted[0]
    assert request["hints"] == ["VOICE"]
    assert "dictionary_path" not in request
    assert "dictionary_path" not in request["settings"]


def test_controller_uses_sanitized_settings_for_automatic_cleanup(
    monkeypatch, tmp_path, make_wav
):
    submitted = []
    controller = TranscriptionJobController(LocalStore(tmp_path / "data"), tmp_path / "cache")
    generation = object()
    monkeypatch.setattr("voice_studio.service.validate_media_file", lambda *_a, **_k: None)
    monkeypatch.setattr(controller, "_ensure_worker", lambda **_kwargs: generation)
    monkeypatch.setattr(
        controller,
        "_submit",
        lambda _generation, request: submitted.append(request),
    )

    def response(_generation, _job, _budget, phase):
        if phase == "cleaning":
            return {
                "ok": True,
                "proposal": {
                    "corrected_text": "raw",
                    "segments": [{"segment_index": 0, "corrected_text": "raw"}],
                    "changes": [],
                },
            }
        return {
            "ok": True,
            "result": EngineResult("ollama", "model", "uk", [Segment(0, 1, "raw")]),
        }

    monkeypatch.setattr(controller, "_wait_for_result", response)
    settings = Settings(automatic_cleanup=True, ollama_model="model", dictionary_path="private")
    controller.run(
        make_wav(tmp_path / "input.wav"),
        settings,
        TerminologyDictionary([DictionaryRule("source", "VOICE")]),
    )
    assert len(submitted) == 2
    assert all("dictionary_path" not in request["settings"] for request in submitted)
