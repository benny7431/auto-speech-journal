[CmdletBinding()]
param(
    [string]$ArtifactRoot,
    [string]$AppPayloadPath,
    [string]$LauncherPath,
    [string]$SetupPath,
    [switch]$SkipSetup,
    [switch]$SkipRuntimeProbe
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    $ArtifactRoot = Join-Path $RepositoryRoot "artifacts\windows"
}
$ArtifactRoot = [IO.Path]::GetFullPath($ArtifactRoot)
if ([string]::IsNullOrWhiteSpace($AppPayloadPath)) {
    $AppPayloadPath = Join-Path $ArtifactRoot "application\payload"
}
if ([string]::IsNullOrWhiteSpace($LauncherPath)) {
    $LauncherPath = Join-Path $ArtifactRoot "application\launchers"
}
$AppPayloadPath = [IO.Path]::GetFullPath($AppPayloadPath)
$LauncherPath = [IO.Path]::GetFullPath($LauncherPath)

function Get-ProjectVersion {
    $Project = Get-Content -LiteralPath (Join-Path $RepositoryRoot "pyproject.toml") -Raw -Encoding UTF8
    $ProjectSection = [regex]::Match($Project, '(?ms)^\[project\]\s*(.*?)(?=^\[|\z)')
    $VersionMatch = [regex]::Match(
        $ProjectSection.Groups[1].Value,
        '(?m)^version\s*=\s*"([^"]+)"\s*$'
    )
    if (-not $VersionMatch.Success) {
        throw "Cannot read [project].version from pyproject.toml"
    }
    return $VersionMatch.Groups[1].Value
}

function Get-NumericVersion([string]$Version) {
    $Match = [regex]::Match($Version, '^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$')
    if (-not $Match.Success) {
        throw "Unsupported project version for Windows resources: $Version"
    }
    return "$($Match.Groups[1].Value).$($Match.Groups[2].Value).$($Match.Groups[3].Value).0"
}

function Assert-File([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required packaging artifact is missing: $Path"
    }
}

function Get-CertificateReceipt([Security.Cryptography.X509Certificates.X509Certificate2]$Certificate) {
    if ($null -eq $Certificate) {
        return $null
    }
    return [ordered]@{
        simple_name = $Certificate.GetNameInfo(
            [Security.Cryptography.X509Certificates.X509NameType]::SimpleName,
            $false
        )
        subject = $Certificate.Subject
        thumbprint = $Certificate.Thumbprint.ToLowerInvariant()
        not_before_utc = $Certificate.NotBefore.ToUniversalTime().ToString("o")
        not_after_utc = $Certificate.NotAfter.ToUniversalTime().ToString("o")
    }
}

function Get-AuthenticodeReceipt([string]$Path) {
    Assert-File $Path
    $Signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($Signature.Status -eq "NotSigned") {
        return [ordered]@{
            status = [string]$Signature.Status
            present = $false
            signer = $null
            timestamp = $null
        }
    }
    if ($Signature.Status -ne "Valid") {
        throw "Artifact contains an invalid Authenticode signature: $Path ($($Signature.Status))"
    }
    return [ordered]@{
        status = [string]$Signature.Status
        present = $true
        signer = Get-CertificateReceipt $Signature.SignerCertificate
        timestamp = Get-CertificateReceipt $Signature.TimeStamperCertificate
    }
}

