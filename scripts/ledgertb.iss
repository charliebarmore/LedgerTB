; Inno Setup script for the LedgerTB Windows installer.
;
; WHY AN INSTALLER AND NOT A ZIP
; When Windows extracts a downloaded zip, it stamps every extracted file with
; "mark-of-the-web" (Zone.Identifier, ZoneId=3 — the Internet zone). The .NET
; Framework refuses to load a managed assembly carrying that tag, so the
; bundled Python.Runtime.dll would not load, pywebview's Windows backend could
; not start, and LedgerTB died with a Python traceback before any window
; appeared. Observed on a clean Windows 11 machine, 2026-08-05.
;
; An installer writes the files itself, so nothing lands tagged and the app
; simply runs. It also gives a Start Menu entry and a real uninstall, which is
; what a firm's IT person expects to see.
;
; PER-USER, NO ADMIN PROMPT
; PrivilegesRequired=lowest installs under the user's own AppData instead of
; Program Files. Accountants on managed firm laptops often cannot clear an
; admin prompt, and needing one turns "try this" into "file a ticket".
;
; BUILD
;   iscc /DAppVersion=1.1.0 scripts\ledgertb.iss
; Expects the PyInstaller output at dist\LedgerTB\ relative to the repo root.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "LedgerTB"
#define AppPublisher "Ledger Labs LLC"
#define AppURL "https://ledgerlabs.co"
#define AppExeName "LedgerTB.exe"

[Setup]
; Stable AppId — never change it, or upgrades install alongside the old copy
; instead of replacing it.
AppId={{8F3A1C42-6B7D-4E19-9A5C-2D4E8B1F7A30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
VersionInfoVersion={#AppVersion}

; Per-user install: no elevation, no admin prompt.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto

; The installer itself is a single file next to the release assets.
OutputDir=..\installer
OutputBaseFilename=LedgerTB-{#AppVersion}-windows-x64-setup
SetupIconFile=..\LedgerTB.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}

Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

; The app bundles its own Python; 64-bit Windows 10 or later.
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
; The whole PyInstaller onedir output. recursesubdirs+createallsubdirs pulls in
; _internal\.streamlit\ — the dot-prefixed config folder that has gone missing
; from a bundle before and takes the product chrome with it.
Source: "..\dist\LedgerTB\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; The stable AppId makes this an in-place upgrade for ProBooks users. Remove
; only obsolete application files/shortcuts whose names changed; books live
; under the user-data directory and are never installation targets.
Type: files; Name: "{app}\ProBooks.exe"
Type: files; Name: "{group}\ProBooks.lnk"
Type: files; Name: "{autodesktop}\ProBooks.lnk"
Type: files; Name: "{app}\_internal\assets\probooks-mark.png"
Type: files; Name: "{app}\_internal\assets\probooks-wordmark.png"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Open {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove anything the app wrote inside its own install folder (caches, logs).
; Books and the saved key live under %LOCALAPPDATA%\LedgerLabs\LedgerTB and are
; deliberately NOT touched — uninstalling the app must never delete the books.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
