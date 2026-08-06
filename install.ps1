[CmdletBinding()]
param(
    [switch]$NoCuda,
    [switch]$SkipModelDownload,
    [switch]$NoStart
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$env:PYTHONUTF8 = "1"

$InstallCuda = -not $NoCuda
$InstallModels = -not $SkipModelDownload

$TaskName = "Auto Speech Journal"
$TaskPath = "\"
$RuntimeRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA "AutoSpeechJournal"))
$AppRoot = [IO.Path]::GetFullPath((Join-Path $RuntimeRoot "app"))
$Token = [Guid]::NewGuid().ToString("N")
$StageRoot = [IO.Path]::GetFullPath((Join-Path $RuntimeRoot "app.new-$Token"))
$BackupRoot = [IO.Path]::GetFullPath((Join-Path $RuntimeRoot "app.old-$Token"))
$StateBackupRoot = [IO.Path]::GetFullPath((Join-Path $RuntimeRoot "install-state-$Token"))
$SourceRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$MutexName = "Local\AutoSpeechJournal"

function Assert-UnderRuntime([string]$Path) {
    $prefix = $RuntimeRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a path outside $RuntimeRoot`: $Path"
    }
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
        # An inaccessible named mutex still proves that an instance is alive.
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

Assert-UnderRuntime $AppRoot
Assert-UnderRuntime $StageRoot
Assert-UnderRuntime $BackupRoot
Assert-UnderRuntime $StateBackupRoot

$Uv = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $Uv) {
    throw "找不到 uv。請先安裝 uv，再重新執行 install.ps1。"
}

foreach ($Required in @(
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "src",
    "packaging\manifests\runtime-models-v1.json"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $SourceRoot $Required))) {
        throw "安裝來源缺少 $Required"
    }
}

$SceneManifestPath = Join-Path $SourceRoot "src\auto_speech_journal\assets\scenes\manifest.json"
if (-not (Test-Path -LiteralPath $SceneManifestPath)) {
    throw "安裝來源缺少離線場景 manifest"
}
$SceneManifest = Get-Content -LiteralPath $SceneManifestPath -Raw -Encoding UTF8 |
    ConvertFrom-Json
$IncompleteScenes = @($SceneManifest.assets | Where-Object { $_.status -ne "ready" })
if (
    $SceneManifest.schema_version -ne 2 -or
    $SceneManifest.asset_count -ne 192 -or
    $SceneManifest.assets.Count -ne 192 -or
    $IncompleteScenes.Count -ne 0
) {
    throw "離線場景圖包尚未完成；正式安裝需要 12 個月份 x 8 種狀態 x 2 種版型共 192 張 ready WebP"
}

$ExistingTask = Get-ScheduledTask `
    -TaskPath $TaskPath `
    -TaskName $TaskName `
    -ErrorAction SilentlyContinue
$ExistingTaskXml = $null
$ExistingTaskWasRunning = $null -ne $ExistingTask -and $ExistingTask.State -eq "Running"
if ($null -ne $ExistingTask) {
    $ExistingTaskXml = Export-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
}

New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
try {
    if (Test-Path -LiteralPath $StageRoot) {
        Remove-Item -LiteralPath $StageRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $StageRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $SourceRoot "pyproject.toml") -Destination $StageRoot
    Copy-Item -LiteralPath (Join-Path $SourceRoot "uv.lock") -Destination $StageRoot
    Copy-Item -LiteralPath (Join-Path $SourceRoot "README.md") -Destination $StageRoot
    Copy-Item -LiteralPath (Join-Path $SourceRoot "src") -Destination $StageRoot -Recurse
    $ManifestStage = Join-Path $StageRoot "manifests"
    New-Item -ItemType Directory -Path $ManifestStage | Out-Null
    Copy-Item `
        -LiteralPath (Join-Path $SourceRoot "packaging\manifests\runtime-models-v1.json") `
        -Destination $ManifestStage
    $LegacyPackagedFonts = Join-Path $StageRoot "src\auto_speech_journal\assets\fonts"
    Assert-UnderRuntime $LegacyPackagedFonts
    if (Test-Path -LiteralPath $LegacyPackagedFonts) {
        Remove-Item -LiteralPath $LegacyPackagedFonts -Recurse -Force
    }
}
catch {
    if (Test-Path -LiteralPath $StageRoot) {
        Remove-Item -LiteralPath $StageRoot -Recurse -Force
    }
    throw
}

$StateFiles = @("config.json", "state.db", "state.db-wal", "state.db-shm")

$Installed = $false
$PreviousAppMoved = $false
$NewAppPlaced = $false
$StateBackupCreated = $false
$HadPreviousInstall = Test-Path -LiteralPath $AppRoot
$TaskRegistered = $false
$ModelsReady = -not $InstallModels
try {
    $ExistingAppProcessIds = @(Get-AppProcessIds)
    if ($null -ne $ExistingTask) {
        Stop-ScheduledTask `
            -TaskPath $TaskPath `
            -TaskName $TaskName `
            -ErrorAction SilentlyContinue
    }
    if (-not (Wait-AppStopped -KnownProcessIds $ExistingAppProcessIds)) {
        throw "現有程式未在 30 秒內停止；請從小窗結束程式後再重試。"
    }
    New-Item -ItemType Directory -Path $StateBackupRoot | Out-Null
    foreach ($StateFile in $StateFiles) {
        $CurrentStatePath = Join-Path $RuntimeRoot $StateFile
        if (Test-Path -LiteralPath $CurrentStatePath) {
            Copy-Item -LiteralPath $CurrentStatePath -Destination $StateBackupRoot
        }
    }
    $StateBackupCreated = $true
    if ($HadPreviousInstall) {
        Move-Item -LiteralPath $AppRoot -Destination $BackupRoot
        $PreviousAppMoved = $true
    }
    Move-Item -LiteralPath $StageRoot -Destination $AppRoot
    $NewAppPlaced = $true

    Push-Location $AppRoot
    try {
        $SyncArguments = @("sync", "--no-editable", "--frozen")
        if ($InstallCuda) {
            $SyncArguments += @("--extra", "cuda")
        }
        & $Uv.Source @SyncArguments
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync 失敗，exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }

    $Python = Join-Path $AppRoot ".venv\Scripts\python.exe"
    $Pythonw = Join-Path $AppRoot ".venv\Scripts\pythonw.exe"
    if (-not (Test-Path -LiteralPath $Pythonw)) {
        throw "安裝後找不到 $Pythonw"
    }

    & $Python -X utf8 -m auto_speech_journal.scene_assets --strict
    if ($LASTEXITCODE -ne 0) {
        throw "離線場景資產驗證失敗，exit code $LASTEXITCODE"
    }

    if ($InstallModels) {
        $ModelManifestPath = Join-Path $AppRoot "manifests\runtime-models-v1.json"
        $ModelProgressPath = Join-Path $RuntimeRoot "provision-progress.json"
        & $Python -X utf8 -m auto_speech_journal repair models `
            --manifest $ModelManifestPath `
            --progress-json $ModelProgressPath
        if ($LASTEXITCODE -eq 0) {
            $ModelsReady = $true
        }
        else {
            Write-Warning (
                "模型尚未下載完成；程式安裝已保留。重新執行 repair models 即可從 .part 續傳。" +
                " exit code $LASTEXITCODE"
            )
        }
    }

    $SelfTestArguments = @(
        "-X", "utf8", "-m", "auto_speech_journal", "self-test",
        "--deep-model-check", "--no-microphone-check"
    )
    if (-not $ModelsReady) {
        $SelfTestArguments += "--no-model-check"
    }
    if (-not $InstallCuda) {
        $SelfTestArguments += "--allow-cpu-finalizer"
    }
    & $Python @SelfTestArguments
    if ($LASTEXITCODE -ne 0) {
        throw "安裝後自我檢查失敗，exit code $LASTEXITCODE"
    }

    $UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $Action = New-ScheduledTaskAction `
        -Execute $Pythonw `
        -Argument "-X utf8 -m auto_speech_journal run" `
        -WorkingDirectory $AppRoot
    $Trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
    $Trigger.Delay = "PT20S"
    $Principal = New-ScheduledTaskPrincipal `
        -UserId $UserId `
        -LogonType Interactive `
        -RunLevel Limited
    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 999 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew
    $Task = New-ScheduledTask `
        -Action $Action `
        -Trigger $Trigger `
        -Principal $Principal `
        -Settings $Settings `
        -Description "本機常駐語音轉錄；登入 20 秒後啟動。"
    Register-ScheduledTask `
        -TaskPath $TaskPath `
        -TaskName $TaskName `
        -InputObject $Task `
        -Force | Out-Null
    $TaskRegistered = $true

    if (-not $NoStart) {
        $LogFile = Join-Path $RuntimeRoot "logs\journal.log"
        $LogSignatureBefore = $null
        if (Test-Path -LiteralPath $LogFile) {
            $OldLog = Get-Item -LiteralPath $LogFile
            $LogSignatureBefore = "$($OldLog.Length):$($OldLog.LastWriteTimeUtc.Ticks)"
        }
        Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
        Start-Sleep -Seconds 3
        $StartedTask = Get-ScheduledTask `
            -TaskPath $TaskPath `
            -TaskName $TaskName `
            -ErrorAction Stop
        if ($StartedTask.State -ne "Running") {
            $TaskInfo = Get-ScheduledTaskInfo `
                -TaskPath $TaskPath `
                -TaskName $TaskName `
                -ErrorAction SilentlyContinue
            $LastResult = if ($null -ne $TaskInfo) { $TaskInfo.LastTaskResult } else { "unknown" }
            throw "排程啟動後未持續執行（state=$($StartedTask.State), result=$LastResult）"
        }
        if (-not (Test-Path -LiteralPath $LogFile)) {
            throw "排程已啟動，但未建立 runtime 日誌：$LogFile"
        }
        if (-not (Test-AppMutex)) {
            throw "排程顯示執行中，但應用程式 mutex 尚未建立。"
        }
        $NewLog = Get-Item -LiteralPath $LogFile
        $LogSignatureAfter = "$($NewLog.Length):$($NewLog.LastWriteTimeUtc.Ticks)"
        if ($null -ne $LogSignatureBefore -and $LogSignatureAfter -eq $LogSignatureBefore) {
            throw "排程啟動後 runtime 日誌沒有更新：$LogFile"
        }
    }
    $Installed = $true
}
finally {
    if (-not $Installed) {
        $RollbackAppProcessIds = @(Get-AppProcessIds)
        if ($TaskRegistered) {
            Stop-ScheduledTask `
                -TaskPath $TaskPath `
                -TaskName $TaskName `
                -ErrorAction SilentlyContinue
        }
        if (($TaskRegistered -or $NewAppPlaced -or $StateBackupCreated) -and
            -not (Wait-AppStopped -KnownProcessIds $RollbackAppProcessIds)) {
            throw "回復安裝前狀態前無法停止程式；為避免損壞資料，已保留備份。"
        }
        if ($TaskRegistered) {
            Unregister-ScheduledTask `
                -TaskPath $TaskPath `
                -TaskName $TaskName `
                -Confirm:$false `
                -ErrorAction SilentlyContinue
        }
        if ($NewAppPlaced -and (Test-Path -LiteralPath $AppRoot)) {
            Remove-Item -LiteralPath $AppRoot -Recurse -Force
        }
        if ($PreviousAppMoved -and (Test-Path -LiteralPath $BackupRoot)) {
            Move-Item -LiteralPath $BackupRoot -Destination $AppRoot
        }
        if ($StateBackupCreated) {
            foreach ($StateFile in $StateFiles) {
                $CurrentStatePath = Join-Path $RuntimeRoot $StateFile
                $BackupStatePath = Join-Path $StateBackupRoot $StateFile
                if (Test-Path -LiteralPath $BackupStatePath) {
                    Copy-Item -LiteralPath $BackupStatePath -Destination $CurrentStatePath -Force
                }
                elseif (Test-Path -LiteralPath $CurrentStatePath) {
                    Remove-Item -LiteralPath $CurrentStatePath -Force
                }
            }
        }
        if ($null -ne $ExistingTaskXml) {
            try {
                if ($TaskRegistered) {
                    Register-ScheduledTask `
                        -TaskPath $TaskPath `
                        -TaskName $TaskName `
                        -Xml $ExistingTaskXml `
                        -Force | Out-Null
                }
                if ($ExistingTaskWasRunning) {
                    Start-ScheduledTask -TaskPath $TaskPath -TaskName $TaskName
                }
            }
            catch {
                Write-Warning "舊工作排程恢復失敗：$($_.Exception.Message)"
            }
        }
        elseif ($TaskRegistered) {
            Unregister-ScheduledTask `
                -TaskPath $TaskPath `
                -TaskName $TaskName `
                -Confirm:$false `
                -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $StateBackupRoot) {
            Remove-Item -LiteralPath $StateBackupRoot -Recurse -Force
        }
    }
    if (Test-Path -LiteralPath $StageRoot) {
        Remove-Item -LiteralPath $StageRoot -Recurse -Force
    }
}

if (Test-Path -LiteralPath $BackupRoot) {
    Remove-Item -LiteralPath $BackupRoot -Recurse -Force
}
if (Test-Path -LiteralPath $StateBackupRoot) {
    Remove-Item -LiteralPath $StateBackupRoot -Recurse -Force
}

Write-Host "安裝完成：$AppRoot"
Write-Host "排程工作：$TaskName（登入後延遲 20 秒）"
Write-Host "診斷：& '$AppRoot\.venv\Scripts\python.exe' -X utf8 -m auto_speech_journal self-test"
