"""Native folder chooser for the installed desktop app.

A browser file input intentionally never reveals a local directory path, but
LedgerTB needs the path itself as the assistant's export boundary. The desktop
bundle can ask the operating system directly instead: Finder on macOS and the
standard Folder Browser on Windows. Linux/source runs use Zenity when present.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _starting_folder(initial) -> str:
    candidate = Path(initial or Path.home()).expanduser()
    if not candidate.is_dir():
        candidate = candidate.parent if candidate.parent.is_dir() else Path.home()
    return str(candidate.resolve())


def choose_folder(initial=None) -> str | None:
    """Return a user-selected folder, or ``None`` when they cancel."""
    start = _starting_folder(initial)

    if sys.platform == "darwin":
        script = """
on run argv
    set startFolder to POSIX file (item 1 of argv) as alias
    set pickedFolder to choose folder with prompt "Choose LedgerTB export folder" default location startFolder
    return POSIX path of pickedFolder
end run
"""
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", script, start],
            capture_output=True,
            text=True,
        )
        if result.returncode:
            if "User canceled" in result.stderr or "(-128)" in result.stderr:
                return None
            raise RuntimeError(result.stderr.strip() or "Finder could not choose a folder.")

    elif sys.platform == "win32":
        script = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Choose LedgerTB export folder'
$dialog.ShowNewFolderButton = $true
if (Test-Path -LiteralPath $env:LEDGERTB_FOLDER_PICKER_START) {
    $dialog.SelectedPath = $env:LEDGERTB_FOLDER_PICKER_START
}
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::Out.Write($dialog.SelectedPath)
}
"""
        env = dict(os.environ, LEDGERTB_FOLDER_PICKER_START=start)
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise RuntimeError(
                result.stderr.strip() or "Windows could not choose a folder."
            )

    else:
        zenity = shutil.which("zenity")
        if not zenity:
            raise RuntimeError(
                "No desktop folder chooser is available; enter the folder path instead."
            )
        result = subprocess.run(
            [
                zenity,
                "--file-selection",
                "--directory",
                "--title=Choose LedgerTB export folder",
                f"--filename={start}{os.sep}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 1:  # Zenity's documented cancel response.
            return None
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Could not choose a folder.")

    picked = result.stdout.strip()
    return str(Path(picked).expanduser().resolve()) if picked else None
