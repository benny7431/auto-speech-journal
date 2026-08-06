[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

$TaskName = "Auto Speech Journal"
$TaskPath = "\"
$RuntimeRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "AutoSpeechJournal"))
$AppRoot = [IO.Path]::GetFullPath((Join-Path $RuntimeRoot "app"))
$MutexName = "Local\AutoSpeechJournal"
$Prefix = $RuntimeRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $AppRoot.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to remove a path outside $RuntimeRoot`: $AppRoot"
}

function Test-AppMutex {
    $Mutex = $null
    try {
        $Mutex = [Threading.Mutex]::OpenExisting($MutexName)
        return $true
    }
    catch [System.Threading.WaitHandleCannotBeOpenedException] {
        return $false
    }
    catch [System.UnauthorizedAccessException] {
        return $true
    }
    finally {
        if ($null -ne $Mutex) {
            $Mutex.Dispose()
        }
    }
}

function Get-AppProcessIds {
    $Processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $AppProcessIds = @(
        $Processes |
            Where-Object {
                $null -ne $_.ExecutablePath -and
                $_.ExecutablePath.StartsWith($AppRoot, [StringComparison]::OrdinalIgnoreCase)
            } |
            ForEach-Object { [int]$_.ProcessId }
    )
    do {
        $PreviousCount = $AppProcessIds.Count
        $Descendants = @(
            $Processes |
                Where-Object { $AppProcessIds -contains [int]$_.ParentProcessId } |
                ForEach-Object { [int]$_.ProcessId }
        )
        $AppProcessIds = @($AppProcessIds + $Descendants | Select-Object -Unique)
    } while ($AppProcessIds.Count -gt $PreviousCount)
    return $AppProcessIds
}

function Wait-AppStopped(
    [int]$TimeoutSeconds = 30,
    [int[]]$KnownProcessIds = @()
) {
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $CurrentTask = Get-ScheduledTask `
            -TaskPath $TaskPath `
            -TaskName $TaskName `
            -ErrorAction SilentlyContinue
        $TaskRunning = $null -ne $CurrentTask -and $CurrentTask.State -eq "Running"
        $CurrentProcessIds = @(Get-AppProcessIds)
        $KnownProcessRunning = @(
            $KnownProcessIds |
                Where-Object { $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue) }
        ).Count -gt 0
        if (
            -not $TaskRunning -and
            -not (Test-AppMutex) -and
            $CurrentProcessIds.Count -eq 0 -and
            -not $KnownProcessRunning
        ) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $Deadline)
    return $false
}

$Task = Get-ScheduledTask `
    -TaskPath $TaskPath `
    -TaskName $TaskName `
    -ErrorAction SilentlyContinue
$ExistingAppProcessIds = @(Get-AppProcessIds)
if ($null -ne $Task) {
    Stop-ScheduledTask `
        -TaskPath $TaskPath `
        -TaskName $TaskName `
        -ErrorAction SilentlyContinue
}
if (-not (Wait-AppStopped -KnownProcessIds $ExistingAppProcessIds)) {
    throw "程式未在 30 秒內停止。請先按小窗內的「結束程式」，再重新解除安裝。"
}
if ($null -ne $Task) {
    Unregister-ScheduledTask `
        -TaskPath $TaskPath `
        -TaskName $TaskName `
        -Confirm:$false
}

$ConfigFile = Join-Path $RuntimeRoot "config.json"
$RecordsRoot = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "語音紀錄"
if (Test-Path -LiteralPath $ConfigFile) {
    try {
        $Config = Get-Content -LiteralPath $ConfigFile -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($null -ne $Config.records_root -and $Config.records_root.Length -gt 0) {
            $RecordsRoot = [string]$Config.records_root
        }
    }
    catch {
        Write-Warning "無法讀取既有設定檔；仍會保留整個資料目錄。"
    }
}

if (Test-Path -LiteralPath $AppRoot) {
    try {
        Remove-Item -LiteralPath $AppRoot -Recurse -Force
    }
    catch {
        throw "無法移除程式目錄。請先按視窗內的「結束程式」再重試。`n$($_.Exception.Message)"
    }
}

Write-Host "已移除程式與排程工作。"
Write-Host "已保留語音紀錄：$RecordsRoot"
Write-Host "已保留設定、設定歷程、資料庫、模型、暫存、日誌、本機字體與聲明：$RuntimeRoot"
