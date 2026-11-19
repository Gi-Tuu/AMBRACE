#define AppName "AMBRACE"
#define AppVersion "3.6.6"
#define Stage GetEnv("AMBRACE_STAGE")

[Setup]
AppId={{7C3E2B1A-9F4D-4A8E-9C2B-3D5E7A1B0C42}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\AiCompanion
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir=E:\Win11Test\Setup
OutputBaseFilename=AiCompanion-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=no
WizardStyle=modern
UninstallDisplayName={#AppName}
DisableProgramGroupPage=yes

[Tasks]
Name: "autostart"; Description: "Start AMBRACE at login (watchdog)"; GroupDescription: "Startup:"
Name: "launchconsole"; Description: "Open server console after install"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "{#Stage}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install-wizard.ps1"" -Unattended -InstallDir ""{app}"" -DataDir ""{app}\data"" -AmbragePort 8000 -CorePort 8001"; StatusMsg: "Configuring AMBRACE..."; Flags: waituntilterminated runhidden
Filename: "{app}\start_watchdog.cmd"; Description: "Start AMBRACE watchdog"; Flags: postinstall nowait runhidden
Filename: "{app}\start_console.cmd"; Description: "Open server console"; Tasks: launchconsole; Flags: postinstall nowait

[Icons]
Name: "{group}\AMBRACE Console"; Filename: "{app}\start_console.cmd"; WorkingDir: "{app}"
Name: "{group}\Stop AMBRACE"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\stop_all.ps1"""; WorkingDir: "{app}"
Name: "{userstartup}\AMBRACE Watchdog"; Filename: "{app}\start_watchdog.cmd"; WorkingDir: "{app}"; Tasks: autostart

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\stop_all.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "StopAMBRACE"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\data"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\secrets"
