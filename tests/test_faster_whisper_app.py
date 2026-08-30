import sys
from types import SimpleNamespace

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
