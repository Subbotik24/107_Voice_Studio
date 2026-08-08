import json
import subprocess
import sys


def _import_state(module: str) -> dict[str, bool]:
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json, sys; "
                f"import {module}; "
                "print(json.dumps({name: name in sys.modules for name in "
                "('torch', 'faster_whisper')}))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(process.stdout)


def test_gui_and_cli_imports_do_not_load_model_runtimes_in_parent_process():
    assert _import_state("hermes_voice_studio.app") == {
        "torch": False,
        "faster_whisper": False,
    }
    assert _import_state("hermes_voice_studio.cli") == {
        "torch": False,
        "faster_whisper": False,
    }
