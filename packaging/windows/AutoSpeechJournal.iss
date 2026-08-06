#ifndef AppVersion
  #error AppVersion must be supplied from pyproject.toml
#endif
#ifndef AppNumericVersion
  #error AppNumericVersion must be supplied from pyproject.toml
#endif
#ifndef AppPayloadRoot
  #error AppPayloadRoot is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef AppIcon
  #error AppIcon is required
#endif

#define AppName "Auto Speech Journal"
#define AppPublisher "benny7431"
#define AppURL "https://github.com/benny7431/auto-speech-journal"
#define AppExeName "AutoSpeechJournal.exe"
#define CliExeName "AutoSpeechJournal.CLI.exe"

[Setup]
AppId={{7F1AC268-A144-4BB4-B98B-3D5C97E1CB3E}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={localappdata}\Programs\AutoSpeechJournal\app
UninstallFilesDir={localappdata}\Programs\AutoSpeechJournal\uninstall
DisableDirPage=yes
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=AutoSpeechJournal-Setup-{#AppVersion}-x64
SetupIconFile={#AppIcon}
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
AppMutex=Local\AutoSpeechJournal
SetupLogging=yes
UsePreviousAppDir=no
MinVersion=10.0.10240
VersionInfoVersion={#AppNumericVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} per-user installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesetraditional"; MessagesFile: "languages\ChineseTraditional.isl"

[Files]
Source: "{#AppPayloadRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#CliExeName}"; Parameters: "startup disable"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "DisableStartup"
