[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$LegacyAppRoot,
    [Parameter(Mandatory = $true)]
    [string]$StableCli,
    [Parameter(Mandatory = $true)]
    [string]$MarkerPath,
    [string]$SchtasksPath = (Join-Path $env:WINDIR "System32\schtasks.exe")
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
$Detail = "No legacy sign-in task is registered."
$Migrated = $false
$StartupPreserved = $false
$LegacyTaskRetained = $false
$ReplacementTaskEnabled = $false
$ManualStartRequired = $false

function Invoke-NativeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $PreviousPreference = $ErrorActionPreference
    $Lines = @()
    $ExitCode = -1
    try {
        # Windows PowerShell promotes native stderr to ErrorRecord. Keep expected
        # schtasks exit codes local instead of letting ErrorActionPreference=Stop abort Setup.
        $ErrorActionPreference = "Continue"
        $Lines = @(& $FilePath @Arguments 2>&1 | ForEach-Object { [string]$_ })
        $ExitCode = $LASTEXITCODE
        if ($null -eq $ExitCode) {
            $ExitCode = -1
        }
    }
    catch {
        $Lines = @([string]$_.Exception.Message)
        $ExitCode = -1
    }
    finally {
        $ErrorActionPreference = $PreviousPreference
    }
    return [pscustomobject]@{
        exit_code = [int]$ExitCode
        lines = [string[]]$Lines
    }
}

function Test-SchedulerAvailable {
    $Probe = Invoke-NativeCommand -FilePath $SchtasksPath -Arguments @(
        "/Query", "/FO", "LIST"
    )
    return $Probe.exit_code -eq 0
}

function Get-TaskQuery([string]$TaskName) {
    $Query = Invoke-NativeCommand -FilePath $SchtasksPath -Arguments @(
        "/Query", "/TN", $TaskName, "/XML"
    )
    $Found = $Query.exit_code -eq 0 -and $Query.lines.Count -gt 0
    $Available = $Found -or (Test-SchedulerAvailable)
    return [pscustomobject]@{
        found = $Found
        available = $Available
        lines = [string[]]$Query.lines
        detail = ($Query.lines -join "`n").Trim()
    }
}

function Test-StableTaskXml {
    param([Parameter(Mandatory = $true)][string[]]$XmlLines)

    try {
        [xml]$TaskXml = $XmlLines -join "`n"
        $Description = [string]$TaskXml.Task.RegistrationInfo.Description
        $Uri = [string]$TaskXml.Task.RegistrationInfo.URI
        $Command = [string]$TaskXml.Task.Actions.Exec.Command
        $ResolvedCommand = [IO.Path]::GetFullPath(
            [Environment]::ExpandEnvironmentVariables($Command.Trim('"'))
        )
        return (
            $Description -eq $OwnershipMarker -and
            $Uri -eq $StableTaskName -and
            $ResolvedCommand.Equals($StableGui, [StringComparison]::OrdinalIgnoreCase)
        )
    }
    catch {
        return $false
    }
}

function Test-StableTaskEnabledXml {
    param([Parameter(Mandatory = $true)][string[]]$XmlLines)

    if (-not (Test-StableTaskXml -XmlLines $XmlLines)) {
        return $false
    }
    try {
        [xml]$TaskXml = $XmlLines -join "`n"
        $Enabled = [string]$TaskXml.Task.Settings.Enabled
        return -not $Enabled.Equals("false", [StringComparison]::OrdinalIgnoreCase)
    }
    catch {
        return $false
    }
}

function Test-LegacyTaskXml {
    param([Parameter(Mandatory = $true)][string[]]$XmlLines)

    try {
        [xml]$TaskXml = $XmlLines -join "`n"
        $Command = [string]$TaskXml.Task.Actions.Exec.Command
        $Arguments = [string]$TaskXml.Task.Actions.Exec.Arguments
        $ResolvedCommand = [IO.Path]::GetFullPath(
            [Environment]::ExpandEnvironmentVariables($Command.Trim('"'))
        )
        return (
            $ResolvedCommand.Equals($LegacyPythonw, [StringComparison]::OrdinalIgnoreCase) -and
            $Arguments -match '(?i)-m\s+auto_speech_journal\s+run(?:\s|$)'
        )
    }
    catch {
        return $false
    }
}