function Get-NormalizedRelativePath([string]$Root, [string]$Path) {
    $ResolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $ResolvedPath = [IO.Path]::GetFullPath($Path)
    $Prefix = $ResolvedRoot + [IO.Path]::DirectorySeparatorChar
    if (-not $ResolvedPath.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "File is outside the expected payload root: $ResolvedPath"
    }
    return $ResolvedPath.Substring($Prefix.Length).Replace('\', '/').Normalize().ToLowerInvariant()
}

function Get-RelativeFileMap([string]$Root) {
    $ResolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    if (-not (Test-Path -LiteralPath $ResolvedRoot -PathType Container)) {
        throw "Required packaging directory is missing: $ResolvedRoot"
    }
    $Map = @{}
    foreach ($File in @(Get-ChildItem -LiteralPath $ResolvedRoot -Recurse -File)) {
        $Relative = Get-NormalizedRelativePath $ResolvedRoot $File.FullName
        if ($Map.ContainsKey($Relative)) {
            throw "Payload contains duplicate normalized path: $Relative"
        }
        $Map[$Relative] = $File
    }
    return $Map
}

function Get-TreeDigest([string]$Root) {
    $Map = Get-RelativeFileMap $Root
    $Lines = foreach ($Relative in @($Map.Keys | Sort-Object)) {
        $Hash = (Get-FileHash -LiteralPath $Map[$Relative].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$Hash  $($Relative.Replace('\', '/'))"
    }
    $Bytes = [Text.Encoding]::UTF8.GetBytes(($Lines -join "`n") + "`n")
    $Hasher = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($Hasher.ComputeHash($Bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $Hasher.Dispose()
    }
}

$Version = Get-ProjectVersion
$NumericVersion = Get-NumericVersion $Version
if ([string]::IsNullOrWhiteSpace($SetupPath) -and -not $SkipSetup) {
    $SetupPath = Join-Path $ArtifactRoot "setup\AutoSpeechJournal-Setup-$Version-x64.exe"
}
if (-not $SkipSetup) {
    $SetupPath = [IO.Path]::GetFullPath($SetupPath)
}

$PayloadGui = Join-Path $AppPayloadPath "AutoSpeechJournal.exe"
$PayloadCli = Join-Path $AppPayloadPath "AutoSpeechJournal.CLI.exe"
$LauncherGui = Join-Path $LauncherPath "AutoSpeechJournal.exe"
$LauncherCli = Join-Path $LauncherPath "AutoSpeechJournal.CLI.exe"
$RequiredExecutables = @(
    [pscustomobject]@{
        Label = "payload/AutoSpeechJournal.exe"
        Path = $PayloadGui
        OriginalFilename = "AutoSpeechJournal.exe"
        InternalName = "AutoSpeechJournal"
    },
    [pscustomobject]@{
        Label = "payload/AutoSpeechJournal.CLI.exe"
        Path = $PayloadCli
        OriginalFilename = "AutoSpeechJournal.CLI.exe"
        InternalName = "AutoSpeechJournal.CLI"
    },
    [pscustomobject]@{
        Label = "launcher/AutoSpeechJournal.exe"
        Path = $LauncherGui
        OriginalFilename = "AutoSpeechJournal.exe"
        InternalName = "AutoSpeechJournal.exe"
    },
    [pscustomobject]@{
        Label = "launcher/AutoSpeechJournal.CLI.exe"
        Path = $LauncherCli
        OriginalFilename = "AutoSpeechJournal.CLI.exe"
        InternalName = "AutoSpeechJournal.CLI.exe"
    }
)

if (-not $SkipSetup) {
    $RequiredExecutables += [pscustomobject]@{
        Label = "setup/AutoSpeechJournal-Setup-$Version-x64.exe"
        Path = $SetupPath
        OriginalFilename = $null
        InternalName = $null
    }
}

$ProhibitedPatterns = @(
    '(?i)(^|[\\/])torch([\\/]|$)',
    '(?i)(^|[\\/])transformers([\\/]|$)',
    '(?i)(^|[\\/])nvidia([\\/]|$)',
    '(?i)(^|[\\/])(cublas|cudnn|nvcuda|nvjitlink|nvrtc)[^\\/]*\.dll$',
    '(?i)(^|[\\/])(PIL|PyInstaller|coverage|pre_commit|pygments|pytest|pytest_cov|ruff)([\\/]|$)',
    '(?i)(^|[\\/])model\.bin$',
    '(?i)\.(?:onnx|safetensors|pt|pth)$',
    '(?i)(^|[\\/])spool([\\/]|$)',
    '(?i)(^|[\\/])assets[\\/]fonts([\\/]|$)',
    '(?i)\.(?:db|sqlite|flac|wav|log)$'
)
$PayloadFiles = @(Get-ChildItem -LiteralPath $AppPayloadPath -Recurse -File)
foreach ($File in $PayloadFiles) {
    $Relative = Get-NormalizedRelativePath $AppPayloadPath $File.FullName
    foreach ($Pattern in $ProhibitedPatterns) {
        if ($Relative -match $Pattern) {
            throw "Frozen application contains a prohibited runtime/build asset: $Relative"
        }
    }
}

$GuiHash = (Get-FileHash -LiteralPath $PayloadGui -Algorithm SHA256).Hash
$StableGuiHash = (Get-FileHash -LiteralPath $LauncherGui -Algorithm SHA256).Hash
if ($GuiHash -eq $StableGuiHash) {
    throw "The stable launcher is a copied version executable, not an independent launcher"
}
$CliHash = (Get-FileHash -LiteralPath $PayloadCli -Algorithm SHA256).Hash
$StableCliHash = (Get-FileHash -LiteralPath $LauncherCli -Algorithm SHA256).Hash
if ($CliHash -eq $StableCliHash) {
    throw "The stable CLI launcher is a copied version executable"
}

$AuthenticodeResults = [ordered]@{}
$VersionResults = [ordered]@{}
foreach ($Executable in $RequiredExecutables) {
    $AuthenticodeResults[$Executable.Label] = Get-AuthenticodeReceipt $Executable.Path
    $VersionInfo = [Diagnostics.FileVersionInfo]::GetVersionInfo($Executable.Path)
    $ActualFileVersion = $VersionInfo.FileVersionRaw.ToString()
    $ActualProductVersion = ([string]$VersionInfo.ProductVersion).Trim()
    $ActualOriginalFilename = ([string]$VersionInfo.OriginalFilename).Trim()
    $ActualInternalName = ([string]$VersionInfo.InternalName).Trim()
    if (-not $ActualFileVersion.Equals($NumericVersion, [StringComparison]::Ordinal)) {
        throw "File version does not exactly match pyproject.toml for $($Executable.Path)`: $ActualFileVersion"
    }
    if (-not [string]::Equals($ActualProductVersion, $Version, [StringComparison]::Ordinal)) {
        throw "Product version does not exactly match pyproject.toml for $($Executable.Path)`: $ActualProductVersion"
    }
    if ($null -ne $Executable.OriginalFilename) {
        if (-not [string]::Equals(
            $ActualOriginalFilename,
            $Executable.OriginalFilename,
            [StringComparison]::Ordinal
        )) {
            throw "OriginalFilename is incorrect for $($Executable.Path)`: $ActualOriginalFilename"
        }
        if (-not [string]::Equals(
            $ActualInternalName,
            $Executable.InternalName,
            [StringComparison]::Ordinal
        )) {
            throw "InternalName is incorrect for $($Executable.Path)`: $ActualInternalName"
        }
    }
    $VersionResults[$Executable.Label] = [ordered]@{
        file_version = $ActualFileVersion
        product_version = $ActualProductVersion
        original_filename = $ActualOriginalFilename
        internal_name = $ActualInternalName
    }
}

# Hash the frozen tree before executing it; Qt may keep diagnostics plugins mapped briefly.
$PayloadTreeSha256 = Get-TreeDigest $AppPayloadPath
$LauncherTreeSha256 = Get-TreeDigest $LauncherPath

if (-not $SkipRuntimeProbe) {
    $ProbeRoot = Join-Path $env:TEMP "asj-frozen-probe-$([Guid]::NewGuid().ToString('N'))"
    try {
        $env:LOCALAPPDATA = Join-Path $ProbeRoot "local-app-data"
        $env:USERPROFILE = Join-Path $ProbeRoot "profile"
        $env:HOME = $env:USERPROFILE
        $env:QT_QPA_PLATFORM = "offscreen"
        $env:QT_QUICK_BACKEND = "software"
        $env:QSG_RHI_BACKEND = "software"
        New-Item -ItemType Directory -Force -Path (Join-Path $env:USERPROFILE "Documents") | Out-Null
        & $PayloadCli installer-probe --isolated
        if ($LASTEXITCODE -ne 0) {
            throw "The frozen installer-probe failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        if (Test-Path -LiteralPath $ProbeRoot) {
            Remove-Item -LiteralPath $ProbeRoot -Recurse -Force
        }
    }
}

$SbomPath = Join-Path $ArtifactRoot "application\AutoSpeechJournal.cdx.json"
$FrozenInventoryPath = Join-Path $AppPayloadPath "frozen-runtime-inventory.json"
Assert-File $SbomPath
Assert-File $FrozenInventoryPath
$InventoryValidationLog = & uv run --no-sync python `
    (Join-Path $RepositoryRoot "packaging\windows\runtime_inventory.py") `
    validate `
    --sbom $SbomPath `
    --inventory $FrozenInventoryPath `
    --lock (Join-Path $RepositoryRoot "uv.lock") `
    --pyproject (Join-Path $RepositoryRoot "pyproject.toml") `
    --payload $AppPayloadPath 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Frozen runtime and CycloneDX SBOM validation failed: $($InventoryValidationLog -join [Environment]::NewLine)"
}
try {
    $RuntimeInventoryResult = (
        $InventoryValidationLog | Select-Object -Last 1
    ) | ConvertFrom-Json
}
catch {
    throw "Runtime inventory validator did not return JSON: $($_.Exception.Message)"
}

$Receipt = [ordered]@{
    schema_version = 3
    project_version = $Version
    verified_at_utc = [DateTime]::UtcNow.ToString("o")
    authenticode_policy = "optional_unsigned_allowed"
    payload_file_count = $PayloadFiles.Count
    payload_tree_sha256 = $PayloadTreeSha256
    launcher_tree_sha256 = $LauncherTreeSha256
    runtime_inventory_validation = $RuntimeInventoryResult
    authenticode = $AuthenticodeResults
    version_resources = $VersionResults
    files = [ordered]@{}
}
foreach ($Executable in $RequiredExecutables) {
    $Receipt.files[$Executable.Label] = [ordered]@{
        normalized_path = $Executable.Label.Replace('\', '/').Normalize()
        size = (Get-Item -LiteralPath $Executable.Path).Length
        sha256 = (Get-FileHash -LiteralPath $Executable.Path -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$ReceiptPath = Join-Path $ArtifactRoot "WINDOWS-VERIFICATION.json"
$Encoding = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText(
    $ReceiptPath,
    (($Receipt | ConvertTo-Json -Depth 8) + "`n"),
    $Encoding
)
Write-Host "Windows packaging verification passed for $Version"
Write-Host "Receipt: $ReceiptPath"
