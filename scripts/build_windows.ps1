param(
    [string]$ReleaseLabel = "0.3.0-test-rc1-windows-x64",
    # Path to a passing 50-task acceptance JSON. When given, a release
    # manifest is written next to SHA256SUMS.txt exactly like the macOS Test RC
    # flow; without it the copy is a smoke build and carries no manifest.
    [string]$AcceptanceResult = $env:VOICE_STUDIO_ACCEPTANCE_RESULT
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

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$Path)

    $Stream = [System.IO.File]::Open(
        $Path,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::Read
    )
    $Algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $Bytes = $Algorithm.ComputeHash($Stream)
        return ([System.BitConverter]::ToString($Bytes)).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $Algorithm.Dispose()
        $Stream.Dispose()
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
Invoke-Checked $Python -c "import build, PyInstaller, pytest, ruff"

$FinalDirectory = Join-Path $ProjectRoot "dist\$ReleaseLabel"
if (Test-Path $FinalDirectory) {
    throw "refusing to overwrite existing Windows Test RC: $FinalDirectory"
}

$Identifier = [guid]::NewGuid().ToString("N")
$StageDirectory = Join-Path $ProjectRoot "dist\.windows-stage-$Identifier"
$WorkDirectory = Join-Path $ProjectRoot "build\.windows-work-$Identifier"
$AppDirectory = Join-Path $StageDirectory "VOICE Studio"
$Executable = Join-Path $AppDirectory "VOICE Studio.exe"
$RuntimeProbe = Join-Path $StageDirectory "runtime-probe.json"
$SBOM = Join-Path $StageDirectory "voice-studio-sbom.cdx.json"
$SmokeProfile = Join-Path $StageDirectory "smoke-profile"
$WheelSourceDirectory = Join-Path $StageDirectory ".wheel-source"

New-Item -ItemType Directory -Force -Path $StageDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $WorkDirectory | Out-Null

try {
    Invoke-Checked $Python scripts/generate_sbom.py `
        --lock (Join-Path $ProjectRoot "requirements-windows.lock") `
        --project-name "voice-studio" `
        --project-version "0.3.0rc1" `
        --output $SBOM

    & (Join-Path $PSScriptRoot "quality_gate.ps1")

    Invoke-Checked $Python -m PyInstaller --noconfirm --clean `
        --distpath $StageDirectory `
        --workpath $WorkDirectory `
        packaging/voice_studio.spec

    if (-not (Test-Path $Executable -PathType Leaf)) {
        throw "PyInstaller did not create $Executable"
    }

    $RequiredFrozenHelpPaths = @(
        "help-index.json",
        "uk\quick-start.md", "uk\workflows.md", "uk\reference.md", "uk\troubleshooting.md",
        "cs\quick-start.md", "cs\workflows.md", "cs\reference.md", "cs\troubleshooting.md",
        "en\quick-start.md", "en\workflows.md", "en\reference.md", "en\troubleshooting.md"
    )
    $FrozenHelpRoot = Join-Path $AppDirectory "_internal\docs\help"
    foreach ($RelativePath in $RequiredFrozenHelpPaths) {
        $HelpPath = Join-Path $FrozenHelpRoot $RelativePath
        if (-not (Test-Path -LiteralPath $HelpPath -PathType Leaf)) {
            throw "Frozen Help payload is missing: $RelativePath"
        }
    }

    $env:VOICE_STUDIO_RUNTIME_PROBE_OUTPUT = $RuntimeProbe
    $ProbeProcess = Start-Process -FilePath $Executable -WorkingDirectory $AppDirectory `
        -WindowStyle Hidden -Wait -PassThru
    if ($ProbeProcess.ExitCode -ne 0) {
        throw "Frozen runtime probe failed with exit code $($ProbeProcess.ExitCode)"
    }
    Invoke-Checked $Python -c `
        "import json, pathlib, sys; p=pathlib.Path(sys.argv[1]); d=json.loads(p.read_text(encoding='utf-8')); raise SystemExit(0 if d.get('status') == 'PASS' else 2)" `
        $RuntimeProbe

    Remove-Item Env:VOICE_STUDIO_RUNTIME_PROBE_OUTPUT -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $SmokeProfile | Out-Null
    $env:VOICE_STUDIO_CONFIG_DIR = Join-Path $SmokeProfile "config"
    $env:VOICE_STUDIO_DATA_DIR = Join-Path $SmokeProfile "data"
    $env:VOICE_STUDIO_CACHE_DIR = Join-Path $SmokeProfile "cache"
    $GuiProcess = Start-Process -FilePath $Executable -WorkingDirectory $AppDirectory `
        -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 6
    $GuiProcess.Refresh()
    if ($GuiProcess.HasExited) {
        throw "Packaged GUI exited during the six-second clean-profile smoke test"
    }
    Stop-Process -Id $GuiProcess.Id
    Wait-Process -Id $GuiProcess.Id -ErrorAction SilentlyContinue
    Remove-Item Env:VOICE_STUDIO_CONFIG_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:VOICE_STUDIO_DATA_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:VOICE_STUDIO_CACHE_DIR -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $SmokeProfile

    New-Item -ItemType Directory -Force -Path $WheelSourceDirectory | Out-Null
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "pyproject.toml") `
        -Destination $WheelSourceDirectory
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "README.md") `
        -Destination $WheelSourceDirectory
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "LICENSE") `
        -Destination $WheelSourceDirectory
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "src") `
        -Destination (Join-Path $WheelSourceDirectory "src") -Recurse
    Copy-Item -LiteralPath (Join-Path $ProjectRoot "docs/help") `
        -Destination (Join-Path $WheelSourceDirectory "docs/help") -Recurse
    Invoke-Checked $Python -m build --wheel --no-isolation --outdir $StageDirectory `
        $WheelSourceDirectory
    $WheelPath = Get-ChildItem -LiteralPath $StageDirectory -Filter "*.whl" |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $WheelPath) {
        throw "Python build did not create a wheel"
    }
    $RequiredWheelSuffixes = @(
        "voice_studio/profiles.py",
        "voice_studio/engines/ollama_audio.py",
        "share/voice-studio/help/help-index.json",
        "share/voice-studio/help/uk/quick-start.md",
        "share/voice-studio/help/cs/quick-start.md",
        "share/voice-studio/help/en/quick-start.md"
    )
    Invoke-Checked $Python -c `
        "import sys, zipfile; names=zipfile.ZipFile(sys.argv[1]).namelist(); required=sys.argv[2:]; valid=names and all(n.split('/', 1)[0].startswith('voice_studio') for n in names) and all(any(n.endswith(s) for n in names) for s in required); raise SystemExit(0 if valid else 2)" `
        $WheelPath @RequiredWheelSuffixes
    Remove-Item -LiteralPath $WheelSourceDirectory -Recurse -Force

    $ArchiveName = "VOICE-Studio-$ReleaseLabel-unsigned.zip"
    $ArchivePath = Join-Path $StageDirectory $ArchiveName
    Compress-Archive -Path $AppDirectory -DestinationPath $ArchivePath `
        -CompressionLevel Optimal

    $ReadmePath = Join-Path $StageDirectory "WINDOWS-README.txt"
    @"
