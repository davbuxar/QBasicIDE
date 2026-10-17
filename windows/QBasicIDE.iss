; Inno Setup script - builds QBasicIDE_Setup.exe  (run build_windows.bat first to create dist\QBasicIDE.exe)
[Setup]
AppId={{B7C1E5A4-2D3F-4C9A-9E11-5B1F0A7C2D10}
AppName=QBasic IDE
AppVersion=1.0
AppPublisher=QBasic IDE
DefaultDirName={autopf}\QBasic IDE
DefaultGroupName=QBasic IDE
OutputDir=..\installer
OutputBaseFilename=QBasicIDE_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\QBasicIDE.exe
DisableProgramGroupPage=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "assoc"; Description: "Open .bas files with QBasic IDE"; GroupDescription: "File association:"

[Files]
Source: "..\dist\QBasicIDE.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\examples\*.bas"; DestDir: "{app}\examples"; Flags: ignoreversion

[Icons]
Name: "{group}\QBasic IDE"; Filename: "{app}\QBasicIDE.exe"
Name: "{group}\Example programs"; Filename: "{app}\examples"
Name: "{group}\Uninstall QBasic IDE"; Filename: "{uninstallexe}"
Name: "{autodesktop}\QBasic IDE"; Filename: "{app}\QBasicIDE.exe"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.bas"; ValueType: string; ValueData: "QBasicIDE.bas"; Flags: uninsdeletevalue; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\QBasicIDE.bas"; ValueType: string; ValueData: "QBasic program"; Flags: uninsdeletekey; Tasks: assoc
Root: HKA; Subkey: "Software\Classes\QBasicIDE.bas\shell\open\command"; ValueType: string; ValueData: """{app}\QBasicIDE.exe"" ""%1"""; Tasks: assoc

[Run]
Filename: "{app}\QBasicIDE.exe"; Description: "Launch QBasic IDE"; Flags: nowait postinstall skipifsilent
