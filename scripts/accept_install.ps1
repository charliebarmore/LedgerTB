<#
.SYNOPSIS
    Install LedgerTB from an installer that Windows believes came from the
    internet, and prove that nothing it installed carries that mark.

.DESCRIPTION
    The reason LedgerTB ships an Inno Setup installer instead of a zip is
    mark-of-the-web: Explorer stamps every file it extracts from a downloaded
    zip with a Zone.Identifier stream (ZoneId=3), the .NET Framework then
    refuses to load the bundled Python.Runtime.dll, and the app dies before
    its window opens. An installer writes the files itself, so nothing lands
    tagged.

    This script checks that claim end to end:

      1. The installer carries the mark (a browser download does; pass
         -SimulateDownload to write the same stream a browser would, which
         is what CI does because a runner cannot download through Explorer).
      2. The tagged installer runs silently and exits 0.
      3. The install folder holds the exe and the dot-prefixed config folder
         that has gone missing from a bundle before.
      4. Not one installed file carries a Zone.Identifier stream. The same
         probe that found the mark on the installer is used here, so a clean
         result is a real negative, not a probe that never worked.

    It does NOT prove what SmartScreen shows. SmartScreen is a shell feature
    triggered when Explorer launches a downloaded file; the installer here is
    started directly (-NoNewWindow uses CreateProcess, not ShellExecute) so an
    unattended run cannot hang on a prompt nobody is there to click. The
    SmartScreen wording and the real browser download stay on the human
    checklist in docs/WINDOWS-TESTING.md.

    ASCII ONLY IN THIS FILE. Windows PowerShell 5.1 reads a BOM-less .ps1 as
    ANSI, so a UTF-8 dash or curly quote decodes into bytes that break the
    parser. CI runs pwsh 7 and would never notice.

.PARAMETER InstallerPath
    The LedgerTB-<version>-windows-x64-setup.exe to test.

.PARAMETER InstallDir
    Where to install. Keep it short: a deep path blows the 260-character limit
    mid-copy and Inno aborts with exit code 5. Must not already exist, so the
    clean-files assertion covers only what this installer wrote.

.PARAMETER SimulateDownload
    Write the Zone.Identifier stream (ZoneId=3, the Internet zone) onto the
    installer first, standing in for the browser download CI cannot perform.

.PARAMETER ReportDir
    Folder for the Inno Setup install log and the mark-of-the-web report.
    Defaults to a folder under the temp directory.

.EXAMPLE
    pwsh scripts/accept_install.ps1 -InstallerPath installer/LedgerTB-1.8.0-windows-x64-setup.exe -SimulateDownload
    pwsh scripts/accept_install.ps1 -InstallerPath $HOME/Downloads/LedgerTB-1.8.0-windows-x64-setup.exe
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,
    [string]$InstallDir = "C:\LTB",
    [switch]$SimulateDownload,
    [string]$ReportDir = ""
)

$ErrorActionPreference = "Stop"

function Get-ZoneId {
    # The ZoneId recorded in a file's Zone.Identifier stream, or $null when the
    # file carries no such stream. Used for both the positive control (the
    # installer) and the assertion (every installed file).
    param([string]$Path)
    $stream = Get-Item -LiteralPath $Path -Stream Zone.Identifier -ErrorAction SilentlyContinue
    if (-not $stream) { return $null }
    $text = Get-Content -LiteralPath $Path -Stream Zone.Identifier -Raw -ErrorAction SilentlyContinue
    foreach ($line in ($text -split "`r?`n")) {
        if ($line.Trim().ToLower().StartsWith("zoneid=")) {
            return [int]($line.Split("=", 2)[1].Trim())
        }
    }
    return -1  # stream present but no ZoneId line: still tagged
}

function Write-Summary {
    param([string[]]$Lines)
    foreach ($l in $Lines) { Write-Host $l }
    if ($env:GITHUB_STEP_SUMMARY) {
        Add-Content -Path $env:GITHUB_STEP_SUMMARY -Value $Lines
    }
}

if (-not (Test-Path -LiteralPath $InstallerPath)) {
    Write-Host "ACCEPT FAIL: no installer at '$InstallerPath'"
    exit 1
}
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path

if (-not $ReportDir) {
    $ReportDir = Join-Path ([System.IO.Path]::GetTempPath()) "ledgertb-accept"
}
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
$installLog = Join-Path $ReportDir "inno-install.log"
$motwReport = Join-Path $ReportDir "motw-report.txt"

$size = (Get-Item -LiteralPath $installer).Length
$sha = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
Write-Host "installer: $installer"
Write-Host "  size:    $size bytes"
Write-Host "  sha256:  $sha"

