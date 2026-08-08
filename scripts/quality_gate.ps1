$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$Python = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }

function Invoke-Checked {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Command)
    & $Command[0] $Command[1..($Command.Length - 1)]
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $($Command -join ' ')"
    }
}

Invoke-Checked $Python -c "import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] <= (3, 12) else 2)"
Invoke-Checked $Python -c "import pytest, ruff"
Invoke-Checked $Python -m compileall -q src tests scripts packaging
Invoke-Checked $Python -m ruff check src tests scripts packaging
$env:PYTHONPATH = "src"
Invoke-Checked $Python -m pytest -q
Invoke-Checked $Python -m hermes_voice_studio.cli --version
