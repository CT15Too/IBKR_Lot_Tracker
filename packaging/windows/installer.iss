; Inno Setup script for the IBKR Lot Tracker Windows desktop app.
;
; Builds a per-user installer that installs under %LOCALAPPDATA% without
; elevation. The Authenticode-signed installer is also the update artifact;
; silent updates pass /VERYSILENT /CURRENTUSER /NORESTART.

#define MyAppName "IBKR Lot Tracker"
#define MyAppVersion "0.1.0"
#define MyAppExeName "IBKR Lot Tracker.exe"

[Setup]
AppId={{8F3B7A1D-6D4A-4A2B-9E0C-5F1D2B3C4A5E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=IBKR Lot Tracker
DefaultDirName={localappdata}\Programs\IBKR Lot Tracker
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=..\..\assets\icon.ico
OutputDir=..\..\dist\installer
OutputBaseFilename=IBKR-Lot-Tracker-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "..\..\dist\{#MyAppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
