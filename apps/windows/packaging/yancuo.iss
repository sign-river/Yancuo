#ifndef MyAppVersion
  #define MyAppVersion "0.1.0rc1"
#endif
#ifndef MyVersionInfo
  #define MyVersionInfo "0.1.0.1"
#endif

#define MyAppName "研错库"
#define MyAppPublisher "Yancuo"
#define MyAppExeName "Yancuo.exe"

[Setup]
AppId={{79045D33-39AF-4C88-9C82-80539E252AD8}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Yancuo
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=Yancuo-{#MyAppVersion}-windows-x64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoVersion={#MyVersionInfo}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Windows 安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyVersionInfo}
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "dist\Yancuo\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