$LegacyQuery = Get-TaskQuery -TaskName $LegacyTaskName
if (-not $LegacyQuery.found) {
    if ($LegacyQuery.available) {
        $Status = "no_task"
    }
    else {
        $Status = "manual_start_scheduler_unavailable"
        $Detail = "Task Scheduler is unavailable; any existing legacy task was left untouched."
        $ManualStartRequired = $true
        $LegacyTaskRetained = $true
    }
}
elseif (-not (Test-LegacyTaskXml -XmlLines $LegacyQuery.lines)) {
    $Status = "foreign_task_preserved"
    $Detail = "The legacy task name is not owned by this installation and was left untouched."
    $LegacyTaskRetained = $true
}
else {
    $LegacyTaskRetained = $true
    $StableQuery = Get-TaskQuery -TaskName $StableTaskName
    if ($StableQuery.found -and -not (Test-StableTaskXml -XmlLines $StableQuery.lines)) {
        $Status = "foreign_replacement_task_preserved"
        $Detail = "The replacement task name is not owned by this installation; both tasks were preserved."
        $ManualStartRequired = $true
    }
    else {
        if (
            $StableQuery.found -and
            (Test-StableTaskEnabledXml -XmlLines $StableQuery.lines)
        ) {
            $ReplacementTaskEnabled = $true
        }
        else {
            $Enable = Invoke-NativeCommand -FilePath $StableCli -Arguments @(
                "startup", "enable"
            )
            if ($Enable.exit_code -ne 0) {
                $Status = "manual_start_enable_failed"
                $Detail = "The replacement task could not be enabled; the legacy task was preserved."
                $ManualStartRequired = $true
            }
            else {
                $StableQuery = Get-TaskQuery -TaskName $StableTaskName
                if (
                    $StableQuery.found -and
                    (Test-StableTaskEnabledXml -XmlLines $StableQuery.lines)
                ) {
                    $ReplacementTaskEnabled = $true
                }
                else {
                    $Status = "manual_start_enable_unverified"
                    $Detail = "The replacement task could not be verified; the legacy task was preserved."
                    $ManualStartRequired = $true
                }
            }
        }

        if ($ReplacementTaskEnabled) {
            # Re-query immediately before deletion. Only the exact legacy action owned by
            # this installation may be removed; a raced or foreign task is never touched.
            $LegacyBeforeDelete = Get-TaskQuery -TaskName $LegacyTaskName
            if (-not $LegacyBeforeDelete.found) {
                $Status = "migrated"
                $Detail = "The stable launcher task is active and the legacy task is absent."
                $Migrated = $true
                $StartupPreserved = $true
                $LegacyTaskRetained = $false
            }
            elseif (-not (Test-LegacyTaskXml -XmlLines $LegacyBeforeDelete.lines)) {
                $Status = "legacy_task_changed_preserved"
                $Detail = "The legacy task changed during migration and was left untouched."
                $StartupPreserved = $true
                $LegacyTaskRetained = $true
            }
            else {
                $Delete = Invoke-NativeCommand -FilePath $SchtasksPath -Arguments @(
                    "/Delete", "/TN", $LegacyTaskName, "/F"
                )
                if ($Delete.exit_code -eq 0) {
                    $Status = "migrated"
                    $Detail = "The verified legacy task was replaced by the stable launcher task."
                    $Migrated = $true
                    $StartupPreserved = $true
                    $LegacyTaskRetained = $false
                }
                else {
                    $Status = "legacy_task_delete_failed_preserved"
                    $Detail = "The stable task is active, but the legacy task could not be removed."
                    $StartupPreserved = $true
                    $LegacyTaskRetained = $true
                }
            }
        }
    }
}

$Marker = [ordered]@{
    schema_version = 1
    legacy_app_root = [IO.Path]::GetFullPath($LegacyAppRoot)
    legacy_app_retained = $true
    legacy_task = $LegacyTaskName
    legacy_task_status = $Status
    legacy_task_retained = $LegacyTaskRetained
    replacement_task = $StableTaskName
    replacement_task_enabled = $ReplacementTaskEnabled
    startup_preserved = $StartupPreserved
    manual_start_required = $ManualStartRequired
    migrated = $Migrated
    detail = $Detail
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
    Remove-Item -LiteralPath $MarkerTemp -Force -ErrorAction SilentlyContinue
    throw
}
