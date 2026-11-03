; Inno Setup script: per-user install, no administrator rights needed
#define AppVersion GetEnv("APP_VERSION")
#if AppVersion == ""
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6B1F2C9E-3A57-4E0B-9C2D-7E5A1B8F4D21}
AppName=Real Book Guitar
AppVersion={#AppVersion}
AppPublisher=Real Book Guitar
DefaultDirName={localappdata}\Programs\Real Book Guitar
DefaultGroupName=Real Book Guitar
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=RealBookGuitar-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\RealBookGuitar.exe

[Files]
Source: "..\dist\RealBookGuitar\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Real Book Guitar"; Filename: "{app}\RealBookGuitar.exe"
Name: "{userdesktop}\Real Book Guitar"; Filename: "{app}\RealBookGuitar.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Run]
; LilyPond builds its font cache on first use (can take a minute on Windows); do it now, not on first print
Filename: "{app}\RealBookGuitar.exe"; Parameters: "--warmup"; StatusMsg: "Preparing the music engraver (about a minute)..."; Flags: runhidden waituntilterminated
Filename: "{app}\RealBookGuitar.exe"; Description: "Start Real Book Guitar"; Flags: nowait postinstall skipifsilent
