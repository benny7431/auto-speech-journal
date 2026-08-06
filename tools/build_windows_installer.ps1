[CmdletBinding()]
param(
    [ValidateSet("All", "Application", "Installer")]
    [string]$Stage = "All",
    [string]$OutputRoot,
    [string]$AppPayloadPath,
    [string]$LauncherPath,
    [string]$ModelManifestPath,
    [string]$InnoCompilerPath,
    [switch]$ReleaseBuild,
    [switch]$SkipSbom
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$SourceRoot = Join-Path $RepositoryRoot "src"
if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $env:PYTHONPATH = $SourceRoot
}
else {
    $env:PYTHONPATH = $SourceRoot + [IO.Path]::PathSeparator + $env:PYTHONPATH
}
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $RepositoryRoot "artifacts\windows"
}
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$OutputPrefix = $OutputRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) +
    [IO.Path]::DirectorySeparatorChar

function Assert-OutputPath([string]$Path) {
    $Resolved = [IO.Path]::GetFullPath($Path)
    if (
        -not $Resolved.Equals($OutputRoot, [StringComparison]::OrdinalIgnoreCase) -and
        -not $Resolved.StartsWith($OutputPrefix, [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Refusing to modify a build path outside $OutputRoot`: $Resolved"
    }
}

function Reset-OutputDirectory([string]$Path) {
    Assert-OutputPath $Path
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $Encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($Path, $Content, $Encoding)
}

function Get-ProjectVersion {
    $Project = Get-Content -LiteralPath (Join-Path $RepositoryRoot "pyproject.toml") -Raw -Encoding UTF8
    $ProjectSection = [regex]::Match(
        $Project,
        '(?ms)^\[project\]\s*(.*?)(?=^\[|\z)'
    )
    if (-not $ProjectSection.Success) {
        throw "pyproject.toml has no [project] section"
    }
    $VersionMatch = [regex]::Match(
        $ProjectSection.Groups[1].Value,
        '(?m)^version\s*=\s*"([^"]+)"\s*$'
    )
    if (-not $VersionMatch.Success) {
        throw "pyproject.toml [project] has no version"
    }
    $Version = $VersionMatch.Groups[1].Value
    if ($Version -notmatch '^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$') {
        throw "Unsupported project version for the Windows installer: $Version"
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

function Get-DirectoryBytes([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return [Int64]0
    }
    $Total = [Int64]0
    Get-ChildItem -LiteralPath $Path -Recurse -File | ForEach-Object {
        $Total += [Int64]$_.Length
    }
    return $Total
}

function Get-ManifestTotal([string]$Path, [string]$Property) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return [Int64]0
    }
    $Manifest = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    $Total = [Int64]0
    $ModelsProperty = $Manifest.PSObject.Properties["models"]
    if ($null -ne $ModelsProperty) {
        foreach ($Model in @($ModelsProperty.Value)) {
            foreach ($File in @($Model.files)) {
                $ValueProperty = $File.PSObject.Properties[$Property]
                $Value = if ($null -ne $ValueProperty) { $ValueProperty.Value } else { $null }
                if ($null -ne $Value) {
                    $Total += [Int64]$Value
                }
            }
        }
    }
    else {
        foreach ($Asset in @($Manifest.assets)) {
            $ValueProperty = $Asset.PSObject.Properties[$Property]
            if ($null -ne $ValueProperty) {
                $Total += [Int64]$ValueProperty.Value
            }
        }
    }
    return $Total
}

function New-AppIcon([string]$Destination) {
    $Source = Join-Path $RepositoryRoot "src\auto_speech_journal\assets\brand\journal-ink-icon.png"
    $Code = @'
from pathlib import Path
import sys
from PIL import Image

source, destination = map(Path, sys.argv[1:])
with Image.open(source) as image:
    image.save(destination, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
'@
    $TemporaryScript = Join-Path ([IO.Path]::GetDirectoryName($Destination)) "make_icon.py"
    Write-Utf8NoBom $TemporaryScript $Code
    & uv run --no-sync python $TemporaryScript $Source $Destination
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $Destination)) {
        throw "Failed to generate the Windows icon"
    }
    Remove-Item -LiteralPath $TemporaryScript -Force
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

function Get-CSharpCompiler {
    $Candidates = @(
        (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
        (Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe")
    )
    $Compiler = $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($null -eq $Compiler) {
        throw ".NET Framework 4.x csc.exe is required to build the stable launchers"
    }
    return $Compiler
}

function Build-StableLaunchers(
    [string]$Destination,
    [string]$Version,
    [string]$NumericVersion,
    [string]$IconPath,
    [string]$BuildRoot
) {
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    $Compiler = Get-CSharpCompiler
    $Program = Join-Path $RepositoryRoot "packaging\windows\launcher\Program.cs"
    $GuiAssemblyInfo = Join-Path $BuildRoot "Launcher.Gui.AssemblyInfo.cs"
    $GuiAssemblySource = @"
using System.Reflection;
[assembly: AssemblyTitle("Auto Speech Journal Launcher")]
[assembly: AssemblyCompany("benny7431")]
[assembly: AssemblyProduct("Auto Speech Journal")]
[assembly: AssemblyCopyright("Copyright (c) benny7431")]
[assembly: AssemblyVersion("$NumericVersion")]
[assembly: AssemblyFileVersion("$NumericVersion")]
[assembly: AssemblyInformationalVersion("$Version")]
"@
    Write-Utf8NoBom $GuiAssemblyInfo $GuiAssemblySource

    $GuiOutput = Join-Path $Destination "AutoSpeechJournal.exe"
    & $Compiler /nologo /optimize+ /platform:x64 /target:winexe `
        "/win32icon:$IconPath" "/out:$GuiOutput" `
        /reference:System.Runtime.Serialization.dll /reference:System.Windows.Forms.dll `
        $Program $GuiAssemblyInfo
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to compile the stable GUI launcher"
    }

    $CliAssemblyInfo = Join-Path $BuildRoot "Launcher.Cli.AssemblyInfo.cs"
    $CliAssemblySource = @"
using System.Reflection;
[assembly: AssemblyTitle("Auto Speech Journal CLI Launcher")]
[assembly: AssemblyCompany("benny7431")]
[assembly: AssemblyProduct("Auto Speech Journal")]
[assembly: AssemblyCopyright("Copyright (c) benny7431")]
[assembly: AssemblyVersion("$NumericVersion")]
[assembly: AssemblyFileVersion("$NumericVersion")]
[assembly: AssemblyInformationalVersion("$Version")]
"@
    Write-Utf8NoBom $CliAssemblyInfo $CliAssemblySource

    $CliOutput = Join-Path $Destination "AutoSpeechJournal.CLI.exe"
    & $Compiler /nologo /optimize+ /platform:x64 /target:exe /define:CLI_LAUNCHER `
        "/win32icon:$IconPath" "/out:$CliOutput" `
        /reference:System.Runtime.Serialization.dll $Program $CliAssemblyInfo
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to compile the stable CLI launcher"
    }
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
    $Compiler = $Candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($null -eq $Compiler) {
        throw "Inno Setup 6 ISCC.exe was not found"
    }
    return [IO.Path]::GetFullPath($Compiler)
}

function Get-InnoCompilerVersion([string]$CompilerPath) {
    $InstallRoot = [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($CompilerPath))
    $Uninstaller = Join-Path $InstallRoot "unins000.exe"
    if (Test-Path -LiteralPath $Uninstaller -PathType Leaf) {
        $ProductVersion = ([string][Diagnostics.FileVersionInfo]::GetVersionInfo(
            $Uninstaller
        ).ProductVersion).Trim()
        if ($ProductVersion -match '^\d+\.\d+\.\d+$') {
            return $ProductVersion
        }
    }
    $UninstallRoots = @(
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall",
        "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
    )
    foreach ($Root in $UninstallRoots) {
        foreach ($Key in @(Get-ChildItem -LiteralPath $Root -ErrorAction SilentlyContinue)) {
            $Entry = Get-ItemProperty -LiteralPath $Key.PSPath -ErrorAction SilentlyContinue
            if ($null -eq $Entry) {
                continue
            }
            $InstallLocationProperty = $Entry.PSObject.Properties['InstallLocation']
            $DisplayVersionProperty = $Entry.PSObject.Properties['DisplayVersion']
            if (
                $null -eq $InstallLocationProperty -or
                $null -eq $DisplayVersionProperty -or
                [string]::IsNullOrWhiteSpace([string]$InstallLocationProperty.Value)
            ) {
                continue
            }
            $RegisteredRoot = [IO.Path]::GetFullPath(
                [string]$InstallLocationProperty.Value
            ).TrimEnd('\', '/')
            if (
                $RegisteredRoot.Equals(
                    $InstallRoot.TrimEnd('\', '/'),
                    [StringComparison]::OrdinalIgnoreCase
                ) -and
                [string]$DisplayVersionProperty.Value -match '^\d+\.\d+\.\d+$'
            ) {
                return [string]$DisplayVersionProperty.Value
            }
        }
    }
    throw "Cannot determine the installed Inno Setup version for $CompilerPath"
}

$Version = Get-ProjectVersion
$NumericVersion = Get-NumericVersion $Version
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

$BuildRoot = Join-Path $OutputRoot "build"
$ApplicationRoot = Join-Path $OutputRoot "application"
$DefaultPayload = Join-Path $ApplicationRoot "payload"
$DefaultLaunchers = Join-Path $ApplicationRoot "launchers"
$GeneratedIcon = Join-Path $BuildRoot "AutoSpeechJournal.ico"
$DefaultModelManifest = Join-Path $RepositoryRoot "packaging\manifests\runtime-models-v1.json"
$CudaManifest = Join-Path $RepositoryRoot "packaging\manifests\cuda-runtime-v1.json"
$PinnedInnoVersion = "6.7.3"
$DetectedInnoVersion = $null

& uv lock --check
if ($LASTEXITCODE -ne 0) {
    throw "uv.lock is not current with pyproject.toml"
}
& uv run --no-sync python `
    (Join-Path $RepositoryRoot "packaging\manifests\validate_cuda_manifest.py") `
    --manifest $CudaManifest `
    --lock (Join-Path $RepositoryRoot "uv.lock")
if ($LASTEXITCODE -ne 0) {
    throw "CUDA manifest does not match the three Windows wheels locked in uv.lock"
}

if ([string]::IsNullOrWhiteSpace($ModelManifestPath)) {
    $ModelManifestPath = $DefaultModelManifest
}
$ModelManifestPath = [IO.Path]::GetFullPath($ModelManifestPath)

& uv run --no-sync python `
    (Join-Path $RepositoryRoot "packaging\models\validate_runtime_model_manifest.py") `
    --manifest $ModelManifestPath
if ($LASTEXITCODE -ne 0) {
    throw "Runtime models must match the reviewed, commit-pinned Hugging Face manifest"
}

if ($Stage -eq "All" -or $Stage -eq "Application") {
    & uv sync --frozen --no-editable --extra dev --reinstall-package auto-speech-journal
    if ($LASTEXITCODE -ne 0) {
        throw "Locked build environment or current project metadata installation failed"
    }
    Reset-OutputDirectory $BuildRoot
    Reset-OutputDirectory $ApplicationRoot
    New-Item -ItemType Directory -Force -Path $DefaultPayload | Out-Null
    New-Item -ItemType Directory -Force -Path $DefaultLaunchers | Out-Null

    New-AppIcon $GeneratedIcon
    $GuiVersionFile = Join-Path $BuildRoot "pyinstaller-gui-version.txt"
    $CliVersionFile = Join-Path $BuildRoot "pyinstaller-cli-version.txt"
    $FrozenInventory = Join-Path $BuildRoot "frozen-runtime-inventory.json"
    New-PyInstallerVersionFile `
        $GuiVersionFile $Version $NumericVersion "AutoSpeechJournal.exe" "Auto Speech Journal"
    New-PyInstallerVersionFile `
        $CliVersionFile $Version $NumericVersion `
        "AutoSpeechJournal.CLI.exe" "Auto Speech Journal CLI"
    $env:ASJ_VERSION_FILE = $GuiVersionFile
    $env:ASJ_GUI_VERSION_FILE = $GuiVersionFile
    $env:ASJ_CLI_VERSION_FILE = $CliVersionFile
    $env:ASJ_FROZEN_INVENTORY_FILE = $FrozenInventory
    $env:ASJ_PROJECT_VERSION = $Version
    $env:ASJ_ICON_FILE = $GeneratedIcon

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
    if (
        -not (Test-Path -LiteralPath (Join-Path $BuiltPayload "AutoSpeechJournal.exe")) -or
        -not (Test-Path -LiteralPath (Join-Path $BuiltPayload "AutoSpeechJournal.CLI.exe"))
    ) {
        throw "PyInstaller did not produce both required entry points"
    }
    Copy-Item -Path (Join-Path $BuiltPayload "*") -Destination $DefaultPayload -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $RepositoryRoot "LICENSE") -Destination $DefaultPayload
    Copy-Item -LiteralPath (Join-Path $RepositoryRoot "THIRD_PARTY_NOTICES.md") `
        -Destination $DefaultPayload
    if (-not (Test-Path -LiteralPath $FrozenInventory -PathType Leaf)) {
        throw "PyInstaller did not produce the frozen runtime inventory"
    }
    Copy-Item -LiteralPath $FrozenInventory `
        -Destination (Join-Path $DefaultPayload "frozen-runtime-inventory.json")
    Build-StableLaunchers $DefaultLaunchers $Version $NumericVersion $GeneratedIcon $BuildRoot

    Copy-Item -LiteralPath $GeneratedIcon -Destination (Join-Path $ApplicationRoot "AutoSpeechJournal.ico")
    Copy-Item -LiteralPath $ModelManifestPath `
        -Destination (Join-Path $ApplicationRoot "runtime-models-v1.json")
    Copy-Item -LiteralPath $CudaManifest -Destination (Join-Path $ApplicationRoot "cuda-runtime-v1.json")

    if (-not $SkipSbom) {
        $SbomLog = & uv export `
            --frozen `
            --no-dev `
            --no-editable `
            --preview-features sbom-export `
            --format cyclonedx1.5 `
            --output-file (Join-Path $ApplicationRoot "AutoSpeechJournal.cdx.json") 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Locked runtime CycloneDX SBOM generation failed: $($SbomLog -join [Environment]::NewLine)"
        }
        & uv run --no-sync python `
            (Join-Path $RepositoryRoot "packaging\windows\runtime_inventory.py") `
            validate `
            --sbom (Join-Path $ApplicationRoot "AutoSpeechJournal.cdx.json") `
            --inventory (Join-Path $DefaultPayload "frozen-runtime-inventory.json") `
            --lock (Join-Path $RepositoryRoot "uv.lock") `
            --pyproject (Join-Path $RepositoryRoot "pyproject.toml") `
            --payload $DefaultPayload
        if ($LASTEXITCODE -ne 0) {
            throw "Frozen runtime and CycloneDX SBOM validation failed"
        }
    }
}

