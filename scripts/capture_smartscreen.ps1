<#
.SYNOPSIS
    Open a mark-of-the-web tagged LedgerTB installer the way Explorer does,
    and record the dialog Windows shows, without installing.

.DESCRIPTION
    scripts/accept_install.ps1 starts the installer with Start-Process
    -NoNewWindow, which is CreateProcess, and passes /VERYSILENT. That path
    never asks the shell, so the zone prompt cannot appear.

    This script is the other path. It checks the installer hash, writes
    Zone.Identifier ZoneId=3, confirms the stream, then calls ShellExecuteEx
    on the file with the open verb and no arguments. SEE_MASK_NOZONECHECKS is
    not set. Nothing is passed that would make Inno install silently.

    The calling thread is not the launch thread, so a modal shell dialog
    cannot stall the screenshot. UI Automation reads whatever window appears.
    Run, Run anyway, and open anyway are never invoked. The dialog is
    cancelled or the process is killed so the runner can exit. The product
    is not installed.

    Writing the zone stream here is not a browser download. This runs on a
    GitHub-hosted windows-latest runner.

    ASCII ONLY IN THIS FILE. Windows PowerShell 5.1 reads a BOM-less .ps1 as
    ANSI, so a UTF-8 dash or curly quote decodes into bytes that break the
    parser. CI runs pwsh 7 and would never notice. Invoke this file with
    powershell.exe so UI Automation loads from the .NET Framework GAC.

.PARAMETER InstallerPath
    The LedgerTB setup exe to open. It is not modified except for the
    Zone.Identifier alternate data stream.

.PARAMETER ExpectedSha256
    Hex SHA-256 the file must match before it is tagged or opened. A mismatch
    stops the script. The hash is of the file bytes, not the zone stream.

.PARAMETER ArtifactDir
    Folder for the screenshot, the UI Automation dump, and the text report.

.EXAMPLE
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\capture_smartscreen.ps1 -InstallerPath C:\temp\LedgerTB-1.8.0-windows-x64-setup.exe -ExpectedSha256 4374221b0e3bf80d21e3d89b50f4a2e0dec488400b37213336673887334f2bf0 -ArtifactDir C:\temp\smartscreen
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,
    [Parameter(Mandatory = $true)]
    [string]$ExpectedSha256,
    [string]$ArtifactDir = ""
)

$ErrorActionPreference = "Stop"
$script:exitCode = 1

function Write-Summary {
    param([string[]]$Lines)
    foreach ($l in $Lines) { Write-Host $l }
    if ($env:GITHUB_STEP_SUMMARY) {
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value $Lines
    }
}

function Get-ZoneId {
    param([string]$Path)
    $stream = Get-Item -LiteralPath $Path -Stream Zone.Identifier -ErrorAction SilentlyContinue
    if (-not $stream) { return $null }
    $text = Get-Content -LiteralPath $Path -Stream Zone.Identifier -Raw -ErrorAction SilentlyContinue
    foreach ($line in ($text -split "`r?`n")) {
        if ($line.Trim().ToLower().StartsWith("zoneid=")) {
            return [int]($line.Split("=", 2)[1].Trim())
        }
    }
    return -1
}

function Convert-UiName {
    # The source of this file is ASCII. Windows sometimes draws Don't with
    # a curly apostrophe, so fold those code points at runtime.
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) { return "" }
    $n = $Name.Trim().ToLowerInvariant()
    $n = $n.Replace(([string][char]0x2019), "'")
    $n = $n.Replace(([string][char]0x2018), "'")
    return $n
}

if (-not (Test-Path -LiteralPath $InstallerPath)) {
    Write-Host "CAPTURE FAIL: no installer at '$InstallerPath'"
    exit 1
}
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path

if (-not $ArtifactDir) {
    $ArtifactDir = Join-Path ([System.IO.Path]::GetTempPath()) "ledgertb-smartscreen"
}
New-Item -ItemType Directory -Force -Path $ArtifactDir | Out-Null
$ArtifactDir = (Resolve-Path -LiteralPath $ArtifactDir).Path
$screenshot = Join-Path $ArtifactDir "windows-smartscreen.png"
$reportPath = Join-Path $ArtifactDir "dialog-report.txt"
$uiaPath = Join-Path $ArtifactDir "uia-dump.txt"
$policyPath = Join-Path $ArtifactDir "policies.txt"

