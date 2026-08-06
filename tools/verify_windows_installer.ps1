[CmdletBinding()]
param(
    [string]$ArtifactRoot,
    [string]$AppPayloadPath,
    [string]$LauncherPath,
    [string]$UnsignedAppPayloadPath,
    [string]$UnsignedLauncherPath,
    [string]$UnsignedSetupPath,
    [string]$SetupPath,
    [string]$ExpectedPublisher = "SignPath Foundation",
    [switch]$AllowUnsigned,
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

function Get-SignerInfoSigningTimeUtc($SignerInfo) {
    foreach ($Attribute in @($SignerInfo.SignedAttributes)) {
        if ($Attribute.Oid.Value -ne "1.2.840.113549.1.9.5") {
            continue
        }
        foreach ($Value in @($Attribute.Values)) {
            $SigningTime = New-Object Security.Cryptography.Pkcs.Pkcs9SigningTime(
                ,$Value.RawData
            )
            return $SigningTime.SigningTime.ToUniversalTime()
        }
    }
    return $null
}

function Get-EmbeddedAuthenticodeTimestampUtc([string]$Path) {
    Add-Type -AssemblyName System.Security
    $Bytes = [IO.File]::ReadAllBytes([IO.Path]::GetFullPath($Path))
    $PeOffset = [int][BitConverter]::ToUInt32($Bytes, 0x3c)
    $OptionalOffset = $PeOffset + 24
    $Magic = [BitConverter]::ToUInt16($Bytes, $OptionalOffset)
    $DataDirectoryOffset = $OptionalOffset + $(if ($Magic -eq 0x020b) { 112 } else { 96 })
    $CertificateOffset = [int][BitConverter]::ToUInt32($Bytes, $DataDirectoryOffset + 32)
    $CertificateSize = [int][BitConverter]::ToUInt32($Bytes, $DataDirectoryOffset + 36)
    if ($CertificateOffset -le 0 -or $CertificateSize -lt 8) {
        throw "The Authenticode signature is not embedded in $Path"
    }
    $Cursor = $CertificateOffset
    $CertificateEnd = $CertificateOffset + $CertificateSize
    while ($Cursor + 8 -le $CertificateEnd) {
        $EntryLength = [int][BitConverter]::ToUInt32($Bytes, $Cursor)
        $CertificateType = [BitConverter]::ToUInt16($Bytes, $Cursor + 6)
        if ($EntryLength -lt 8 -or $Cursor + $EntryLength -gt $CertificateEnd) {
            throw "Invalid WIN_CERTIFICATE entry in $Path"
        }
        if ($CertificateType -eq 2) {
            $Pkcs7 = New-Object byte[] ($EntryLength - 8)
            [Array]::Copy($Bytes, $Cursor + 8, $Pkcs7, 0, $EntryLength - 8)
            $SignedCms = New-Object Security.Cryptography.Pkcs.SignedCms
            $SignedCms.Decode($Pkcs7)
            foreach ($SignerInfo in @($SignedCms.SignerInfos)) {
                foreach ($CounterSigner in @($SignerInfo.CounterSignerInfos)) {
                    $SigningTime = Get-SignerInfoSigningTimeUtc $CounterSigner
                    if ($null -ne $SigningTime) {
                        return $SigningTime
                    }
                }
                foreach ($Attribute in @($SignerInfo.UnsignedAttributes)) {
                    if ($Attribute.Oid.Value -ne "1.3.6.1.4.1.311.3.3.1") {
                        continue
                    }
                    foreach ($Value in @($Attribute.Values)) {
                        $TimestampCms = New-Object Security.Cryptography.Pkcs.SignedCms
                        $TimestampCms.Decode($Value.RawData)
                        foreach ($TimestampSigner in @($TimestampCms.SignerInfos)) {
                            $SigningTime = Get-SignerInfoSigningTimeUtc $TimestampSigner
                            if ($null -ne $SigningTime) {
                                return $SigningTime
                            }
                        }
                    }
                }
            }
        }
        $Cursor += (($EntryLength + 7) -band (-bnot 7))
    }
    throw "The Authenticode timestamp time could not be decoded from $Path"
}

function Test-AuthenticodeFile([string]$Path) {
    Assert-File $Path
    $Signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($AllowUnsigned -and $Signature.Status -eq "NotSigned") {
        return [ordered]@{
            status = [string]$Signature.Status
            signer = $null
            timestamp = $null
        }
    }
    if ($Signature.Status -ne "Valid") {
        throw "A public release artifact is not validly signed: $Path ($($Signature.Status))"
    }
    if ($null -eq $Signature.SignerCertificate) {
        throw "The Authenticode signature has no signer certificate: $Path"
    }
    $SignerSimpleName = $Signature.SignerCertificate.GetNameInfo(
        [Security.Cryptography.X509Certificates.X509NameType]::SimpleName,
        $false
    )
    if (-not $SignerSimpleName.Equals($ExpectedPublisher, [StringComparison]::Ordinal)) {
        throw "Unexpected Authenticode publisher for $Path`: '$SignerSimpleName'"
    }
    if ($null -eq $Signature.TimeStamperCertificate) {
        throw "The Authenticode signature has no trusted timestamp: $Path"
    }
    $TimestampReceipt = Get-CertificateReceipt $Signature.TimeStamperCertificate
    $TimestampReceipt["signed_at_utc"] = (
        Get-EmbeddedAuthenticodeTimestampUtc $Path
    ).ToString("o")
    return [ordered]@{
        status = [string]$Signature.Status
        signer = Get-CertificateReceipt $Signature.SignerCertificate
        timestamp = $TimestampReceipt
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

function Get-AuthenticodeNormalizedSha256([string]$Path) {
    Assert-File $Path
    $Bytes = [IO.File]::ReadAllBytes([IO.Path]::GetFullPath($Path))
    if ($Bytes.Length -lt 64) {
        throw "Cannot normalize a non-PE file: $Path"
    }
    $PeOffset = [Int64][BitConverter]::ToUInt32($Bytes, 0x3c)
    if ($PeOffset -lt 0 -or $PeOffset + 24 -gt $Bytes.Length) {
        throw "Invalid PE header offset in $Path"
    }
    if ([BitConverter]::ToUInt32($Bytes, [int]$PeOffset) -ne 0x00004550) {
        throw "Missing PE signature in $Path"
    }
    $OptionalSize = [int][BitConverter]::ToUInt16($Bytes, [int]$PeOffset + 20)
    $OptionalOffset = $PeOffset + 24
    if ($OptionalSize -lt 120 -or $OptionalOffset + $OptionalSize -gt $Bytes.Length) {
        throw "Invalid PE optional header in $Path"
    }
    $Magic = [BitConverter]::ToUInt16($Bytes, [int]$OptionalOffset)
    if ($Magic -eq 0x010b) {
        $DataDirectoryOffset = $OptionalOffset + 96
    }
    elseif ($Magic -eq 0x020b) {
        $DataDirectoryOffset = $OptionalOffset + 112
    }
    else {
        throw "Unsupported PE optional-header magic in $Path"
    }
    $ChecksumOffset = $OptionalOffset + 64
    $SecurityDirectoryOffset = $DataDirectoryOffset + 32
    if ($SecurityDirectoryOffset + 8 -gt $OptionalOffset + $OptionalSize) {
        throw "PE security directory is outside the optional header in $Path"
    }
    $CertificateOffset = [Int64][BitConverter]::ToUInt32(
        $Bytes,
        [int]$SecurityDirectoryOffset
    )
    $CertificateSize = [Int64][BitConverter]::ToUInt32(
        $Bytes,
        [int]$SecurityDirectoryOffset + 4
    )
    foreach ($Index in 0..3) {
        $Bytes[[int]$ChecksumOffset + $Index] = 0
    }
    foreach ($Index in 0..7) {
        $Bytes[[int]$SecurityDirectoryOffset + $Index] = 0
    }
    if (($CertificateOffset -eq 0) -xor ($CertificateSize -eq 0)) {
        throw "Inconsistent PE certificate table in $Path"
    }
    if ($CertificateSize -gt 0) {
        if (
            $CertificateOffset -lt 8 -or
            ($CertificateOffset % 8) -ne 0 -or
            $CertificateSize -lt 8 -or
            $CertificateOffset + $CertificateSize -gt $Bytes.Length
        ) {
            throw "Invalid PE certificate table range in $Path"
        }
    }
    $Hasher = [Security.Cryptography.SHA256]::Create()
    $CryptoStream = New-Object Security.Cryptography.CryptoStream(
        [IO.Stream]::Null,
        $Hasher,
        [Security.Cryptography.CryptoStreamMode]::Write
    )
    try {
        if ($CertificateSize -eq 0) {
            $CryptoStream.Write($Bytes, 0, $Bytes.Length)
        }
        else {
            $CryptoStream.Write($Bytes, 0, [int]$CertificateOffset)
            $AfterOffset = [int]($CertificateOffset + $CertificateSize)
            $AfterLength = $Bytes.Length - $AfterOffset
            if ($AfterLength -gt 0) {
                $CryptoStream.Write($Bytes, $AfterOffset, $AfterLength)
            }
        }
        $CryptoStream.FlushFinalBlock()
        return ([BitConverter]::ToString($Hasher.Hash)).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $CryptoStream.Dispose()
        $Hasher.Dispose()
    }
}

function Assert-NormalizedPeInvariant([string]$UnsignedPath, [string]$SignedPath) {
    $Before = Get-AuthenticodeNormalizedSha256 $UnsignedPath
    $After = Get-AuthenticodeNormalizedSha256 $SignedPath
    if ($Before -ne $After) {
        throw "Signing changed Authenticode-covered PE content: $SignedPath"
    }
    return [ordered]@{
        unsigned_sha256 = $Before
        signed_sha256 = $After
    }
}

function Assert-SignedTreeInvariant(
    [string]$UnsignedRoot,
    [string]$SignedRoot,
    [string[]]$SignableFiles
) {
    $Unsigned = Get-RelativeFileMap $UnsignedRoot
    $Signed = Get-RelativeFileMap $SignedRoot
    if ($Unsigned.Count -ne $Signed.Count) {
        throw "SignPath changed the application file count for $SignedRoot"
    }
    $Signable = @{}
    foreach ($Relative in $SignableFiles) {
        $Signable[$Relative.Replace('\', '/').Normalize().ToLowerInvariant()] = $true
    }
    $NormalizedDigests = [ordered]@{}
    foreach ($Relative in @($Unsigned.Keys | Sort-Object)) {
        if (-not $Signed.ContainsKey($Relative)) {
            throw "SignPath output is missing $Relative"
        }
        if ($Signable.ContainsKey($Relative)) {
            $NormalizedDigests[$Relative] = Assert-NormalizedPeInvariant `
                $Unsigned[$Relative].FullName `
                $Signed[$Relative].FullName
        }
        else {
            $Before = (Get-FileHash -LiteralPath $Unsigned[$Relative].FullName -Algorithm SHA256).Hash
            $After = (Get-FileHash -LiteralPath $Signed[$Relative].FullName -Algorithm SHA256).Hash
            if ($Before -ne $After) {
                throw "SignPath unexpectedly changed non-signable payload file $Relative"
            }
        }
    }
    foreach ($Relative in $Signable.Keys) {
        if (-not $Unsigned.ContainsKey($Relative)) {
            throw "Unsigned signing input is missing expected executable $Relative"
        }
    }
    return $NormalizedDigests
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

$NormalizedSigningDigests = [ordered]@{}
if (-not $AllowUnsigned) {
    if ([string]::IsNullOrWhiteSpace($UnsignedAppPayloadPath)) {
        throw "Signed release verification requires -UnsignedAppPayloadPath"
    }
    if ([string]::IsNullOrWhiteSpace($UnsignedLauncherPath)) {
        throw "Signed release verification requires -UnsignedLauncherPath"
    }
    if (-not $SkipSetup -and [string]::IsNullOrWhiteSpace($UnsignedSetupPath)) {
        throw "Signed release verification requires -UnsignedSetupPath"
    }
}
if (-not [string]::IsNullOrWhiteSpace($UnsignedAppPayloadPath)) {
    $NormalizedSigningDigests.payload = Assert-SignedTreeInvariant `
        ([IO.Path]::GetFullPath($UnsignedAppPayloadPath)) `
        ([IO.Path]::GetFullPath($AppPayloadPath)) `
        @('AutoSpeechJournal.exe', 'AutoSpeechJournal.CLI.exe')
}
if (-not [string]::IsNullOrWhiteSpace($UnsignedLauncherPath)) {
    $NormalizedSigningDigests.launchers = Assert-SignedTreeInvariant `
        ([IO.Path]::GetFullPath($UnsignedLauncherPath)) `
        ([IO.Path]::GetFullPath($LauncherPath)) `
        @('AutoSpeechJournal.exe', 'AutoSpeechJournal.CLI.exe')
}
if (-not $SkipSetup) {
    if (-not [string]::IsNullOrWhiteSpace($UnsignedSetupPath)) {
        $NormalizedSigningDigests.setup = Assert-NormalizedPeInvariant `
            ([IO.Path]::GetFullPath($UnsignedSetupPath)) `
            $SetupPath
    }
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

$SignatureResults = [ordered]@{}
$VersionResults = [ordered]@{}
foreach ($Executable in $RequiredExecutables) {
    $SignatureResults[$Executable.Label] = Test-AuthenticodeFile $Executable.Path
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
    schema_version = 2
    project_version = $Version
    verified_at_utc = [DateTime]::UtcNow.ToString("o")
    allow_unsigned = [bool]$AllowUnsigned
    payload_file_count = $PayloadFiles.Count
    payload_tree_sha256 = $PayloadTreeSha256
    launcher_tree_sha256 = $LauncherTreeSha256
    runtime_inventory_validation = $RuntimeInventoryResult
    signatures = $SignatureResults
    version_resources = $VersionResults
    authenticode_normalized_invariants = $NormalizedSigningDigests
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