if ($Stage -eq "All" -or $Stage -eq "Installer") {
    if ([string]::IsNullOrWhiteSpace($AppPayloadPath)) {
        $AppPayloadPath = $DefaultPayload
    }
    if ([string]::IsNullOrWhiteSpace($LauncherPath)) {
        $LauncherPath = $DefaultLaunchers
    }
    $AppPayloadPath = [IO.Path]::GetFullPath($AppPayloadPath)
    $LauncherPath = [IO.Path]::GetFullPath($LauncherPath)
    foreach ($Required in @(
        (Join-Path $AppPayloadPath "AutoSpeechJournal.exe"),
        (Join-Path $AppPayloadPath "AutoSpeechJournal.CLI.exe"),
        (Join-Path $LauncherPath "AutoSpeechJournal.exe"),
        (Join-Path $LauncherPath "AutoSpeechJournal.CLI.exe")
    )) {
        if (-not (Test-Path -LiteralPath $Required)) {
            throw "Installer input is missing: $Required"
        }
    }

    if (-not (Test-Path -LiteralPath $GeneratedIcon)) {
        New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
        New-AppIcon $GeneratedIcon
    }
    $ManifestStage = Join-Path $BuildRoot "installer-manifests"
    Reset-OutputDirectory $ManifestStage
    Copy-Item -LiteralPath $ModelManifestPath `
        -Destination (Join-Path $ManifestStage "runtime-models-v1.json")
    Copy-Item -LiteralPath $CudaManifest -Destination (Join-Path $ManifestStage "cuda-runtime-v1.json")

    $SetupOutput = Join-Path $OutputRoot "setup"
    Reset-OutputDirectory $SetupOutput
    $PayloadBytes = Get-DirectoryBytes $AppPayloadPath
    $ModelDownloadBytes = Get-ManifestTotal $ModelManifestPath "size"
    # Direct files are atomically moved from .part staging; no extracted second copy is created.
    $ModelInstalledBytes = [Int64]0
    $GpuDownloadBytes = Get-ManifestTotal $CudaManifest "size"
    $GpuInstalledBytes = Get-ManifestTotal $CudaManifest "installed_size"
    $Inno = Resolve-InnoCompiler $InnoCompilerPath
    $DetectedInnoVersion = Get-InnoCompilerVersion $Inno
    if ($ReleaseBuild -and $DetectedInnoVersion -ne $PinnedInnoVersion) {
        throw "Release builds require Inno Setup $PinnedInnoVersion; found $DetectedInnoVersion"
    }
    $InnoArguments = @(
        "/Qp",
        "/DAppVersion=$Version",
        "/DAppNumericVersion=$NumericVersion",
        "/DAppPayloadRoot=$AppPayloadPath",
        "/DLauncherRoot=$LauncherPath",
        "/DManifestRoot=$ManifestStage",
        "/DOutputDir=$SetupOutput",
        "/DAppIcon=$GeneratedIcon",
        "/DPayloadInstalledBytes=$PayloadBytes",
        "/DModelDownloadBytes=$ModelDownloadBytes",
        "/DModelInstalledBytes=$ModelInstalledBytes",
        "/DGpuDownloadBytes=$GpuDownloadBytes",
        "/DGpuInstalledBytes=$GpuInstalledBytes",
        (Join-Path $RepositoryRoot "packaging\windows\AutoSpeechJournal.iss")
    )
    & $Inno @InnoArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup compilation failed"
    }
    $SetupPath = Join-Path $SetupOutput "AutoSpeechJournal-Setup-$Version-x64.exe"
    if (-not (Test-Path -LiteralPath $SetupPath)) {
        throw "Inno Setup did not produce $SetupPath"
    }
}

$Metadata = [ordered]@{
    schema_version = 2
    version = $Version
    numeric_version = $NumericVersion
    stage = $Stage
    release_build = [bool]$ReleaseBuild
    pyinstaller_version = "6.16.0"
    sbom_generator = "uv export cyclonedx1.5"
    inno_setup_version = $DetectedInnoVersion
    required_release_inno_setup_version = $PinnedInnoVersion
}
$MetadataPath = Join-Path $OutputRoot "build-metadata.json"
Write-Utf8NoBom $MetadataPath (($Metadata | ConvertTo-Json -Depth 4) + "`n")
Write-Host "Windows packaging stage '$Stage' completed for Auto Speech Journal $Version"
Write-Host "Artifacts: $OutputRoot"
