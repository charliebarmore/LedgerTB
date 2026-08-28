param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath,
    [int]$WindowTimeoutSeconds = 60,
    [int]$ExitTimeoutSeconds = 15
)

$ErrorActionPreference = "Stop"
$exe = (Resolve-Path $ExePath).Path
$probeDir = Join-Path ([System.IO.Path]::GetTempPath()) ("ledgertb-close-" + [guid]::NewGuid())
$probeBook = Join-Path $probeDir "close-probe.db"
$previousBook = $env:LEDGERTB_DB_PATH
$parent = $null
$childIds = @()

New-Item -ItemType Directory -Path $probeDir | Out-Null

try {
    # Keep the lifecycle probe isolated from any book configured on the runner.
    $env:LEDGERTB_DB_PATH = $probeBook
    $parent = Start-Process -FilePath $exe -PassThru
    $windowDeadline = [DateTime]::UtcNow.AddSeconds($WindowTimeoutSeconds)

    do {
        Start-Sleep -Milliseconds 250
        $parent.Refresh()
        if ($parent.HasExited) {
            throw "LedgerTB exited before creating its desktop window (code $($parent.ExitCode))."
        }
    } while (($parent.MainWindowHandle -eq 0) -and ([DateTime]::UtcNow -lt $windowDeadline))

    if ($parent.MainWindowHandle -eq 0) {
        throw "LedgerTB did not create a desktop window within $WindowTimeoutSeconds seconds."
    }

    $childIds = @(
        Get-CimInstance Win32_Process -Filter "ParentProcessId = $($parent.Id)" |
            Where-Object { $_.Name -ieq "LedgerTB.exe" } |
            Select-Object -ExpandProperty ProcessId
    )
    if ($childIds.Count -eq 0) {
        throw "The desktop window opened, but its LedgerTB server child was not found."
    }

    if (-not $parent.CloseMainWindow()) {
        throw "Windows refused the desktop close request."
    }
    if (-not $parent.WaitForExit($ExitTimeoutSeconds * 1000)) {
        throw "The LedgerTB desktop process remained alive after its window closed."
    }

    foreach ($childId in $childIds) {
        $child = Get-Process -Id $childId -ErrorAction SilentlyContinue
        if ($null -ne $child -and -not $child.WaitForExit($ExitTimeoutSeconds * 1000)) {
            throw "LedgerTB server child $childId remained alive after its window closed."
        }
    }

    Write-Host "CLOSE OK - desktop and server processes exited"
}
finally {
    if ($null -ne $parent) {
        $parent.Refresh()
        if (-not $parent.HasExited) {
            Stop-Process -Id $parent.Id -Force -ErrorAction SilentlyContinue
        }
    }
    foreach ($childId in $childIds) {
        Stop-Process -Id $childId -Force -ErrorAction SilentlyContinue
    }
    $env:LEDGERTB_DB_PATH = $previousBook
    Remove-Item -LiteralPath $probeDir -Recurse -Force -ErrorAction SilentlyContinue
}
