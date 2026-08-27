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
                "('faster_whisper', 'av')}))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(process.stdout)


def test_gui_and_cli_imports_do_not_load_model_runtimes_in_parent_process():
    """Model runtimes and the native media parser stay out of the parent.

    `av` belongs in this set: the parent process owns the Tk loop and the user's
    original files, and it is the process a crafted media file would reach if
    the parser were loaded here (SEC-001).
    """

    absent = {"faster_whisper": False, "av": False}
    assert _import_state("voice_studio.app") == absent
    assert _import_state("voice_studio.cli") == absent
