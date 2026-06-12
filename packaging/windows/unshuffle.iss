#define AppName "Unshuffle"
#define AppPublisher "UmU"
#ifndef AppVersion
#define AppVersion "1.0.0"
#endif
#ifndef SourceDir
#define SourceDir "..\..\dist\Unshuffle"
#endif
#ifndef OutputDir
#define OutputDir "..\..\dist\installer"
#endif

[Setup]
AppId={{9D84E78F-9EB3-47A7-A42C-86C9AD5F0E46}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=UnshuffleSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\..\icons\app_logo.ico
UninstallDisplayIcon={app}\_internal\icons\app_logo.ico
UninstallDisplayName={#AppName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\Unshuffle.exe"; IconFilename: "{app}\_internal\icons\app_logo.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\Unshuffle.exe"; IconFilename: "{app}\_internal\icons\app_logo.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Run]
Filename: "{app}\Unshuffle.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
