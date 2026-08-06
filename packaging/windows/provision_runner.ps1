[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Executable,
    [Parameter(Mandatory = $true)]
    [ValidateSet("models", "gpu")]
    [string]$Mode,
    [Parameter(Mandatory = $true)]
    [string]$Manifest,
    [Parameter(Mandatory = $true)]
    [string]$ProgressJson,
    [Parameter(Mandatory = $true)]
    [string]$PidFile,
    [Parameter(Mandatory = $true)]
    [string]$ExitFile,
    [switch]$ForceGpu
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ExitCode = 1
$Utf8 = New-Object System.Text.UTF8Encoding($false)

try {
    [IO.File]::WriteAllText($PidFile, "$PID`n", $Utf8)
    if ($Mode -eq "models") {
        & $Executable provision `
            --manifest $Manifest `
            --progress-json $ProgressJson
    }
    else {
        $Arguments = @(
            "repair", "gpu",
            "--manifest", $Manifest,
            "--progress-json", $ProgressJson
        )
        if ($ForceGpu) {
            $Arguments += "--force-gpu"
        }
        & $Executable @Arguments
    }
    $ExitCode = $LASTEXITCODE
}
catch {
    $Message = "Provision runner failed: $($_.Exception.Message)`n"
    [IO.File]::WriteAllText("$ExitFile.error", $Message, $Utf8)
    $ExitCode = 1
}
finally {
    [IO.File]::WriteAllText($ExitFile, "$ExitCode`n", $Utf8)
}

exit $ExitCode