VOICE Studio $ReleaseLabel

1. Extract $ArchiveName.
2. Open the extracted 'VOICE Studio' folder.
3. Run 'VOICE Studio.exe'.
4. Windows SmartScreen may warn because this Test RC is unsigned.
5. The app stores settings, history, models and audio under the current Windows
   user profile. The archive contains no user data or model files.

This artifact must pass a real Windows 10/11 x64 microphone, hotkey and
50-task acceptance run before it can be called production-ready.
"@ | Set-Content -Path $ReadmePath -Encoding UTF8

    $ChecksumTargets = @($ArchivePath, $RuntimeProbe, $ReadmePath, $SBOM)
    if ($AcceptanceResult) {
        if (-not (Test-Path -LiteralPath $AcceptanceResult)) {
            throw "VOICE_STUDIO_ACCEPTANCE_RESULT does not exist: $AcceptanceResult"
        }
        $AcceptanceCopy = Join-Path $StageDirectory "acceptance-result.json"
        Copy-Item -LiteralPath $AcceptanceResult -Destination $AcceptanceCopy
        $ManifestPath = Join-Path $StageDirectory "release-manifest.json"
        Invoke-Checked $Python scripts/create_release_manifest.py `
            --release-directory $StageDirectory `
            --release-label $ReleaseLabel `
            --release-kind unsigned-windows-test-rc `
            --acceptance-result $AcceptanceCopy `
            --repository-root $ProjectRoot `
            --sbom $SBOM `
            --artifact $ArchivePath `
            --artifact $WheelPath `
            --artifact $RuntimeProbe `
            --artifact $AcceptanceCopy `
            --output $ManifestPath | Out-Null
        $ChecksumTargets += @($AcceptanceCopy, $ManifestPath)
    } else {
        Write-Host "No acceptance result given: smoke build without release-manifest.json"
    }
    $ChecksumTargets += Get-ChildItem -LiteralPath $StageDirectory -Filter "*.whl" |
        Select-Object -ExpandProperty FullName
    $ChecksumLines = foreach ($Target in $ChecksumTargets) {
        $Hash = Get-Sha256Hex -Path $Target
        "$Hash  $([System.IO.Path]::GetFileName($Target))"
    }
    $ChecksumLines | Set-Content -Path (Join-Path $StageDirectory "SHA256SUMS.txt") `
        -Encoding ASCII

    Invoke-Checked $Python scripts/release_filesystem.py promote --source $StageDirectory --destination $FinalDirectory
    Write-Host "Created verified unsigned Windows copy: $FinalDirectory"
    Write-Host "Executable: $FinalDirectory\VOICE Studio\VOICE Studio.exe"
    Write-Host "Archive: $FinalDirectory\$ArchiveName"
}
finally {
    Remove-Item Env:VOICE_STUDIO_RUNTIME_PROBE_OUTPUT -ErrorAction SilentlyContinue
    Remove-Item Env:VOICE_STUDIO_CONFIG_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:VOICE_STUDIO_DATA_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:VOICE_STUDIO_CACHE_DIR -ErrorAction SilentlyContinue
    if (Test-Path $StageDirectory) {
        Remove-Item -Recurse -Force $StageDirectory
    }
    if (Test-Path $WorkDirectory) {
        Remove-Item -Recurse -Force $WorkDirectory
    }
}
