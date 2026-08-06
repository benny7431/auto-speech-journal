[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$LegacyAppRoot,
    [Parameter(Mandatory = $true)]
    [string]$StableCli,
    [Parameter(Mandatory = $true)]
    [string]$MarkerPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$LegacyTaskName = "\Auto Speech Journal"
$StableTaskName = "\AutoSpeechJournal\Auto Speech Journal"
$OwnershipMarker = "AutoSpeechJournal-owned:v1"
$LegacyPythonw = [IO.Path]::GetFullPath(
    (Join-Path $LegacyAppRoot ".venv\Scripts\pythonw.exe")
)
$StableGui = [IO.Path]::GetFullPath(
    (Join-Path ([IO.Path]::GetDirectoryName($StableCli)) "AutoSpeechJournal.exe")
)
$Status = "no_task"
$Migrated = $false
$TaskBackup = $null
$LegacyTaskRemoved = $false
$StableTaskEnabled = $false

function Get-TaskXmlLines([string]$TaskName) {
    $Lines = @(& "$env:WINDIR\System32\schtasks.exe" /Query /TN $TaskName /XML 2>$null)
    if ($LASTEXITCODE -ne 0 -or $Lines.Count -eq 0) {
        return $null
    }
    return ,$Lines
}

function Test-StableTaskXml {
    param([Parameter(Mandatory = $true)][string[]]$XmlLines)

    [xml]$TaskXml = $XmlLines -join "`n"
    $Description = [string]$TaskXml.Task.RegistrationInfo.Description
    $Uri = [string]$TaskXml.Task.RegistrationInfo.URI
    $Command = [string]$TaskXml.Task.Actions.Exec.Command
    $ResolvedCommand = $null
    try {
        $ResolvedCommand = [IO.Path]::GetFullPath(
            [Environment]::ExpandEnvironmentVariables($Command.Trim('"'))
        )
    }
    catch {
        $ResolvedCommand = $null
    }
    return (
        $Description -eq $OwnershipMarker -and
        $Uri -eq $StableTaskName -and
        $null -ne $ResolvedCommand -and
        $ResolvedCommand.Equals($StableGui, [StringComparison]::OrdinalIgnoreCase)
    )
}

function Test-LegacyTaskXml {
    param([Parameter(Mandatory = $true)][string[]]$XmlLines)

    [xml]$TaskXml = $XmlLines -join "`n"
    $Command = [string]$TaskXml.Task.Actions.Exec.Command
    $Arguments = [string]$TaskXml.Task.Actions.Exec.Arguments
    $ResolvedCommand = $null
    try {
        $ResolvedCommand = [IO.Path]::GetFullPath(
            [Environment]::ExpandEnvironmentVariables($Command.Trim('"'))
        )
    }
    catch {
        $ResolvedCommand = $null
    }
    return (
        $null -ne $ResolvedCommand -and
        $ResolvedCommand.Equals($LegacyPythonw, [StringComparison]::OrdinalIgnoreCase) -and
        $Arguments -match '(?i)-m\s+auto_speech_journal\s+run(?:\s|$)'
    )
}

function Restore-LegacyTask {
    if ($StableTaskEnabled) {
        & $StableCli startup disable | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to remove the replacement startup task before restoring the legacy task"
        }
        $script:StableTaskEnabled = $false
    }
    elseif ($LegacyTaskRemoved) {
        & "$env:WINDIR\System32\schtasks.exe" /Delete /TN $LegacyTaskName /F 2>$null |
            Out-Null
    }

    & "$env:WINDIR\System32\schtasks.exe" /Create /TN $LegacyTaskName /XML $TaskBackup /F |
        Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to restore the verified legacy Auto Speech Journal task"
    }
    $RestoredXml = @(Get-TaskXmlLines -TaskName $LegacyTaskName)
    if ($RestoredXml.Count -eq 0 -or -not (Test-LegacyTaskXml -XmlLines $RestoredXml)) {
        throw "The legacy task restoration command completed, but verification failed"
    }
    $script:LegacyTaskRemoved = $false
}

$XmlLines = @(Get-TaskXmlLines -TaskName $LegacyTaskName)
if ($XmlLines.Count -gt 0) {
    $OwnedLegacyAction = Test-LegacyTaskXml -XmlLines $XmlLines
    if ($OwnedLegacyAction) {
        $TaskBackup = Join-Path $env:TEMP "auto-speech-journal-legacy-task-$PID.xml"
        [IO.File]::WriteAllText(
            $TaskBackup,
            ($XmlLines -join "`n"),
            (New-Object System.Text.UnicodeEncoding($false, $true))
        )
        & "$env:WINDIR\System32\schtasks.exe" /Delete /TN $LegacyTaskName /F | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to remove the verified legacy Auto Speech Journal task"
        }
        $LegacyTaskRemoved = $true
        try {
            & $StableCli startup enable
            if ($LASTEXITCODE -ne 0) {
                throw "The stable-launcher task could not be enabled"
            }
            $StableTaskEnabled = $true
            $ReplacementXml = @(Get-TaskXmlLines -TaskName $StableTaskName)
            if (
                $ReplacementXml.Count -eq 0 -or
                -not (Test-StableTaskXml -XmlLines $ReplacementXml)
            ) {
                throw "The stable-launcher task enable command completed, but verification failed"
            }
            $Status = "migrated"
            $Migrated = $true
        }
        catch {
            $MigrationError = $_
            Restore-LegacyTask
            throw $MigrationError
        }
    }
    else {
        $Status = "foreign_task_preserved"
    }
}

$Marker = [ordered]@{
    schema_version = 1
    legacy_app_root = [IO.Path]::GetFullPath($LegacyAppRoot)
    legacy_app_retained = $true
    legacy_task = $LegacyTaskName
    legacy_task_status = $Status
    replacement_task = $StableTaskName
    startup_preserved = $Migrated
    migrated_at_utc = [DateTime]::UtcNow.ToString("o")
}
$Utf8 = New-Object System.Text.UTF8Encoding($false)
$MarkerTemp = "$MarkerPath.tmp-$PID"
try {
    [IO.File]::WriteAllText(
        $MarkerTemp,
        (($Marker | ConvertTo-Json -Depth 4) + "`n"),
        $Utf8
    )
    Move-Item -LiteralPath $MarkerTemp -Destination $MarkerPath -Force
    if (-not (Test-Path -LiteralPath $MarkerPath -PathType Leaf)) {
        throw "Migration marker activation completed, but verification failed"
    }
}
catch {
    $MarkerError = $_
    Remove-Item -LiteralPath $MarkerTemp -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $MarkerPath -Force -ErrorAction SilentlyContinue
    if ($Migrated -and $LegacyTaskRemoved) {
        Restore-LegacyTask
    }
    throw $MarkerError
}
finally {
    if ($null -ne $TaskBackup -and -not $LegacyTaskRemoved) {
        Remove-Item -LiteralPath $TaskBackup -Force -ErrorAction SilentlyContinue
    }
}

if ($null -ne $TaskBackup) {
    Remove-Item -LiteralPath $TaskBackup -Force -ErrorAction SilentlyContinue
}
