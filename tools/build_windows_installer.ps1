[CmdletBinding()]
param(
    [string]$OutputRoot,
    [string]$InnoCompilerPath,
    [switch]$ReleaseBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $RepositoryRoot "artifacts\windows"
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
if ($OutputRoot -eq $RepositoryRoot) {
    throw "OutputRoot must not be the repository root"
}

function Reset-Directory([string]$Path) {
    $Resolved = [IO.Path]::GetFullPath($Path)
    if (-not $Resolved.StartsWith(
        $OutputRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    ) -and $Resolved -ne $OutputRoot) {
        throw "Refusing to reset a path outside OutputRoot: $Resolved"
    }
    if (Test-Path -LiteralPath $Resolved) {
        Remove-Item -LiteralPath $Resolved -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $Resolved | Out-Null
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Content, $Encoding)
}

function Get-ProjectVersion {
    $Project = Get-Content -LiteralPath (Join-Path $RepositoryRoot "pyproject.toml") `
        -Raw -Encoding UTF8
    $ProjectSection = [regex]::Match($Project, '(?ms)^\[project\]\s*(.*?)(?=^\[|\z)')
    $VersionMatch = [regex]::Match(
        $ProjectSection.Groups[1].Value,
        '(?m)^version\s*=\s*"([^"]+)"\s*$'
    )
    if (-not $ProjectSection.Success -or -not $VersionMatch.Success) {
        throw "pyproject.toml [project] has no version"
    }
    $Version = $VersionMatch.Groups[1].Value
    if ($Version -notmatch '^\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.dev\d+)?$') {
        throw "Unsupported project version: $Version"
    }
    return $Version
}

function Get-NumericVersion([string]$Version) {
    $Match = [regex]::Match($Version, '^(\d+)\.(\d+)\.(\d+)')
    if (-not $Match.Success) {
        throw "Cannot create a Windows file version from $Version"
    }
    return "$($Match.Groups[1].Value).$($Match.Groups[2].Value).$($Match.Groups[3].Value).0"
}

function New-AppIcon([string]$Destination) {
    $Source = Join-Path $RepositoryRoot `
        "src\auto_speech_journal\assets\brand\journal-ink-icon.png"
    $Code = @'
from pathlib import Path
import sys
from PIL import Image

source, destination = map(Path, sys.argv[1:])
with Image.open(source) as image:
    image.save(destination, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
'@
    $Script = Join-Path ([IO.Path]::GetDirectoryName($Destination)) "make_icon.py"
    Write-Utf8NoBom $Script $Code
    & uv run --no-sync python $Script $Source $Destination
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Destination)) {
        throw "Failed to generate the Windows icon"
    }
    Remove-Item -LiteralPath $Script -Force
}

function New-PyInstallerVersionFile(
    [string]$Destination,
    [string]$Version,
    [string]$NumericVersion,
    [string]$ExecutableName,
    [string]$Description
) {
    $Parts = $NumericVersion.Split('.')
    $Tuple = "($($Parts -join ', '))"
    $InternalName = [IO.Path]::GetFileNameWithoutExtension($ExecutableName)
    $Content = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=$Tuple,
    prodvers=$Tuple,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'benny7431'),
        StringStruct('FileDescription', '$Description'),
        StringStruct('FileVersion', '$NumericVersion'),
        StringStruct('InternalName', '$InternalName'),
        StringStruct('LegalCopyright', 'Copyright (c) benny7431'),
        StringStruct('OriginalFilename', '$ExecutableName'),
        StringStruct('ProductName', 'Auto Speech Journal'),
        StringStruct('ProductVersion', '$Version')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
    Write-Utf8NoBom $Destination $Content
}

function Resolve-InnoCompiler([string]$RequestedPath) {
    $Candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        $Candidates += $RequestedPath
    }
    if (-not [string]::IsNullOrWhiteSpace(${env:ProgramFiles(x86)})) {
        $Candidates += (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
    }
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $Candidates += (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
    }
    $Candidates += (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
    $Command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($null -ne $Command) {
        $Candidates += $Command.Source
    }
    $Compiler = $Candidates | Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
    if ($null -eq $Compiler) {
        throw "Inno Setup 6 ISCC.exe was not found"
    }
    return [IO.Path]::GetFullPath($Compiler)
}

function Get-InnoCompilerVersion([string]$CompilerPath) {
    $InstallRoot = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($CompilerPath))
    $Uninstaller = Join-Path $InstallRoot "unins000.exe"
    if (-not (Test-Path -LiteralPath $Uninstaller -PathType Leaf)) {
        throw "Cannot determine the installed Inno Setup version"
    }
    $Version = ([string][Diagnostics.FileVersionInfo]::GetVersionInfo(
        $Uninstaller
    ).ProductVersion).Trim()
    if ($Version -notmatch '^\d+\.\d+\.\d+$') {
        throw "Cannot determine the installed Inno Setup version"
    }
    return $Version
}

