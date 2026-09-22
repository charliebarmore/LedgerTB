<#
.SYNOPSIS
    Launch the installed LedgerTB desktop app, confirm a window opens and stays
    up, and photograph the screen so a person can see it did.

.DESCRIPTION
    smoke_serve.ps1 proves the server serves pages; smoke_close.ps1 proves the
    window closes cleanly. Neither shows anyone what the window looked like.
    This script starts the exe the way a Start Menu shortcut does (no mode, no
    arguments), waits for the native window, gives the page time to render,
    then:

      - asserts the desktop process is still alive,
      - asserts neither the server log nor the parent's stderr contains a
        Python traceback,
      - saves a PNG of the whole screen (System.Drawing CopyFromScreen) into
        -ArtifactDir for a human to look at,
      - closes the window and stops whatever is left.

    Books are isolated: LEDGERTB_DB_PATH points at a throwaway file, so this
    never touches a book configured on the machine.

    ASCII ONLY IN THIS FILE. Windows PowerShell 5.1 reads a BOM-less .ps1 as
    ANSI, so a UTF-8 dash or curly quote decodes into bytes that break the
    parser. CI runs pwsh 7 and would never notice.

.PARAMETER ExePath
    The installed (or built) LedgerTB.exe.

.PARAMETER ArtifactDir
    Where the screenshot and copied logs go.

.PARAMETER WindowTimeoutSeconds
    How long to wait for the native window to appear. A frozen build unpacking
    a bundled Python and starting Streamlit is slow on a cold runner.

.PARAMETER SettleSeconds
    How long to leave the window open after it appears, so the page inside it
    has rendered before the screenshot and so a late crash still shows.

.EXAMPLE
    pwsh scripts/smoke_desktop.ps1 -ExePath C:\LTB\LedgerTB.exe -ArtifactDir acceptance
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ExePath,
    [string]$ArtifactDir = (Join-Path ([System.IO.Path]::GetTempPath()) "ledgertb-desktop"),
    [int]$WindowTimeoutSeconds = 120,
    [int]$SettleSeconds = 25
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ExePath)) {
    Write-Host "DESKTOP FAIL: no binary at '$ExePath'"
    exit 1
}
$exe = (Resolve-Path -LiteralPath $ExePath).Path
New-Item -ItemType Directory -Force -Path $ArtifactDir | Out-Null
$ArtifactDir = (Resolve-Path -LiteralPath $ArtifactDir).Path

$parentOut = Join-Path $ArtifactDir "desktop.out.log"
$parentErr = Join-Path $ArtifactDir "desktop.err.log"
$screenshot = Join-Path $ArtifactDir "desktop.png"
# run_ledgertb.py gives the no-console server child a log file under
# platformdirs.user_data_dir("LedgerTB", appauthor=False), which on Windows is
# %LOCALAPPDATA%\LedgerTB. It appends, so remember where it ended before launch.
$serverLog = Join-Path $env:LOCALAPPDATA "LedgerTB\server.log"
$serverLogStart = 0
if (Test-Path -LiteralPath $serverLog) { $serverLogStart = (Get-Item -LiteralPath $serverLog).Length }

