import sys

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
