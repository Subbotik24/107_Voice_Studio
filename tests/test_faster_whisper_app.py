import builtins
import sys
from types import ModuleType, SimpleNamespace

import pytest

from voice_studio.engines.faster_whisper import FasterWhisperEngine


@pytest.mark.parametrize(
    ("field", "value"),
    [("device", "rocm"), ("compute_type", "float64")],
)
def test_engine_rejects_unsupported_hardware_before_runtime_import(field, value):
    sys.modules.pop("faster_whisper", None)

    with pytest.raises(ValueError, match=rf"{field}.*{value}") as error:
        FasterWhisperEngine("tiny", **{field: value})

    assert "faster_whisper" not in sys.modules
    assert "allowed" in str(error.value).lower()


@pytest.mark.parametrize(
    ("device", "compute_type", "message"),
    [
        ("cuda", "int8", "CUDA device"),
        ("cpu", "float16", "compute_type"),
    ],
)
def test_engine_rejects_runtime_unsupported_pair_before_whisper_model_load(
    monkeypatch, device, compute_type, message
):
    fake_ctranslate2 = SimpleNamespace(
        get_cuda_device_count=lambda: 0,
        get_supported_compute_types=lambda _device=None: ("int8",),
    )

    def forbidden_model_load(*_args, **_kwargs):
        pytest.fail("WhisperModel must not load for an unsupported runtime pair")

    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ctranslate2)
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=forbidden_model_load),
    )
    engine = FasterWhisperEngine("tiny", device=device, compute_type=compute_type)

    with pytest.raises(RuntimeError, match=message):
        engine._load()


def test_engine_allows_selector_compute_types_when_runtime_reports_concrete_types(
    monkeypatch,
):
    fake_ctranslate2 = SimpleNamespace(
        get_cuda_device_count=lambda: 0,
        get_supported_compute_types=lambda _device=None: ("int8",),
    )
    model = object()
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ctranslate2)
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=lambda *_args, **_kwargs: model),
    )

    assert FasterWhisperEngine("tiny", compute_type="default")._load() is model
    assert FasterWhisperEngine("tiny", compute_type="auto")._load() is model


@pytest.mark.parametrize(
    ("cuda_devices", "expected_runtime_device"),
    [(0, "cpu"), (1, "cuda")],
)
def test_engine_validates_auto_compute_type_for_ct2_selected_runtime_device(
    monkeypatch, cuda_devices, expected_runtime_device
):
    calls = []

    def supported_compute_types(device=None):
        calls.append(device)
        return ("float16",)

    fake_ctranslate2 = SimpleNamespace(
        get_cuda_device_count=lambda: cuda_devices,
        get_supported_compute_types=supported_compute_types,
    )
    model = object()
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ctranslate2)
    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        SimpleNamespace(WhisperModel=lambda *_args, **_kwargs: model),
    )

    engine = FasterWhisperEngine("tiny", device="auto", compute_type="float16")

    assert engine._load() is model
    assert calls == [expected_runtime_device]


def test_engine_preflights_ctranslate2_before_faster_whisper_import(monkeypatch):
    imported_faster_whisper = []
    real_import = builtins.__import__

    def controlled_import(name, *args, **kwargs):
        if name == "ctranslate2":
            raise OSError("CTranslate2 DLL load failed")
        if name == "faster_whisper":
            imported_faster_whisper.append(name)
            return SimpleNamespace(WhisperModel=lambda *_a, **_k: object())
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", controlled_import)
    monkeypatch.delitem(sys.modules, "ctranslate2", raising=False)
    fake_faster_whisper = ModuleType("faster_whisper")

    def get_faster_whisper_attribute(name):
        if name == "WhisperModel":
            imported_faster_whisper.append(name)
            return lambda *_a, **_k: object()
        raise AttributeError(name)

    fake_faster_whisper.__getattr__ = get_faster_whisper_attribute
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_faster_whisper)

    engine = FasterWhisperEngine("tiny", device="auto", compute_type="float16")

    with pytest.raises(RuntimeError, match="CTranslate2 runtime"):
        engine._load()

    assert imported_faster_whisper == []
