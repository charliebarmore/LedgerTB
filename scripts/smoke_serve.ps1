<#
.SYNOPSIS
    Start the built LedgerTB.exe as a server and prove it actually serves a page.

.DESCRIPTION
    LEDGERTB_MODE=selfcheck answers "are all the parts in the box?" - it imports
    the runtime modules and exits. It does NOT start the server or request a
    page, which is how build 31013372986 passed every gate and still returned
    HTTP 500 on every request: starlette 1.4.0 made an argument required that
    Streamlit 1.61.0 did not pass, and the failure only surfaced once a real
    response was assembled.

    This script closes that hole. It launches the frozen binary in server mode,
    requests the health endpoint and the app page, and fails if either does not
    come back 200.

    The page request explicitly asks for gzip. That matters: the bug lived in
    the gzip middleware, so a request that skipped compression would have
    sailed through and told us nothing.

    ASCII ONLY IN THIS FILE. Windows PowerShell 5.1 reads a .ps1 with no BOM as
    ANSI, so a UTF-8 dash or quote decodes into bytes that break the parser -
    including a stray double-quote that swallows the rest of the script. CI
    runs pwsh (7.x, UTF-8 by default) and would not notice.

.PARAMETER ExePath
    Path to the built LedgerTB.exe. Defaults to the standard build output.

.PARAMETER Port
    Local port to serve on. Defaults to 8599.

.PARAMETER TimeoutSeconds
    How long to wait for the server to come up. A frozen build unpacking a
    bundled Python is slower than running from source.

.EXAMPLE
    pwsh scripts/smoke_serve.ps1
    pwsh scripts/smoke_serve.ps1 -ExePath dist/LedgerTB/LedgerTB.exe
#>
[CmdletBinding()]
param(
    [string]$ExePath = "dist/LedgerTB/LedgerTB.exe",
    [int]$Port = 8599,
    [int]$TimeoutSeconds = 90
)

if (-not (Test-Path $ExePath)) {
    Write-Host "SMOKE FAIL: no binary at '$ExePath' - build first"
    exit 1
}

$logDir = Join-Path ([System.IO.Path]::GetTempPath()) "ledgertb-smoke"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir "server.out.log"
$errLog = Join-Path $logDir "server.err.log"

$env:LEDGERTB_MODE = "server"
$env:LEDGERTB_PORT = "$Port"

Write-Host "smoke check: starting $ExePath on 127.0.0.1:$Port"
$proc = Start-Process -FilePath $ExePath -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog

$base = "http://127.0.0.1:$Port"
$failures = @()

try {
    # Wait for the server to answer at all.
    $ready = $false
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) {
            $failures += "server exited early with code $($proc.ExitCode)"
            break
        }
        try {
            $h = Invoke-WebRequest "$base/_stcore/health" -UseBasicParsing -TimeoutSec 3
            if ($h.StatusCode -eq 200) { $ready = $true; break }
        } catch {
            Start-Sleep -Milliseconds 750
        }
    }

    if (-not $ready -and $failures.Count -eq 0) {
        $failures += "server never became ready within $TimeoutSeconds seconds"
    }

    if ($ready) {
        Write-Host "smoke check: server is up"

        # Every check below sends Accept-Encoding: gzip, and that is the whole
        # point. The starlette 1.4.0 break lived in the gzip responder, and it
        # does not fire on every route: on the broken build GET / still
        # returned 200 while /_stcore/health returned 500. Asking for
        # compression on more than one route is what makes this gate real.
        $routes = @(
            @{ Path = "/_stcore/health"; MinBytes = 1 },
            @{ Path = "/";               MinBytes = 500 }
        )

        foreach ($route in $routes) {
            $u = "$base$($route.Path)"
            try {
                $r = Invoke-WebRequest $u -UseBasicParsing -TimeoutSec 15 `
                    -Headers @{ "Accept-Encoding" = "gzip" }
                if ($r.StatusCode -ne 200) {
                    $failures += "GET $($route.Path) [gzip] returned $($r.StatusCode), expected 200"
                } elseif ($r.RawContentLength -lt $route.MinBytes) {
                    $failures += "GET $($route.Path) [gzip] returned only $($r.RawContentLength) bytes"
                } else {
                    Write-Host "smoke check: GET $($route.Path) [gzip] OK ($($r.RawContentLength) bytes)"
                }
            } catch {
                $code = $null
                if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
                if ($code) {
                    $failures += "GET $($route.Path) [gzip] returned $code, expected 200"
                } else {
                    $failures += "GET $($route.Path) [gzip] failed: $($_.Exception.Message)"
                }
            }
        }
    }
} finally {
    if ($proc -and -not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}

if ($failures.Count -gt 0) {
    Write-Host ""
    Write-Host "===== server stdout ====="
    if (Test-Path $outLog) { Get-Content $outLog -Tail 40 }
    Write-Host "===== server stderr ====="
    if (Test-Path $errLog) { Get-Content $errLog -Tail 40 }
    Write-Host ""
    foreach ($f in $failures) { Write-Host "SMOKE FAIL: $f" }
    exit 1
}

Write-Host "SMOKE OK - the built app serves a page"
exit 0