# --- hash gate: do not tag and do not open a different file ----------------
$size = (Get-Item -LiteralPath $installer).Length
$sha = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
$expected = $ExpectedSha256.Trim().ToLowerInvariant()
Write-Host "installer: $installer"
Write-Host "  size:    $size bytes"
Write-Host "  sha256:  $sha"
Write-Host "  expect:  $expected"
if ($sha -ne $expected) {
    Write-Host "CAPTURE FAIL: sha256 does not match the qualified installer. Not opening it."
    exit 1
}

# --- tag, then prove the stream is really there ----------------------------
Write-Host "tagging Zone.Identifier ZoneId=3"
Set-Content -LiteralPath $installer -Stream Zone.Identifier -Value "[ZoneTransfer]`r`nZoneId=3"
Write-Host "streams on the installer:"
Get-Item -LiteralPath $installer -Stream * | ForEach-Object {
    Write-Host ("  {0} ({1} bytes)" -f $_.Stream, $_.Length)
}
$zoneText = Get-Content -LiteralPath $installer -Stream Zone.Identifier -Raw
Write-Host "Zone.Identifier raw:"
Write-Host $zoneText
$zoneId = Get-ZoneId $installer
if ($null -eq $zoneId -or $zoneId -ne 3) {
    Write-Host "CAPTURE FAIL: Zone.Identifier ZoneId=3 is not present (got '$zoneId'). Not opening it."
    exit 1
}
# Hashing reads the main stream. The zone tag must not have changed the bytes.
$shaAfter = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($shaAfter -ne $expected) {
    Write-Host "CAPTURE FAIL: sha256 changed after tagging ('$shaAfter'). Not opening it."
    exit 1
}

$os = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$elevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$sessionId = (Get-Process -Id $PID).SessionId
$who = ""
try { $who = (& whoami.exe) } catch { $who = "(whoami failed)" }
$label = ""
try { $label = ((& whoami.exe /groups) | Select-String "Mandatory Label" | Select-Object -First 1).Line } catch { }

$installDir = Join-Path $env:LOCALAPPDATA "Programs\LedgerTB"
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{8EE4B706-D4BD-4A9E-97DB-219152E5C235}_is1"
$existedBefore = Test-Path -LiteralPath (Join-Path $installDir "LedgerTB.exe")
$uninstallBefore = Test-Path -LiteralPath $uninstallKey