$probeDir = Join-Path ([System.IO.Path]::GetTempPath()) ("ledgertb-desktop-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $probeDir | Out-Null
$previousBook = $env:LEDGERTB_DB_PATH
$env:LEDGERTB_DB_PATH = Join-Path $probeDir "desktop-probe.db"

$failures = @()
$parent = $null
$childIds = @()
$windowTitle = ""
$windowRect = ""

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
Add-Type -Namespace LedgerTB -Name Native -MemberDefinition @"
[System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool SetForegroundWindow(System.IntPtr hWnd);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr hWnd, int nCmdShow);
[System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool GetWindowRect(System.IntPtr hWnd, out RECT lpRect);
"@

function Save-Screenshot {
    param([string]$Path)
    $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    try {
        $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $g.Dispose()
        $bmp.Dispose()
    }
    return $bounds
}

function Find-Traceback {
    param([string]$Path, [int]$Offset = 0)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -le $Offset) { return $null }
    $text = [System.Text.Encoding]::UTF8.GetString($bytes, $Offset, $bytes.Length - $Offset)
    if ($text -match "(?im)^\s*Traceback \(most recent call last\)") { return $text }
    return $null
}

try {
    Write-Host "desktop check: starting $exe"
    # No LEDGERTB_MODE: this is the launch a Start Menu shortcut performs. The
    # redirects also make this a plain CreateProcess, so nothing shell-side can
    # prompt; the parent's own stderr (normally lost in a windowed build) lands
    # in a file we can read.
    $parent = Start-Process -FilePath $exe -PassThru `
        -RedirectStandardOutput $parentOut -RedirectStandardError $parentErr

    $deadline = [DateTime]::UtcNow.AddSeconds($WindowTimeoutSeconds)
    do {
        Start-Sleep -Milliseconds 500
        $parent.Refresh()
        if ($parent.HasExited) {
            $failures += "LedgerTB exited before creating its desktop window (code $($parent.ExitCode))"
            break
        }
    } while (($parent.MainWindowHandle -eq 0) -and ([DateTime]::UtcNow -lt $deadline))

    if ($failures.Count -eq 0 -and $parent.MainWindowHandle -eq 0) {
        $failures += "no desktop window appeared within $WindowTimeoutSeconds seconds"
    }

    if ($failures.Count -eq 0) {
        Write-Host "desktop check: window handle $($parent.MainWindowHandle) after $([int]([DateTime]::UtcNow - $parent.StartTime.ToUniversalTime()).TotalSeconds)s"
        try {
            [LedgerTB.Native]::ShowWindow($parent.MainWindowHandle, 9) | Out-Null  # SW_RESTORE
            [LedgerTB.Native]::SetForegroundWindow($parent.MainWindowHandle) | Out-Null
        } catch { }

        Write-Host "desktop check: letting the page render for $SettleSeconds seconds"
        Start-Sleep -Seconds $SettleSeconds

        $parent.Refresh()
        if ($parent.HasExited) {
            $failures += "LedgerTB opened a window and then exited (code $($parent.ExitCode))"
        } else {
            $windowTitle = $parent.MainWindowTitle
            $rect = New-Object LedgerTB.Native+RECT
            if ([LedgerTB.Native]::GetWindowRect($parent.MainWindowHandle, [ref]$rect)) {
                $windowRect = "{0},{1} {2}x{3}" -f $rect.Left, $rect.Top, ($rect.Right - $rect.Left), ($rect.Bottom - $rect.Top)
            }
            Write-Host "desktop check: window '$windowTitle' at $windowRect, process alive"
        }

        $childIds = @(
            Get-CimInstance Win32_Process -Filter "ParentProcessId = $($parent.Id)" |
                Where-Object { $_.Name -ieq "LedgerTB.exe" } |
                Select-Object -ExpandProperty ProcessId
        )
        if ($childIds.Count -eq 0) {
            $failures += "the desktop window is open but its LedgerTB server child was not found"
        }

        try {
            $bounds = Save-Screenshot -Path $screenshot
            Write-Host "desktop check: screenshot $($bounds.Width)x$($bounds.Height) saved to $screenshot"
        } catch {
            $failures += "screenshot failed: $($_.Exception.Message)"
        }
    }

    foreach ($probe in @(@{ Path = $serverLog; Offset = $serverLogStart; Name = "server.log" },
                         @{ Path = $parentErr; Offset = 0; Name = "desktop stderr" },
                         @{ Path = $parentOut; Offset = 0; Name = "desktop stdout" })) {
        if (Find-Traceback -Path $probe.Path -Offset $probe.Offset) {
            $failures += "$($probe.Name) contains a Python traceback"
        }
    }
} finally {
    if ($null -ne $parent) {
        $parent.Refresh()
        if (-not $parent.HasExited) {
            # Ask nicely first, the way a user closing the window would; the
            # app is expected to take its server child down with it.
            try { $parent.CloseMainWindow() | Out-Null } catch { }
            if (-not $parent.WaitForExit(15000)) {
                Write-Host "desktop check: window did not close on request; stopping it"
                Stop-Process -Id $parent.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }
    foreach ($childId in $childIds) {
        Stop-Process -Id $childId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    if (Test-Path -LiteralPath $serverLog) {
        Copy-Item -LiteralPath $serverLog -Destination (Join-Path $ArtifactDir "server.log") -Force
    }
    $env:LEDGERTB_DB_PATH = $previousBook
    Remove-Item -LiteralPath $probeDir -Recurse -Force -ErrorAction SilentlyContinue
}

$summary = @(
    "### Desktop window",
    "",
    "- window: **$(if ($windowTitle) { $windowTitle } else { 'none' })** at $windowRect",
    "- server child processes seen: **$($childIds.Count)**",
    "- screenshot: $(if (Test-Path -LiteralPath $screenshot) { 'desktop.png in the evidence artifact' } else { 'not captured' })",
    ""
)
if ($env:GITHUB_STEP_SUMMARY) { Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value $summary }

if ($failures.Count -gt 0) {
    Write-Host ""
    Write-Host "===== desktop stderr ====="
    if (Test-Path -LiteralPath $parentErr) { Get-Content -LiteralPath $parentErr -Tail 40 }
    Write-Host "===== server.log (this launch) ====="
    if (Test-Path -LiteralPath $serverLog) { Get-Content -LiteralPath $serverLog -Tail 40 }
    Write-Host ""
    foreach ($f in $failures) { Write-Host "DESKTOP FAIL: $f" }
    exit 1
}

Write-Host "DESKTOP OK - the installed app opened a window and stayed up"
exit 0