# --- 1. the installer must look like a browser download ---------------------
if ($SimulateDownload) {
    Write-Host "tagging the installer as an internet download (Zone.Identifier ZoneId=3)"
    Set-Content -LiteralPath $installer -Stream Zone.Identifier -Value "[ZoneTransfer]`r`nZoneId=3"
}

Write-Host "streams on the installer:"
Get-Item -LiteralPath $installer -Stream * | ForEach-Object { Write-Host ("  {0} ({1} bytes)" -f $_.Stream, $_.Length) }
$installerZone = Get-ZoneId $installer
if ($null -eq $installerZone) {
    Write-Host "ACCEPT FAIL: the installer carries no Zone.Identifier stream, so this run would not"
    Write-Host "  exercise the mark-of-the-web path. Download it through a browser (not gh run"
    Write-Host "  download) or pass -SimulateDownload."
    exit 1
}
if ($installerZone -lt 3) {
    Write-Host "ACCEPT FAIL: installer ZoneId is $installerZone; a download carries 3 (Internet)."
    exit 1
}
Write-Host "installer is marked as downloaded (ZoneId=$installerZone)"

# --- 2. silent install -------------------------------------------------------
if ((Test-Path -LiteralPath $InstallDir) -and @(Get-ChildItem -LiteralPath $InstallDir -Force).Count -gt 0) {
    Write-Host "ACCEPT FAIL: '$InstallDir' already has files in it. Remove it first so the"
    Write-Host "  clean-files check covers only what this installer wrote."
    exit 1
}

$setupArgs = @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
    "/DIR=$InstallDir",
    "/LOG=$installLog"
)
Write-Host "running installer: $($setupArgs -join ' ')"
# A GUI-subsystem exe: "&" would neither wait nor return an exit code. Run it
# under Start-Process and trust the exit code. -NoNewWindow makes this a plain
# CreateProcess, so the shell's "Open File - Security Warning" for a tagged
# file cannot pop up and stall an unattended run.
$p = Start-Process -FilePath $installer -ArgumentList $setupArgs -Wait -PassThru -NoNewWindow
if ($p.ExitCode -ne 0) {
    Write-Host "ACCEPT FAIL: installer exited with code $($p.ExitCode)"
    if (Test-Path -LiteralPath $installLog) {
        Write-Host "===== inno-install.log (tail) ====="
        Get-Content -LiteralPath $installLog -Tail 40
    }
    exit 1
}
Write-Host "installer exited 0"

# --- 3. bundle sanity --------------------------------------------------------
$exe = Join-Path $InstallDir "LedgerTB.exe"
$config = Join-Path $InstallDir "_internal\.streamlit\config.toml"
$runtimeDll = Join-Path $InstallDir "_internal\pythonnet\runtime\Python.Runtime.dll"
foreach ($required in @($exe, $config, $runtimeDll)) {
    if (-not (Test-Path -LiteralPath $required)) {
        Write-Host "ACCEPT FAIL: expected installed file is missing: $required"
        exit 1
    }
}
Write-Host "installed exe, .streamlit config and Python.Runtime.dll are present"

# --- 4. nothing installed may carry the mark ---------------------------------
$files = @(Get-ChildItem -LiteralPath $InstallDir -Recurse -File -Force)
$tagged = @()
foreach ($f in $files) {
    $zone = Get-ZoneId $f.FullName
    if ($null -ne $zone) { $tagged += ("{0} (ZoneId={1})" -f $f.FullName, $zone) }
}

$report = @(
    "installer:          $installer",
    "installer sha256:   $sha",
    "installer ZoneId:   $installerZone",
    "install dir:        $InstallDir",
    "files installed:    $($files.Count)",
    "files tagged:       $($tagged.Count)"
)
if ($tagged.Count -gt 0) { $report += ""; $report += $tagged }
Set-Content -LiteralPath $motwReport -Value $report

Write-Summary @(
    "### Installer with mark-of-the-web",
    "",
    "- installer ZoneId: **$installerZone** (Internet zone, as a browser download carries)",
    "- silent install exit code: **0** into ``$InstallDir``",
    "- files installed: **$($files.Count)**",
    "- installed files carrying Zone.Identifier: **$($tagged.Count)**",
    ""
)

if ($tagged.Count -gt 0) {
    Write-Host "ACCEPT FAIL: $($tagged.Count) of $($files.Count) installed files carry mark-of-the-web:"
    $tagged | Select-Object -First 20 | ForEach-Object { Write-Host "  $_" }
    exit 1
}

Write-Host "ACCEPT OK - 0 of $($files.Count) installed files carry mark-of-the-web"
exit 0