$policyLines = @(
    "product: $((Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').ProductName)",
    "display: $($os.DisplayVersion)",
    "build:   $($os.CurrentBuild).$($os.UBR)",
    "user:    $who",
    "session: $sessionId",
    "elevated: $elevated",
    "integrity: $label",
    "install dir before: $existedBefore ($installDir)",
    "uninstall key before: $uninstallBefore"
)
$policyNames = @(
    @{ Path = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer"; Name = "SmartScreenEnabled" },
    @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\System"; Name = "EnableSmartScreen" },
    @{ Path = "HKLM:\SOFTWARE\Policies\Microsoft\Windows\System"; Name = "ShellSmartScreenLevel" },
    @{ Path = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer"; Name = "SmartScreenEnabled" },
    @{ Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\AppHost"; Name = "EnableWebContentEvaluation" },
    @{ Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings\Zones\3"; Name = "1806" },
    @{ Path = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings\Zones\3"; Name = "1806" },
    @{ Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Policies\Attachments"; Name = "SaveZoneInformation" },
    @{ Path = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Attachments"; Name = "SaveZoneInformation" },
    @{ Path = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Policies\Associations"; Name = "LowRiskFileTypes" }
)
foreach ($entry in $policyNames) {
    $shown = "(absent)"
    try {
        $item = Get-ItemProperty -LiteralPath $entry.Path -Name $entry.Name -ErrorAction Stop
        $shown = [string]$item.($entry.Name)
    } catch { }
    $policyLines += ("{0} ! {1} = {2}" -f $entry.Path, $entry.Name, $shown)
}
$policyLines | ForEach-Object { Write-Host $_ }
Set-Content -LiteralPath $policyPath -Value $policyLines -Encoding UTF8

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
$uiaOk = $true
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
} catch {
    $uiaOk = $false
    Write-Host "UIA assemblies did not load: $($_.Exception.Message)"
}

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace LedgerTB
{
    public class Native
    {
        public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);

        [StructLayout(LayoutKind.Sequential)]
        public struct RECT
        {
            public int Left;
            public int Top;
            public int Right;
            public int Bottom;
        }

        [DllImport("user32.dll")]
        public static extern bool EnumWindows(EnumProc lpEnumFunc, IntPtr lParam);
        [DllImport("user32.dll")]
        public static extern bool IsWindowVisible(IntPtr hWnd);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        public static extern int GetWindowText(IntPtr hWnd, StringBuilder lpString, int nMaxCount);
        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        public static extern int GetClassName(IntPtr hWnd, StringBuilder lpClass, int nMaxCount);
        [DllImport("user32.dll")]
        public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
        [DllImport("user32.dll")]
        public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
        [DllImport("user32.dll")]
        public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
        [DllImport("user32.dll")]
        public static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);
        [DllImport("user32.dll")]
        public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
        [DllImport("user32.dll")]
        public static extern bool SetProcessDPIAware();

        public static List<string> WindowLines = new List<string>();
        private static EnumProc EnumCallback;

        public static void CollectWindows()
        {
            WindowLines = new List<string>();
            EnumCallback = delegate(IntPtr h, IntPtr l)
            {
                if (!IsWindowVisible(h)) return true;
                StringBuilder title = new StringBuilder(512);
                GetWindowText(h, title, title.Capacity);
                StringBuilder cls = new StringBuilder(256);
                GetClassName(h, cls, cls.Capacity);
                uint pid;
                GetWindowThreadProcessId(h, out pid);
                RECT r;
                int w = 0;
                int t = 0;
                int width = 0;
                int height = 0;
                if (GetWindowRect(h, out r))
                {
                    w = r.Left;
                    t = r.Top;
                    width = r.Right - r.Left;
                    height = r.Bottom - r.Top;
                }
                WindowLines.Add(
                    h.ToInt64().ToString() + "\t" + pid.ToString() + "\t" + w.ToString()
                    + "," + t.ToString() + " " + width.ToString() + "x" + height.ToString()
                    + "\t" + cls.ToString() + "\t" + title.ToString());
                return true;
            };
            EnumWindows(EnumCallback, IntPtr.Zero);
        }

        public static void BringToFront(IntPtr hWnd)
        {
            ShowWindow(hWnd, 9);
            SetWindowPos(hWnd, new IntPtr(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010);
            SetWindowPos(hWnd, new IntPtr(-2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010);
        }
    }

    public class ShellLaunch
    {
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        public struct SHELLEXECUTEINFO
        {
            public int cbSize;
            public uint fMask;
            public IntPtr hwnd;
            public string lpVerb;
            public string lpFile;
            public string lpParameters;
            public string lpDirectory;
            public int nShow;
            public IntPtr hInstApp;
            public IntPtr lpIDList;
            public string lpClass;
            public IntPtr hkeyClass;
            public uint dwHotKey;
            public IntPtr hIconOrMonitor;
            public IntPtr hProcess;
        }

        [DllImport("shell32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool ShellExecuteEx(ref SHELLEXECUTEINFO lpExecInfo);

        public static string TargetPath;
        public static string Result = "";
        public static int Done = 0;
        public static Thread Worker;

        public static void Go()
        {
            try
            {
                SHELLEXECUTEINFO sei = new SHELLEXECUTEINFO();
                sei.cbSize = Marshal.SizeOf(typeof(SHELLEXECUTEINFO));
                // SEE_MASK_NOCLOSEPROCESS only. SEE_MASK_NOZONECHECKS is not
                // set. lpParameters stays null: no /VERYSILENT, no /SILENT.
                sei.fMask = 0x00000040;
                sei.lpVerb = "open";
                sei.lpFile = TargetPath;
                sei.lpParameters = null;
                sei.nShow = 1;
                bool ok = ShellExecuteEx(ref sei);
                int err = Marshal.GetLastWin32Error();
                Result = (ok ? "ShellExecuteEx returned true" : "ShellExecuteEx returned false")
                    + " win32=" + err.ToString()
                    + " hInstApp=" + sei.hInstApp.ToInt64().ToString()
                    + " hProcess=" + sei.hProcess.ToInt64().ToString();
            }
            catch (Exception ex)
            {
                Result = "ShellExecuteEx exception: " + ex.GetType().Name + ": " + ex.Message;
            }
            Done = 1;
        }

        public static void Start()
        {
            Done = 0;
            Result = "";
            Worker = new Thread(Go);
            Worker.IsBackground = true;
            Worker.SetApartmentState(ApartmentState.STA);
            Worker.Start();
        }
    }
}
'@

function Save-Screenshot {
    param([string]$Path)
    [LedgerTB.Native]::SetProcessDPIAware() | Out-Null
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
    Write-Host "screenshot $($bounds.Width)x$($bounds.Height) -> $Path"
}

function Get-WindowRows {
    [LedgerTB.Native]::CollectWindows()
    $rows = @()
    foreach ($line in [LedgerTB.Native]::WindowLines) {
        $parts = $line -split "`t", 5
        $procName = ""
        $procPath = ""
        try {
            $p = Get-Process -Id ([int]$parts[1]) -ErrorAction Stop
            $procName = $p.ProcessName
            $procPath = $p.Path
        } catch { }
        $rows += [pscustomobject]@{
            Hwnd = [int64]$parts[0]
            Pid = [int]$parts[1]
            Rect = $parts[2]
            Class = $parts[3]
            Title = $parts[4]
            Process = $procName
            Path = $procPath
        }
    }
    return $rows
}

function Get-UiaNodeText {
    param($Element, [int]$Depth, [int]$MaxDepth)
    $lines = New-Object System.Collections.Generic.List[string]
    if ($null -eq $Element -or $Depth -gt $MaxDepth) { return $lines }
    try {
        $current = $Element.Current
        $typeName = ""
        try { $typeName = $current.ControlType.ProgrammaticName } catch { $typeName = "ControlType" }
        $typeName = $typeName -replace "^ControlType\.", ""
        $name = [string]$current.Name
        if ($name) { $name = $name -replace "[\r\n]+", " " }
        $autoId = [string]$current.AutomationId
        $pad = "  " * $Depth
        $suffix = ""
        if ($autoId) { $suffix = "  id=$autoId" }
        $lines.Add(("{0}[{1}] {2}{3}" -f $pad, $typeName, $name, $suffix))
        if ($Depth -ge $MaxDepth) { return $lines }
        $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
        $child = $walker.GetFirstChild($Element)
        $guard = 0
        while ($null -ne $child -and $guard -lt 200) {
            $guard++
            foreach ($line in (Get-UiaNodeText -Element $child -Depth ($Depth + 1) -MaxDepth $MaxDepth)) {
                $lines.Add($line)
            }
            $child = $walker.GetNextSibling($child)
        }
    } catch {
        $lines.Add(("  " * $Depth) + "UIA error: " + $_.Exception.Message)
    }
    return $lines
}

function Get-UiaForHwnd {
    param([int64]$Hwnd)
    if (-not $uiaOk) { return @("UIA unavailable") }
    try {
        $el = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
        return @(Get-UiaNodeText -Element $el -Depth 0 -MaxDepth 8)
    } catch {
        return @("UIA FromHandle failed: $($_.Exception.Message)")
    }
}

function Test-PromptRow {
    param($Row)
    $title = Convert-UiName $Row.Title
    $proc = $Row.Process
    if ($proc -eq "smartscreen") { return $true }
    if ($title -match "protected your pc|smartscreen|security warning|open file|user account control|do you want to run") { return $true }
    if ($Row.Path -and ($Row.Path -eq $installer)) { return $true }
    if ($proc -like "LedgerTB*") { return $true }
    if ($title -match "ledgertb|setup") { return $true }
    if ($Row.Class -eq "#32770") { return $true }
    return $false
}

function Get-SafeInvokeName {
    param([string]$Name)
    $n = Convert-UiName $Name
    if ($n -in @("don't run", "dont run", "do not run", "cancel")) { return $n }
    return $null
}

$script:invokedButton = "(none)"
$script:dialogRows = @()
$script:uiaBlocks = @()
$script:launchResult = "(not started)"

function Invoke-SafeButtons {
    param($Rows)
    if (-not $uiaOk) { return }
    foreach ($row in $Rows) {
        if (-not (Test-PromptRow $row)) { continue }
        try {
            $el = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$row.Hwnd)
            $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
            $stack = New-Object System.Collections.Stack
            $stack.Push($el)
            $seen = 0
            while ($stack.Count -gt 0 -and $seen -lt 400) {
                $seen++
                $node = $stack.Pop()
                $name = ""
                $isButton = $false
                try {
                    $name = [string]$node.Current.Name
                    $isButton = $node.Current.ControlType.ProgrammaticName -eq "ControlType.Button"
                } catch { }
                $safe = Get-SafeInvokeName $name
                if ($isButton -and $safe) {
                    $pattern = $null
                    $got = $node.TryGetCurrentPattern(
                        [System.Windows.Automation.InvokePattern]::Pattern,
                        [ref]$pattern)
                    if ($got -and $null -ne $pattern) {
                        Write-Host "invoking safe button: $name"
                        $pattern.Invoke()
                        $script:invokedButton = $name
                        return
                    }
                }
                $child = $walker.GetFirstChild($node)
                $guard = 0
                while ($null -ne $child -and $guard -lt 80) {
                    $guard++
                    $stack.Push($child)
                    $child = $walker.GetNextSibling($child)
                }
            }
        } catch {
            Write-Host "safe-button scan failed for hwnd $($row.Hwnd): $($_.Exception.Message)"
        }
    }
}

function Close-PromptWindows {
    param($Rows)
    foreach ($row in $Rows) {
        if (-not (Test-PromptRow $row)) { continue }
        Write-Host "WM_CLOSE hwnd=$($row.Hwnd) title='$($row.Title)' process=$($row.Process)"
        [LedgerTB.Native]::PostMessage([IntPtr]$row.Hwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
    }
}

function Stop-InstallerProcesses {
    $names = @("smartscreen")
    Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Id -ne $PID -and (
            $_.ProcessName -in $names -or
            $_.ProcessName -like "LedgerTB-*-setup" -or
            $_.Path -eq $installer
        )
    } | ForEach-Object {
        Write-Host "stopping process $($_.Id) $($_.ProcessName)"
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    }
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ExecutablePath -eq $installer
    } | ForEach-Object {
        Write-Host "stopping pid $($_.ProcessId) path $($_.ExecutablePath)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

try {
    $before = @(Get-WindowRows)
    $beforeHwnds = @{}
    foreach ($row in $before) { $beforeHwnds[[string]$row.Hwnd] = $true }
    Write-Host "visible windows before launch: $($before.Count)"

    Write-Host "launch: ShellExecuteEx verb=open arguments=(none) NOZONECHECKS=not set"
    [LedgerTB.ShellLaunch]::TargetPath = $installer
    [LedgerTB.ShellLaunch]::Start()

    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    $captured = $false
    $stableSince = $null
    do {
        Start-Sleep -Milliseconds 400
        $rows = @(Get-WindowRows)
        $newRows = @($rows | Where-Object { -not $beforeHwnds.ContainsKey([string]$_.Hwnd) })
        $promptRows = @($newRows | Where-Object { Test-PromptRow $_ })
        $smart = @(Get-Process -Name smartscreen -ErrorAction SilentlyContinue)
        if ($promptRows.Count -gt 0 -or $smart.Count -gt 0) {
            if ($null -eq $stableSince) { $stableSince = [DateTime]::UtcNow }
            # Let a reputation check replace a spinner before the shot.
            if (([DateTime]::UtcNow - $stableSince).TotalSeconds -ge 3) {
                foreach ($row in $promptRows) {
                    try { [LedgerTB.Native]::BringToFront([IntPtr]$row.Hwnd) | Out-Null } catch { }
                }
                Start-Sleep -Milliseconds 500
                $script:dialogRows = @(Get-WindowRows | Where-Object {
                    (Test-PromptRow $_) -and (-not $beforeHwnds.ContainsKey([string]$_.Hwnd) -or $_.Process -eq "smartscreen")
                })
                if ($script:dialogRows.Count -eq 0) {
                    $script:dialogRows = @($promptRows)
                }
                Save-Screenshot -Path $screenshot
                $captured = $true
                break
            }
        }
    } while ([DateTime]::UtcNow -lt $deadline)

    if (-not $captured) {
        Write-Host "no prompt window appeared within 45 seconds"
        Save-Screenshot -Path $screenshot
        $script:dialogRows = @()
    }

    $after = @(Get-WindowRows)
    $dump = New-Object System.Collections.Generic.List[string]
    $dump.Add("UIA loaded: $uiaOk")
    $dump.Add("windows after launch:")
    foreach ($row in $after) {
        $mark = ""
        if (-not $beforeHwnds.ContainsKey([string]$row.Hwnd)) { $mark = " NEW" }
        $dump.Add(("hwnd={0} pid={1} {2} class={3} process={4} title={5}{6}" -f $row.Hwnd, $row.Pid, $row.Rect, $row.Class, $row.Process, $row.Title, $mark))
    }
    $dump.Add("")
    $targets = @($script:dialogRows)
    if ($targets.Count -eq 0) {
        $targets = @($after | Where-Object { -not $beforeHwnds.ContainsKey([string]$_.Hwnd) })
    }
    foreach ($row in $targets) {
        $dump.Add(("----- hwnd={0} process={1} class={2} title={3}" -f $row.Hwnd, $row.Process, $row.Class, $row.Title))
        $tree = @(Get-UiaForHwnd -Hwnd $row.Hwnd)
        foreach ($line in $tree) { $dump.Add($line) }
        $dump.Add("")
        $script:uiaBlocks += [pscustomobject]@{ Row = $row; Tree = $tree }
    }
    Set-Content -LiteralPath $uiaPath -Value $dump -Encoding UTF8
    $dump | ForEach-Object { Write-Host $_ }

    $script:launchResult = [LedgerTB.ShellLaunch]::Result
    if (-not $script:launchResult) { $script:launchResult = "(ShellExecuteEx has not returned yet)" }
    Write-Host "launch result so far: $($script:launchResult)"
} finally {
    try {
        $live = @(Get-WindowRows)
        Invoke-SafeButtons -Rows $live
        Start-Sleep -Milliseconds 400
        Close-PromptWindows -Rows $live
        Start-Sleep -Milliseconds 400
        Stop-InstallerProcesses
    } catch {
        Write-Host "cleanup warning: $($_.Exception.Message)"
    }
    if ([LedgerTB.ShellLaunch]::Worker) {
        [void][LedgerTB.ShellLaunch]::Worker.Join(10000)
    }
    if ([LedgerTB.ShellLaunch]::Result) { $script:launchResult = [LedgerTB.ShellLaunch]::Result }
    Write-Host "launch result: $($script:launchResult)"
}

$buttonNames = New-Object System.Collections.Generic.List[string]
$textNames = New-Object System.Collections.Generic.List[string]
$titles = New-Object System.Collections.Generic.List[string]
foreach ($block in $script:uiaBlocks) {
    if ($block.Row.Title) { $titles.Add([string]$block.Row.Title) }
    foreach ($line in $block.Tree) {
        if ($line -match '^(\s*)\[([^\]]+)\]\s*(.*)$') {
            $kind = $Matches[2]
            $value = $Matches[3]
            if ($value -match '^(.*)\s\sid=.+$') { $value = $Matches[1] }
            $value = $value.Trim()
            if (-not $value) { continue }
            if ($kind -eq "Button" -or $kind -eq "Hyperlink") { $buttonNames.Add($value) }
            elseif ($kind -in @("Text", "Document", "Edit", "TitleBar")) { $textNames.Add($value) }
        }
    }
}

function Test-Choice {
    param([string[]]$Names, [string[]]$Exact)
    foreach ($name in $Names) {
        $n = Convert-UiName $name
        if ($n -in $Exact) { return $true }
    }
    return $false
}

$runShown = Test-Choice -Names $buttonNames -Exact @("run")
$runAnywayShown = Test-Choice -Names $buttonNames -Exact @("run anyway", "open anyway")
$dontRunShown = Test-Choice -Names $buttonNames -Exact @("don't run", "dont run", "do not run")
$cancelShown = Test-Choice -Names $buttonNames -Exact @("cancel")

$dialogFound = ($script:dialogRows.Count -gt 0)
$titleText = ""
if ($titles.Count -gt 0) { $titleText = ($titles | Select-Object -Unique) -join " | " }
$bodyText = ""
if ($textNames.Count -gt 0) { $bodyText = ($textNames | Select-Object -Unique) -join "`r`n" }
$blob = Convert-UiName ($titleText + " " + ($textNames -join " ") + " " + ($buttonNames -join " "))
$promptKind = "none"
if ($blob -match "protected your pc|smartscreen") {
    $promptKind = "smartscreen"
    $dialogFound = $true
} elseif ($blob -match "security warning|do you want to run") {
    $promptKind = "security-warning"
    $dialogFound = $true
} elseif ($dialogFound) {
    $promptKind = "installer-or-other"
}

$existedAfter = Test-Path -LiteralPath (Join-Path $installDir "LedgerTB.exe")
$uninstallAfter = Test-Path -LiteralPath $uninstallKey
$installedNow = ((-not $existedBefore) -and $existedAfter) -or ((-not $uninstallBefore) -and $uninstallAfter)

$report = @(
    "sha256: $sha",
    "size: $size",
    "zone_id: $zoneId",
    "zone_stream: present",
    "launch: ShellExecuteEx verb=open arguments=(none) SEE_MASK_NOZONECHECKS=not set",
    "launch_result: $($script:launchResult)",
    "host: GitHub-hosted windows-latest runner, zone stream written by this script",
    "dialog_found: $dialogFound",
    "prompt_kind: $promptKind",
    "dialog_title: $titleText",
    "buttons: $($buttonNames -join ' | ')",
    "run_shown: $runShown",
    "run_anyway_shown: $runAnywayShown",
    "dont_run_shown: $dontRunShown",
    "cancel_shown: $cancelShown",
    "invoked_button: $($script:invokedButton)",
    "install_dir_after: $existedAfter",
    "uninstall_key_after: $uninstallAfter",
    "installed_by_this_job: $installedNow",
    "screenshot: $(if (Test-Path -LiteralPath $screenshot) { $screenshot } else { 'missing' })",
    "BODY_BEGIN",
    $bodyText,
    "BODY_END"
)
Set-Content -LiteralPath $reportPath -Value $report -Encoding UTF8
Write-Host "===== CAPTURE REPORT ====="
$report | ForEach-Object { Write-Host $_ }
Write-Host "===== END CAPTURE REPORT ====="

$summary = @(
    "### SmartScreen capture",
    "",
    "- installer sha256: ``$sha``",
    "- Zone.Identifier ZoneId: **$zoneId**",
    "- launch: ShellExecuteEx open, no arguments, zone check left enabled",
    "- dialog found: **$dialogFound** ($promptKind)",
    "- dialog title: $titleText",
    "- buttons: $($buttonNames -join ', ')",
    "- Run shown: **$runShown**. Run anyway / open anyway shown: **$runAnywayShown**. Don't run shown: **$dontRunShown**.",
    "- button invoked: $($script:invokedButton)",
    "- installed by this job: **$installedNow**",
    "",
    "This is a GitHub-hosted runner. The zone stream was written by the script. It is not a browser download.",
    ""
)
Write-Summary $summary

if (-not (Test-Path -LiteralPath $screenshot)) {
    Write-Host "CAPTURE FAIL: screenshot was not written"
    exit 1
}
if ($installedNow) {
    Write-Host "CAPTURE FAIL: LedgerTB was installed. This job must not install it."
    exit 1
}
if ($script:invokedButton -and (Convert-UiName $script:invokedButton) -notin @("(none)", "don't run", "dont run", "do not run", "cancel")) {
    Write-Host "CAPTURE FAIL: invoked an unexpected button '$($script:invokedButton)'"
    exit 1
}

Write-Host "CAPTURE DONE - dialog_found=$dialogFound installed=false"
exit 0