$Version = Get-ProjectVersion
$NumericVersion = Get-NumericVersion $Version
$PinnedInnoVersion = "6.7.3"
$BuildRoot = Join-Path $OutputRoot "build"
$ApplicationRoot = Join-Path $OutputRoot "application"
$SetupOutput = Join-Path $OutputRoot "setup"
Reset-Directory $OutputRoot
New-Item -ItemType Directory -Force -Path $BuildRoot, $ApplicationRoot, $SetupOutput |
    Out-Null

& uv lock --check
if ($LASTEXITCODE -ne 0) {
    throw "uv.lock is not current with pyproject.toml"
}
& uv sync --frozen --no-editable --extra dev --reinstall-package auto-speech-journal
if ($LASTEXITCODE -ne 0) {
    throw "Locked build environment installation failed"
}

$Icon = Join-Path $BuildRoot "AutoSpeechJournal.ico"
New-AppIcon $Icon
$GuiVersionFile = Join-Path $BuildRoot "pyinstaller-gui-version.txt"
$CliVersionFile = Join-Path $BuildRoot "pyinstaller-cli-version.txt"
New-PyInstallerVersionFile `
    $GuiVersionFile $Version $NumericVersion "AutoSpeechJournal.exe" "Auto Speech Journal"
New-PyInstallerVersionFile `
    $CliVersionFile $Version $NumericVersion `
    "AutoSpeechJournal.CLI.exe" "Auto Speech Journal CLI"
$env:ASJ_GUI_VERSION_FILE = $GuiVersionFile
$env:ASJ_CLI_VERSION_FILE = $CliVersionFile
$env:ASJ_ICON_FILE = $Icon

$PyInstallerDist = Join-Path $BuildRoot "pyinstaller-dist"
$PyInstallerWork = Join-Path $BuildRoot "pyinstaller-work"
& uv run --no-sync pyinstaller `
    --noconfirm --clean --log-level WARN `
    --distpath $PyInstallerDist `
    --workpath $PyInstallerWork `
    (Join-Path $RepositoryRoot "packaging\windows\AutoSpeechJournal.spec")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed"
}
$BuiltPayload = Join-Path $PyInstallerDist "AutoSpeechJournal"
foreach ($Required in @("AutoSpeechJournal.exe", "AutoSpeechJournal.CLI.exe")) {
    if (-not (Test-Path -LiteralPath (Join-Path $BuiltPayload $Required) -PathType Leaf)) {
        throw "PyInstaller output is missing $Required"
    }
}
Copy-Item -Path (Join-Path $BuiltPayload "*") -Destination $ApplicationRoot -Recurse -Force
Copy-Item -LiteralPath (Join-Path $RepositoryRoot "LICENSE") -Destination $ApplicationRoot
Copy-Item -LiteralPath (Join-Path $RepositoryRoot "THIRD_PARTY_NOTICES.md") `
    -Destination $ApplicationRoot

$Manifest = Get-ChildItem -LiteralPath $ApplicationRoot -Recurse `
    -Filter "runtime-models-v1.json" -File
if (@($Manifest).Count -ne 1) {
    throw "Frozen application must contain exactly one runtime-models-v1.json"
}
$BundledModels = @(Get-ChildItem -LiteralPath $ApplicationRoot -Recurse -File |
    Where-Object {
        $_.Extension -in @('.onnx', '.safetensors') -or $_.Name -eq 'model.bin'
    })
if ($BundledModels.Count -ne 0) {
    throw "Runtime models must be downloaded after installation, not bundled"
}

$Inno = Resolve-InnoCompiler $InnoCompilerPath
$InnoVersion = Get-InnoCompilerVersion $Inno
if ($ReleaseBuild -and $InnoVersion -ne $PinnedInnoVersion) {
    throw "Release builds require Inno Setup $PinnedInnoVersion; found $InnoVersion"
}
& $Inno `
    "/Qp" `
    "/DAppVersion=$Version" `
    "/DAppNumericVersion=$NumericVersion" `
    "/DAppPayloadRoot=$ApplicationRoot" `
    "/DOutputDir=$SetupOutput" `
    "/DAppIcon=$Icon" `
    (Join-Path $RepositoryRoot "packaging\windows\AutoSpeechJournal.iss")
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed"
}

$Setup = Join-Path $SetupOutput "AutoSpeechJournal-Setup-$Version-x64.exe"
if (-not (Test-Path -LiteralPath $Setup -PathType Leaf)) {
    throw "Inno Setup did not produce $Setup"
}
$Hash = (Get-FileHash -LiteralPath $Setup -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Utf8NoBom (Join-Path $SetupOutput "SHA256SUMS.txt") "$Hash  $([IO.Path]::GetFileName($Setup))`n"
Write-Host "Built unsigned Setup: $Setup"
Write-Host "SHA-256: $Hash"
