# Shared application process and mutex helpers for install.ps1 and uninstall.ps1.
# Both callers define $MutexName and $AppRoot before invoking these functions;
# PowerShell resolves those from the caller scope at call time.

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
        $CurrentProcessIds = @(Get-AppProcessIds)
        $KnownProcessRunning = @(
            $KnownProcessIds |
                Where-Object { $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue) }
        ).Count -gt 0
        if (
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
