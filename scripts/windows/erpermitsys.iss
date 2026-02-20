#define MyAppName "erpermitsys"
#define MyAppPublisher "ERPermitSys"
#define MyAppURL "https://github.com/imyago9/ERPermitSys"

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppExeDir
  #define AppExeDir "dist\erpermitsys"
#endif
#ifndef OutputDir
  #define OutputDir "dist"
#endif

[Setup]
AppId={{8C2ABF22-3B71-4E11-95D8-20C2956A724E}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=erpermitsys-setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\erpermitsys.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
Source: "{#AppExeDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\erpermitsys.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\erpermitsys.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\erpermitsys.exe"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
