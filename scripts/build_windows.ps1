param(
    [string]$ReleaseLabel = "0.3.0-test-rc1-windows-x64"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $ProjectRoot
$Python = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE`: $FilePath $($Arguments -join ' ')"
    }
}

Invoke-Checked $Python -c @"
import sys
valid = (
    sys.platform == 'win32'
    and sys.maxsize > 2**32
    and (3, 11) <= sys.version_info[:2] <= (3, 12)
)
raise SystemExit(0 if valid else 2)
"@
Invoke-Checked $Python -c "import build, PyInstaller, pytest, ruff, torch"

$FinalDirectory = Join-Path $ProjectRoot "dist\$ReleaseLabel"
if (Test-Path $FinalDirectory) {
    throw "refusing to overwrite existing Windows Test RC: $FinalDirectory"
}

$Identifier = [guid]::NewGuid().ToString("N")
$StageDirectory = Join-Path $ProjectRoot "dist\.windows-stage-$Identifier"
$WorkDirectory = Join-Path $ProjectRoot "build\.windows-work-$Identifier"
$AppDirectory = Join-Path $StageDirectory "Hermes Voice Studio"
$Executable = Join-Path $AppDirectory "Hermes Voice Studio.exe"
$RuntimeProbe = Join-Path $StageDirectory "runtime-probe.json"
$SmokeProfile = Join-Path $StageDirectory "smoke-profile"

New-Item -ItemType Directory -Force -Path $StageDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $WorkDirectory | Out-Null

try {
    & (Join-Path $PSScriptRoot "quality_gate.ps1")

    Invoke-Checked $Python -m PyInstaller --noconfirm --clean `
        --distpath $StageDirectory `
        --workpath $WorkDirectory `
        packaging/hermes_voice_studio.spec

    if (-not (Test-Path $Executable -PathType Leaf)) {
        throw "PyInstaller did not create $Executable"
    }

    $TorchJitInternal = Join-Path $AppDirectory "_internal\torch\_jit_internal.py"
    if (-not (Test-Path $TorchJitInternal -PathType Leaf)) {
        throw "Frozen PyTorch source was not found: $TorchJitInternal"
    }
    Invoke-Checked $Python scripts/patch_frozen_torch.py --target $TorchJitInternal

    $env:HVS_RUNTIME_PROBE_OUTPUT = $RuntimeProbe
    $ProbeProcess = Start-Process -FilePath $Executable -WorkingDirectory $AppDirectory `
        -Wait -PassThru
    if ($ProbeProcess.ExitCode -ne 0) {
        throw "Frozen runtime probe failed with exit code $($ProbeProcess.ExitCode)"
    }
    Invoke-Checked $Python -c `
        "import json, pathlib, sys; p=pathlib.Path(sys.argv[1]); d=json.loads(p.read_text(encoding='utf-8')); raise SystemExit(0 if d.get('status') == 'PASS' else 2)" `
        $RuntimeProbe

    Remove-Item Env:HVS_RUNTIME_PROBE_OUTPUT -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $SmokeProfile | Out-Null
    $env:HVS_CONFIG_DIR = Join-Path $SmokeProfile "config"
    $env:HVS_DATA_DIR = Join-Path $SmokeProfile "data"
    $env:HVS_CACHE_DIR = Join-Path $SmokeProfile "cache"
    $GuiProcess = Start-Process -FilePath $Executable -WorkingDirectory $AppDirectory `
        -PassThru
    Start-Sleep -Seconds 6
    $GuiProcess.Refresh()
    if ($GuiProcess.HasExited) {
        throw "Packaged GUI exited during the six-second clean-profile smoke test"
    }
    Stop-Process -Id $GuiProcess.Id
    Wait-Process -Id $GuiProcess.Id -ErrorAction SilentlyContinue
    Remove-Item Env:HVS_CONFIG_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:HVS_DATA_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:HVS_CACHE_DIR -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $SmokeProfile

    Invoke-Checked $Python -m build --wheel --no-isolation --outdir $StageDirectory `
        $ProjectRoot

    $ArchiveName = "Hermes-Voice-Studio-$ReleaseLabel-unsigned.zip"
    $ArchivePath = Join-Path $StageDirectory $ArchiveName
    Compress-Archive -Path $AppDirectory -DestinationPath $ArchivePath `
        -CompressionLevel Optimal

    $ReadmePath = Join-Path $StageDirectory "WINDOWS-README.txt"
    @"
Hermes Voice Studio $ReleaseLabel

1. Extract $ArchiveName.
2. Open the extracted 'Hermes Voice Studio' folder.
3. Run 'Hermes Voice Studio.exe'.
4. Windows SmartScreen may warn because this Test RC is unsigned.
5. The app stores settings, history, models and audio under the current Windows
   user profile. The archive contains no user data or model files.

This artifact must pass a real Windows 10/11 x64 microphone, hotkey and
50-task acceptance run before it can be called production-ready.
"@ | Set-Content -Path $ReadmePath -Encoding UTF8

    $ChecksumTargets = @($ArchivePath, $RuntimeProbe, $ReadmePath)
    $ChecksumTargets += Get-ChildItem -Path $StageDirectory -Filter "*.whl" |
        Select-Object -ExpandProperty FullName
    $ChecksumLines = foreach ($Target in $ChecksumTargets) {
        $Hash = (Get-FileHash -Algorithm SHA256 -Path $Target).Hash.ToLowerInvariant()
        "$Hash  $([System.IO.Path]::GetFileName($Target))"
    }
    $ChecksumLines | Set-Content -Path (Join-Path $StageDirectory "SHA256SUMS.txt") `
        -Encoding ASCII

    Move-Item -Path $StageDirectory -Destination $FinalDirectory
    Write-Host "Created verified unsigned Windows copy: $FinalDirectory"
    Write-Host "Executable: $FinalDirectory\Hermes Voice Studio\Hermes Voice Studio.exe"
    Write-Host "Archive: $FinalDirectory\$ArchiveName"
}
finally {
    Remove-Item Env:HVS_RUNTIME_PROBE_OUTPUT -ErrorAction SilentlyContinue
    Remove-Item Env:HVS_CONFIG_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:HVS_DATA_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:HVS_CACHE_DIR -ErrorAction SilentlyContinue
    if (Test-Path $StageDirectory) {
        Remove-Item -Recurse -Force $StageDirectory
    }
    if (Test-Path $WorkDirectory) {
        Remove-Item -Recurse -Force $WorkDirectory
    }
}
