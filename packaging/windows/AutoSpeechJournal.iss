#ifndef AppVersion
  #error AppVersion must be supplied from pyproject.toml by build_windows_installer.ps1
#endif
#ifndef AppNumericVersion
  #error AppNumericVersion must be supplied from pyproject.toml by build_windows_installer.ps1
#endif
#ifndef AppPayloadRoot
  #error AppPayloadRoot is required
#endif
#ifndef LauncherRoot
  #error LauncherRoot is required
#endif
#ifndef ManifestRoot
  #error ManifestRoot is required
#endif
#ifndef OutputDir
  #error OutputDir is required
#endif
#ifndef AppIcon
  #error AppIcon is required
#endif
#ifndef PayloadInstalledBytes
  #define PayloadInstalledBytes 0
#endif
#ifndef ModelDownloadBytes
  #define ModelDownloadBytes 0
#endif
#ifndef ModelInstalledBytes
  #define ModelInstalledBytes 0
#endif
#ifndef GpuDownloadBytes
  #define GpuDownloadBytes 0
#endif
#ifndef GpuInstalledBytes
  #define GpuInstalledBytes 0
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
DefaultDirName={localappdata}\Programs\AutoSpeechJournal
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
CloseApplications=no
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
AppModifyPath="{app}\AutoSpeechJournal-Maintenance.exe"
MinVersion=10.0.10240
VersionInfoVersion={#AppNumericVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} per-user installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesetraditional"; MessagesFile: "languages\ChineseTraditional.isl"

[Tasks]
Name: "gpu"; Description: "{cm:GpuAcceleration}"; GroupDescription: "{cm:OptionalComponents}"
Name: "gpu\force"; Description: "{cm:ForceGpu}"; Flags: unchecked

[Files]
Source: "{#AppPayloadRoot}\*"; DestDir: "{app}\versions\{#AppVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#LauncherRoot}\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#LauncherRoot}\{#CliExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#ManifestRoot}\runtime-models-v1.json"; DestDir: "{app}\manifests"; Flags: ignoreversion
Source: "{#ManifestRoot}\cuda-runtime-v1.json"; DestDir: "{app}\manifests"; Flags: ignoreversion
Source: "provision_runner.ps1"; Flags: dontcopy
Source: "migrate_legacy_task.ps1"; Flags: dontcopy

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Repair or reinstall {#AppName}"; Filename: "{app}\AutoSpeechJournal-Maintenance.exe"
Name: "{group}\Repair models"; Filename: "{app}\{#CliExeName}"; Parameters: "repair models --manifest ""{app}\manifests\runtime-models-v1.json"""
Name: "{group}\Repair GPU acceleration"; Filename: "{app}\{#CliExeName}"; Parameters: "repair gpu --manifest ""{app}\manifests\cuda-runtime-v1.json"""
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#CliExeName}"; Parameters: "startup disable"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "DisableStartup"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\versions"
Type: files; Name: "{app}\current.json"
Type: files; Name: "{app}\current.previous.json"
Type: files; Name: "{app}\legacy-migration.json"
Type: files; Name: "{app}\AutoSpeechJournal-Maintenance.exe"

[CustomMessages]
english.OptionalComponents=Optional components
english.GpuAcceleration=Auto-detect and install NVIDIA GPU acceleration (CPU fallback is always available)
english.ForceGpu=Force the GPU attempt even if the driver recommendation is not met
english.ModelIncomplete=The application was installed, but recognition models are not ready. Use "Repair models" from the Start menu after reconnecting.
english.GpuFallback=GPU acceleration could not be activated. The application will continue with the CPU fallback; diagnostics are in the application log.
english.ProvisionTitle=Download local recognition components
english.ProvisionDescription=Setup downloads verified, commit-pinned runtime models directly from Hugging Face. Downloads can resume after cancellation.
english.ProvisionWaiting=Preparing download...
english.ProvisionCancel=Cancel download
english.ProvisionCancelled=Download cancelled. The installed application is kept; use Repair to resume later.
english.UninstallHeading=Choose optional local data to remove
english.UninstallBody=The application is removed by default. Journals outside the application data directory are never deleted.
english.RemoveModels=Remove downloaded models and download caches
english.RemoveState=Remove local configuration, SQLite state, pending audio, and logs
english.StateConfirmation=This permanently removes local state and pending audio. Markdown journal folders are not deleted. Continue?
chinesetraditional.OptionalComponents=選用元件
chinesetraditional.GpuAcceleration=自動偵測並安裝 NVIDIA GPU 加速（始終保留 CPU fallback）
chinesetraditional.ForceGpu=即使驅動條件不符建議，仍強制嘗試 GPU
chinesetraditional.ModelIncomplete=程式已安裝，但辨識模型尚未就緒。連線後請從開始選單執行「Repair models」。
chinesetraditional.GpuFallback=GPU 加速無法啟用，程式會繼續使用 CPU fallback；診斷資料已寫入應用程式日誌。
chinesetraditional.ProvisionTitle=下載本機辨識元件
chinesetraditional.ProvisionDescription=Setup 會直接從 Hugging Face 下載固定 commit 且經雜湊驗證的執行模型；取消後可從 Repair 繼續。
chinesetraditional.ProvisionWaiting=正在準備下載…
chinesetraditional.ProvisionCancel=取消下載
chinesetraditional.ProvisionCancelled=下載已取消。已安裝程式會保留，之後可使用 Repair 繼續。
chinesetraditional.UninstallHeading=選擇是否另外移除本機資料
chinesetraditional.UninstallBody=預設只移除程式。應用資料夾以外的 Markdown 日記永遠不會被刪除。
chinesetraditional.RemoveModels=移除已下載的模型與下載快取
chinesetraditional.RemoveState=移除本機設定、SQLite 狀態、待處理音訊與日誌
chinesetraditional.StateConfirmation=這會永久移除本機狀態與待處理音訊。Markdown 日記資料夾不會被刪除。確定繼續？

[Code]
function SetTimer(hWnd, nIDEvent, uElapse, lpTimerFunc: Longword): Longword;
external 'SetTimer@user32.dll stdcall';

function KillTimer(hWnd, nIDEvent: Longword): Boolean;
external 'KillTimer@user32.dll stdcall';

var
  ModelProvisionFailed: Boolean;
  GpuProvisionFailed: Boolean;
  RemoveModelsSelected: Boolean;
  RemoveStateSelected: Boolean;
  ProvisionPage: TWizardPage;
  ProvisionStatusLabel: TNewStaticText;
  ProvisionDetailLabel: TNewStaticText;
  ProvisionProgressBar: TNewProgressBar;
  ProvisionTimer: Longword;
  ProvisionPhase: Integer;
  ProvisionActive: Boolean;
  ProvisionPidFile: String;
  ProvisionExitFile: String;
  ProvisionProgressFile: String;
  ProvisionProcessId: Integer;
  OriginalCancelCaption: String;

function CmdLineParamExists(Name: String): Boolean;
var
  Index: Integer;
  Argument: String;
begin
  Result := False;
  for Index := 1 to ParamCount do
  begin
    Argument := ParamStr(Index);
    if (CompareText(Argument, '/' + Name) = 0) or
       (CompareText(Argument, '-' + Name) = 0) then
    begin
      Result := True;
      exit;
    end;
  end;
end;

function PosFrom(SubString, Value: String; Offset: Integer): Integer;
var
  RelativePosition: Integer;
begin
  Result := 0;
  if Offset < 1 then
    Offset := 1;
  RelativePosition := Pos(SubString, Copy(Value, Offset, Length(Value)));
  if RelativePosition > 0 then
    Result := Offset + RelativePosition - 1;
end;

function DirectorySize(Path: String): Int64;
var
  FindRec: TFindRec;
  ChildPath: String;
  FileBytes: Int64;
begin
  Result := 0;
  if FindFirst(AddBackslash(Path) + '*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          ChildPath := AddBackslash(Path) + FindRec.Name;
          if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
            Result := Result + DirectorySize(ChildPath)
          else if FileSize64(ChildPath, FileBytes) then
            Result := Result + FileBytes;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function CheckDiskPreflight(): String;
var
  FreeBytes: Int64;
  TotalBytes: Int64;
  ExistingTargetBytes: Int64;
  NewPayloadBytes: Int64;
  MaintenanceSetupBytes: Int64;
  ExistingMaintenanceBytes: Int64;
  OptionalBytes: Int64;
  RequiredBytes: Int64;
begin
  Result := '';
  ExistingTargetBytes := DirectorySize(
    ExpandConstant('{app}\versions\{#AppVersion}'));
  NewPayloadBytes := {#PayloadInstalledBytes} - ExistingTargetBytes;
  if NewPayloadBytes < 0 then
    NewPayloadBytes := 0;
  MaintenanceSetupBytes := 0;
  ExistingMaintenanceBytes := 0;
  if CompareText(
       ExpandConstant('{srcexe}'),
       ExpandConstant('{app}\AutoSpeechJournal-Maintenance.exe')) <> 0 then
  begin
    FileSize64(ExpandConstant('{srcexe}'), MaintenanceSetupBytes);
    FileSize64(
      ExpandConstant('{app}\AutoSpeechJournal-Maintenance.exe'),
      ExistingMaintenanceBytes);
    MaintenanceSetupBytes := MaintenanceSetupBytes - ExistingMaintenanceBytes;
    if MaintenanceSetupBytes < 0 then
      MaintenanceSetupBytes := 0;
  end;
  OptionalBytes := 0;
  if not CmdLineParamExists('NOMODELS') then
    OptionalBytes := OptionalBytes + {#ModelDownloadBytes} + {#ModelInstalledBytes};
  if WizardIsTaskSelected('gpu') and not CmdLineParamExists('NOGPU') then
    OptionalBytes := OptionalBytes + {#GpuDownloadBytes} + {#GpuInstalledBytes};

  RequiredBytes :=
    (NewPayloadBytes + MaintenanceSetupBytes + OptionalBytes) * 12 div 10;
  if GetSpaceOnDisk64(ExpandConstant('{localappdata}'), FreeBytes, TotalBytes) and
     (FreeBytes < RequiredBytes) then
  begin
    Result := Format(
      'Auto Speech Journal needs %d MB free for the new version, downloads, rollback, and a 20%% safety margin; only %d MB is available.', [RequiredBytes div 1048576, FreeBytes div 1048576]);
  end;
end;

function CurrentVersionFromManifest(Path: String): String;
var
  Contents: AnsiString;
  Compact: String;
  Marker: String;
  P1: Integer;
  P2: Integer;
begin
  Result := '';
  if not LoadStringFromFile(Path, Contents) then
    exit;
  Compact := Contents;
  StringChangeEx(Compact, ' ', '', True);
  StringChangeEx(Compact, #9, '', True);
  StringChangeEx(Compact, #13, '', True);
  StringChangeEx(Compact, #10, '', True);
  Marker := '"version":"';
  P1 := Pos(Marker, Compact);
  if P1 = 0 then
    exit;
  P1 := P1 + Length(Marker);
  P2 := P1;
  while (P2 <= Length(Compact)) and (Compact[P2] <> '"') do
    P2 := P2 + 1;
  if P2 <= Length(Compact) then
    Result := Copy(Compact, P1, P2 - P1);
end;

function VersionedCliFromManifest(ManifestPath: String): String;
var
  Version: String;
  Candidate: String;
begin
  Result := '';
  Version := CurrentVersionFromManifest(ManifestPath);
  if Version = '' then
    exit;
  Candidate := ExpandConstant('{app}\versions\') + Version + '\{#CliExeName}';
  if FileExists(Candidate) then
    Result := Candidate;
end;

function ShutdownCliPath(): String;
var
  VersionsRoot: String;
  FindRec: TFindRec;
  Candidate: String;
begin
  Result := VersionedCliFromManifest(ExpandConstant('{app}\current.json'));
  if Result = '' then
    Result := VersionedCliFromManifest(ExpandConstant('{app}\current.previous.json'));
  if Result <> '' then
    exit;

  VersionsRoot := ExpandConstant('{app}\versions');
  if FindFirst(AddBackslash(VersionsRoot) + '*', FindRec) then
  begin
    try
      repeat
        if ((FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and
           (FindRec.Name <> '.') and (FindRec.Name <> '..') then
        begin
          Candidate := AddBackslash(VersionsRoot) + FindRec.Name + '\{#CliExeName}';
          if FileExists(Candidate) then
          begin
            Result := Candidate;
            exit;
          end;
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

function RequestGracefulShutdown(): String;
var
  ResultCode: Integer;
  CliPath: String;
begin
  Result := '';
  CliPath := ShutdownCliPath();
  if CliPath <> '' then
  begin
    if not Exec(
      CliPath,
      'request-shutdown --timeout 30',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode) or (ResultCode <> 0) then
    begin
      Result := 'The running application did not stop safely. Use System status > Exit application, then run Setup again.';
      exit;
    end;
  end;

  if CheckForMutexes('Local\AutoSpeechJournal') then
    Result := 'Auto Speech Journal is still running. Setup will not force-close it; exit the application and retry.';
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := CheckDiskPreflight();
  if Result = '' then
    Result := RequestGracefulShutdown();
end;

procedure ActivateInstalledVersion();
var
  CurrentPath: String;
  PreviousPath: String;
  TemporaryPath: String;
  PreviousVersion: String;
  CurrentIsValid: Boolean;
  Manifest: String;
begin
  CurrentPath := ExpandConstant('{app}\current.json');
  PreviousPath := ExpandConstant('{app}\current.previous.json');
  TemporaryPath := ExpandConstant('{app}\current.json.tmp');
  PreviousVersion := CurrentVersionFromManifest(CurrentPath);
  CurrentIsValid := VersionedCliFromManifest(CurrentPath) <> '';
  if not CurrentIsValid then
    PreviousVersion := CurrentVersionFromManifest(PreviousPath);

  Manifest := Format(
    '{"schema_version":1,"version":"%s","previous_version":"%s","targets":{"gui":"{#AppExeName}","cli":"{#CliExeName}"}}' + #13 + #10, ['{#AppVersion}', PreviousVersion]);
  DeleteFile(TemporaryPath);
  if not SaveStringToFile(TemporaryPath, AnsiString(Manifest), False) then
    RaiseException('Could not stage current.json. The previous version remains active.');

  if CurrentIsValid then
  begin
    DeleteFile(PreviousPath);
    if FileExists(CurrentPath) and not RenameFile(CurrentPath, PreviousPath) then
    begin
      DeleteFile(TemporaryPath);
      RaiseException('Could not preserve the previous current.json. The previous version remains active.');
    end;
  end
  else if FileExists(CurrentPath) and not DeleteFile(CurrentPath) then
  begin
    DeleteFile(TemporaryPath);
    RaiseException('Could not replace the invalid current.json. Run Setup again.');
  end;
  if not RenameFile(TemporaryPath, CurrentPath) then
  begin
    if FileExists(PreviousPath) and RenameFile(PreviousPath, CurrentPath) and
       (VersionedCliFromManifest(CurrentPath) <> '') then
      RaiseException('Could not activate the new version. The previous version was restored.')
    else
      RaiseException(
        'Could not activate the new version, and current.json could not be restored. ' +
        'Run Setup again to repair the installation.');
  end;
end;

procedure CleanupOldVersions();
var
  VersionsRoot: String;
  ActiveVersion: String;
  RollbackVersion: String;
  FindRec: TFindRec;
  Candidate: String;
begin
  VersionsRoot := ExpandConstant('{app}\versions');
  ActiveVersion := CurrentVersionFromManifest(ExpandConstant('{app}\current.json'));
  RollbackVersion := CurrentVersionFromManifest(
    ExpandConstant('{app}\current.previous.json'));
  if (ActiveVersion = '') or not DirExists(VersionsRoot) then
    exit;

  if FindFirst(AddBackslash(VersionsRoot) + '*', FindRec) then
  begin
    try
      repeat
        if ((FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0) and
           (FindRec.Name <> '.') and (FindRec.Name <> '..') and
           (CompareText(FindRec.Name, ActiveVersion) <> 0) and
           ((RollbackVersion = '') or
            (CompareText(FindRec.Name, RollbackVersion) <> 0)) then
        begin
          Candidate := AddBackslash(VersionsRoot) + FindRec.Name;
          if not DelTree(Candidate, True, True, True) then
            Log('Could not remove obsolete version directory: ' + Candidate);
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure PreserveMaintenanceSetup();
var
  SourcePath: String;
  DestinationPath: String;
begin
  SourcePath := ExpandConstant('{srcexe}');
  DestinationPath := ExpandConstant('{app}\AutoSpeechJournal-Maintenance.exe');
  if CompareText(SourcePath, DestinationPath) = 0 then
    exit;
  if not CopyFile(SourcePath, DestinationPath, False) then
    RaiseException('Could not preserve Setup for future repair or reinstall.');
end;

procedure MigrateLegacyInstall();
var
  LegacyAppRoot: String;
  RunnerPath: String;
  MarkerPath: String;
  Arguments: String;
  FallbackMarker: String;
  ResultCode: Integer;
begin
  LegacyAppRoot := ExpandConstant('{localappdata}\AutoSpeechJournal\app');
  if not DirExists(LegacyAppRoot) then
    exit;
  ExtractTemporaryFile('migrate_legacy_task.ps1');
  RunnerPath := ExpandConstant('{tmp}\migrate_legacy_task.ps1');
  MarkerPath := ExpandConstant('{app}\legacy-migration.json');
  Arguments := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    RunnerPath + '" -LegacyAppRoot "' + LegacyAppRoot + '" -StableCli "' +
    ExpandConstant('{app}\{#CliExeName}') + '" -MarkerPath "' + MarkerPath + '"';
  ResultCode := -1;
  if not Exec(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Arguments,
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode) or (ResultCode <> 0) then
  begin
    Log(Format(
      'Legacy task migration helper failed with exit code %d. ' +
      'The verified new version remains active; legacy state and tasks are preserved.', [ResultCode]));
    FallbackMarker :=
      '{"schema_version":1,"legacy_app_retained":true,' +
      '"legacy_task_status":"manual_start_migration_helper_failed",' +
      '"manual_start_required":true}' + #13 + #10;
    SaveStringToFile(MarkerPath, AnsiString(FallbackMarker), False);
  end;
end;

procedure WriteProvisionFailure(Name: String; ResultCode: Integer);
var
  LogDir: String;
  Detail: String;
begin
  LogDir := ExpandConstant('{localappdata}\AutoSpeechJournal\logs');
  ForceDirectories(LogDir);
  Detail := Format('%s failed during Setup with exit code %d.' + #13 + #10, [Name, ResultCode]);
  SaveStringToFile(AddBackslash(LogDir) + 'installer-provisioning-error.txt', AnsiString(Detail), True);
end;

procedure ProvisionOptionalComponentsSilent();
var
  CliPath: String;
  ProgressPath: String;
  Arguments: String;
  ResultCode: Integer;
begin
  CliPath := ExpandConstant('{app}\{#CliExeName}');
  ProgressPath := ExpandConstant('{localappdata}\AutoSpeechJournal\provision-progress.json');

  if not CmdLineParamExists('NOMODELS') then
  begin
    Arguments := 'provision --manifest "' +
      ExpandConstant('{app}\manifests\runtime-models-v1.json') + '" --progress-json "' +
      ProgressPath + '"';
    if not Exec(CliPath, Arguments, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or
       (ResultCode <> 0) then
    begin
      ModelProvisionFailed := True;
      WriteProvisionFailure('Model provisioning', ResultCode);
    end;
  end;

  if WizardIsTaskSelected('gpu') and not CmdLineParamExists('NOGPU') then
  begin
    Arguments := 'repair gpu --manifest "' +
      ExpandConstant('{app}\manifests\cuda-runtime-v1.json') +
      '" --progress-json "' + ProgressPath + '"';
    if WizardIsTaskSelected('gpu\force') then
      Arguments := Arguments + ' --force-gpu';
    if not Exec(CliPath, Arguments, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) or
       (ResultCode <> 0) then
    begin
      GpuProvisionFailed := True;
      WriteProvisionFailure('GPU provisioning', ResultCode);
    end;
  end;

end;

function JsonInteger(Contents: String; Name: String): Int64;
var
  Compact: String;
  Marker: String;
  P1: Integer;
  P2: Integer;
begin
  Result := 0;
  Compact := Contents;
  StringChangeEx(Compact, ' ', '', True);
  StringChangeEx(Compact, #13, '', True);
  StringChangeEx(Compact, #10, '', True);
  Marker := '"' + Name + '":';
  P1 := Pos(Marker, Compact);
  if P1 = 0 then
    exit;
  P1 := P1 + Length(Marker);
  P2 := P1;
  while (P2 <= Length(Compact)) and
        (Compact[P2] >= '0') and (Compact[P2] <= '9') do
    P2 := P2 + 1;
  Result := StrToInt64Def(Copy(Compact, P1, P2 - P1), 0);
end;

function JsonString(Contents: String; Name: String): String;
var
  Compact: String;
  Marker: String;
  P1: Integer;
  P2: Integer;
begin
  Result := '';
  Compact := Contents;
  StringChangeEx(Compact, #13, '', True);
  StringChangeEx(Compact, #10, '', True);
  Marker := '"' + Name + '"';
  P1 := Pos(Marker, Compact);
  if P1 = 0 then
    exit;
  P1 := PosFrom(':', Compact, P1 + Length(Marker));
  if P1 = 0 then
    exit;
  P1 := PosFrom('"', Compact, P1 + 1);
  if P1 = 0 then
    exit;
  P1 := P1 + 1;
  P2 := PosFrom('"', Compact, P1);
  if P2 > P1 then
    Result := Copy(Compact, P1, P2 - P1);
end;

procedure UpdateProvisionDisplay();
var
  Raw: AnsiString;
  Contents: String;
  Completed: Int64;
  Total: Int64;
  Eta: Int64;
  Status: String;
  AssetName: String;
begin
  if not LoadStringFromFile(ProvisionProgressFile, Raw) then
    exit;
  Contents := String(Raw);
  Completed := JsonInteger(Contents, 'completed');
  Total := JsonInteger(Contents, 'total');
  Eta := JsonInteger(Contents, 'eta_seconds');
  Status := JsonString(Contents, 'status');
  AssetName := JsonString(Contents, 'asset');
  if Status = '' then
    Status := ExpandConstant('{cm:ProvisionWaiting}');
  if AssetName <> '' then
    ProvisionStatusLabel.Caption := Status + ': ' + AssetName
  else
    ProvisionStatusLabel.Caption := Status;
  if Total > 0 then
  begin
    ProvisionProgressBar.Position := Integer((Completed * 1000) div Total);
    if Eta > 0 then
      ProvisionDetailLabel.Caption := Format('%d / %d MB - ETA %d min %d sec', [Completed div 1048576, Total div 1048576, Eta div 60, Eta mod 60])
    else
      ProvisionDetailLabel.Caption := Format('%d / %d MB', [Completed div 1048576, Total div 1048576]);
  end;
end;

procedure StartProvisionPhase(Phase: Integer);
var
  RunnerPath: String;
  CliPath: String;
  ManifestPath: String;
  Arguments: String;
  ErrorCode: Integer;
begin
  ProvisionPhase := Phase;
  ProvisionPidFile := ExpandConstant('{tmp}\asj-provision.pid');
  ProvisionExitFile := ExpandConstant('{tmp}\asj-provision.exit');
  ProvisionProgressFile := ExpandConstant('{localappdata}\AutoSpeechJournal\provision-progress.json');
  ProvisionProcessId := 0;
  DeleteFile(ProvisionPidFile);
  DeleteFile(ProvisionExitFile);
  DeleteFile(ProvisionExitFile + '.error');
  DeleteFile(ProvisionProgressFile);
  ExtractTemporaryFile('provision_runner.ps1');
  RunnerPath := ExpandConstant('{tmp}\provision_runner.ps1');
  CliPath := ExpandConstant('{app}\{#CliExeName}');

  if Phase = 1 then
  begin
    ManifestPath := ExpandConstant('{app}\manifests\runtime-models-v1.json');
    ProvisionStatusLabel.Caption := 'Recognition models';
    Arguments := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
      RunnerPath + '" -Executable "' + CliPath + '" -Mode models -Manifest "' +
      ManifestPath + '" -ProgressJson "' + ProvisionProgressFile +
      '" -PidFile "' + ProvisionPidFile + '" -ExitFile "' + ProvisionExitFile + '"';
  end
  else
  begin
    ManifestPath := ExpandConstant('{app}\manifests\cuda-runtime-v1.json');
    ProvisionStatusLabel.Caption := 'NVIDIA GPU runtime';
    Arguments := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
      RunnerPath + '" -Executable "' + CliPath + '" -Mode gpu -Manifest "' +
      ManifestPath + '" -ProgressJson "' + ProvisionProgressFile +
      '" -PidFile "' + ProvisionPidFile + '" -ExitFile "' + ProvisionExitFile + '"';
    if WizardIsTaskSelected('gpu\force') then
      Arguments := Arguments + ' -ForceGpu';
  end;

  ProvisionDetailLabel.Caption := ExpandConstant('{cm:ProvisionWaiting}');
  ProvisionProgressBar.Position := 0;
  if not Exec(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    Arguments,
    '',
    SW_HIDE,
    ewNoWait,
    ErrorCode) then
  begin
    if Phase = 1 then
      ModelProvisionFailed := True
    else
      GpuProvisionFailed := True;
    WriteProvisionFailure('Provision runner startup', ErrorCode);
    ProvisionActive := False;
    WizardForm.NextButton.Enabled := True;
    exit;
  end;
  ProvisionProcessId := ErrorCode;
  ProvisionActive := True;
end;

procedure FinishProvisionPhase(ResultCode: Integer);
var
  FinishedPhase: Integer;
begin
  FinishedPhase := ProvisionPhase;
  ProvisionActive := False;
  if ResultCode <> 0 then
  begin
    if FinishedPhase = 1 then
      ModelProvisionFailed := True
    else
      GpuProvisionFailed := True;
    WriteProvisionFailure('Provisioning', ResultCode);
  end;

  if (FinishedPhase = 1) and WizardIsTaskSelected('gpu') and
     not CmdLineParamExists('NOGPU') then
  begin
    StartProvisionPhase(2);
    exit;
  end;
  ProvisionProgressBar.Position := 1000;
  ProvisionStatusLabel.Caption := 'Provisioning complete';
  ProvisionDetailLabel.Caption := 'Select Next to finish Setup.';
  WizardForm.NextButton.Enabled := True;
  WizardForm.CancelButton.Caption := OriginalCancelCaption;
end;

procedure ProvisionTimerTick(Arg1, Arg2, Arg3, Arg4: Longword);
var
  Raw: AnsiString;
  ResultCode: Integer;
begin
  if not ProvisionActive then
    exit;
  UpdateProvisionDisplay();
  if LoadStringFromFile(ProvisionExitFile, Raw) then
  begin
    ResultCode := StrToIntDef(Trim(String(Raw)), 1);
    FinishProvisionPhase(ResultCode);
  end;
end;

procedure InitializeWizard();
begin
  ProvisionPage := CreateCustomPage(
    wpInstalling,
    ExpandConstant('{cm:ProvisionTitle}'),
    ExpandConstant('{cm:ProvisionDescription}'));
  ProvisionStatusLabel := TNewStaticText.Create(ProvisionPage);
  ProvisionStatusLabel.Parent := ProvisionPage.Surface;
  ProvisionStatusLabel.Left := 0;
  ProvisionStatusLabel.Top := ScaleY(16);
  ProvisionStatusLabel.Width := ProvisionPage.SurfaceWidth;
  ProvisionStatusLabel.Caption := ExpandConstant('{cm:ProvisionWaiting}');
  ProvisionStatusLabel.Font.Style := [fsBold];

  ProvisionProgressBar := TNewProgressBar.Create(ProvisionPage);
  ProvisionProgressBar.Parent := ProvisionPage.Surface;
  ProvisionProgressBar.Left := 0;
  ProvisionProgressBar.Top := ScaleY(52);
  ProvisionProgressBar.Width := ProvisionPage.SurfaceWidth;
  ProvisionProgressBar.Min := 0;
  ProvisionProgressBar.Max := 1000;

  ProvisionDetailLabel := TNewStaticText.Create(ProvisionPage);
  ProvisionDetailLabel.Parent := ProvisionPage.Surface;
  ProvisionDetailLabel.Left := 0;
  ProvisionDetailLabel.Top := ScaleY(86);
  ProvisionDetailLabel.Width := ProvisionPage.SurfaceWidth;
  ProvisionDetailLabel.Caption := ExpandConstant('{cm:ProvisionWaiting}');

  ProvisionTimer := SetTimer(0, 0, 250, CreateCallback(@ProvisionTimerTick));
  if ProvisionTimer = 0 then
    RaiseException('Could not initialize the provisioning progress timer.');
  OriginalCancelCaption := WizardForm.CancelButton.Caption;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = ProvisionPage.ID) and
    (CmdLineParamExists('NOMODELS')) and
    (not WizardIsTaskSelected('gpu') or CmdLineParamExists('NOGPU'));
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
var
  Raw: AnsiString;
  ProcessId: Integer;
  ResultCode: Integer;
begin
  if (CurPageID <> ProvisionPage.ID) or not ProvisionActive then
    exit;
  Cancel := False;
  Confirm := False;
  if MsgBox(ExpandConstant('{cm:ProvisionCancelled}'), mbConfirmation, MB_YESNO) <> IDYES then
    exit;
  ProcessId := ProvisionProcessId;
  if (ProcessId <= 0) and LoadStringFromFile(ProvisionPidFile, Raw) then
  begin
    ProcessId := StrToIntDef(Trim(String(Raw)), 0);
  end;
  if ProcessId > 0 then
    Exec(ExpandConstant('{sys}\taskkill.exe'),
      Format('/PID %d /T /F', [ProcessId]), '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode);
  if ProvisionPhase = 1 then
    ModelProvisionFailed := True
  else
    GpuProvisionFailed := True;
  ProvisionActive := False;
  ProvisionStatusLabel.Caption := ExpandConstant('{cm:ProvisionCancelled}');
  ProvisionDetailLabel.Caption := '';
  WizardForm.NextButton.Enabled := True;
  WizardForm.CancelButton.Caption := OriginalCancelCaption;
end;

procedure DeinitializeSetup();
begin
  if ProvisionTimer <> 0 then
    KillTimer(0, ProvisionTimer);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ProbePath: String;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    ProbePath := ExpandConstant('{app}\versions\{#AppVersion}\{#CliExeName}');
    if not Exec(
      ProbePath,
      'installer-probe --isolated',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode) or (ResultCode <> 0) then
    begin
      RaiseException('The isolated readiness probe failed. The previous version remains active.');
    end;
    PreserveMaintenanceSetup();
    ActivateInstalledVersion();
    MigrateLegacyInstall();
    CleanupOldVersions();
    if WizardSilent then
      ProvisionOptionalComponentsSilent();
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = ProvisionPage.ID then
  begin
    WizardForm.NextButton.Enabled := False;
    WizardForm.CancelButton.Caption := ExpandConstant('{cm:ProvisionCancel}');
    if not CmdLineParamExists('NOMODELS') then
      StartProvisionPhase(1)
    else if WizardIsTaskSelected('gpu') and not CmdLineParamExists('NOGPU') then
      StartProvisionPhase(2)
    else
      WizardForm.NextButton.Enabled := True;
  end
  else if CurPageID = wpFinished then
  begin
    if ModelProvisionFailed then
      WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13 + #10 +
        ExpandConstant('{cm:ModelIncomplete}');
    if GpuProvisionFailed then
      WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13 + #10 +
        ExpandConstant('{cm:GpuFallback}');
  end;
end;

function InitializeUninstall(): Boolean;
var
  OptionsForm: TSetupForm;
  HeadingLabel: TNewStaticText;
  BodyLabel: TNewStaticText;
  RemoveModelsCheck: TNewCheckBox;
  RemoveStateCheck: TNewCheckBox;
  OkButton: TNewButton;
  CancelButton: TNewButton;
  ShutdownError: String;
begin
  Result := False;
  RemoveModelsSelected := False;
  RemoveStateSelected := False;
  ShutdownError := RequestGracefulShutdown();
  if ShutdownError <> '' then
  begin
    Log('Uninstall cancelled: ' + ShutdownError);
    if not UninstallSilent then
      MsgBox(
        ShutdownError + #13 + #10 + 'Uninstall was cancelled.',
        mbError,
        MB_OK);
    exit;
  end;
  if UninstallSilent then
  begin
    Result := True;
    exit;
  end;
  OptionsForm := CreateCustomForm(ScaleX(520), ScaleY(230), False, False);
  try
    OptionsForm.Caption := '{#AppName}';
    OptionsForm.Position := poScreenCenter;

    HeadingLabel := TNewStaticText.Create(OptionsForm);
    HeadingLabel.Parent := OptionsForm;
    HeadingLabel.Left := ScaleX(20);
    HeadingLabel.Top := ScaleY(16);
    HeadingLabel.Width := ScaleX(480);
    HeadingLabel.Caption := ExpandConstant('{cm:UninstallHeading}');
    HeadingLabel.Font.Style := [fsBold];

    BodyLabel := TNewStaticText.Create(OptionsForm);
    BodyLabel.Parent := OptionsForm;
    BodyLabel.Left := ScaleX(20);
    BodyLabel.Top := ScaleY(48);
    BodyLabel.Width := ScaleX(480);
    BodyLabel.Height := ScaleY(44);
    BodyLabel.AutoSize := False;
    BodyLabel.WordWrap := True;
    BodyLabel.Caption := ExpandConstant('{cm:UninstallBody}');

    RemoveModelsCheck := TNewCheckBox.Create(OptionsForm);
    RemoveModelsCheck.Parent := OptionsForm;
    RemoveModelsCheck.Left := ScaleX(20);
    RemoveModelsCheck.Top := ScaleY(106);
    RemoveModelsCheck.Width := ScaleX(480);
    RemoveModelsCheck.Caption := ExpandConstant('{cm:RemoveModels}');
    RemoveModelsCheck.Checked := False;

    RemoveStateCheck := TNewCheckBox.Create(OptionsForm);
    RemoveStateCheck.Parent := OptionsForm;
    RemoveStateCheck.Left := ScaleX(20);
    RemoveStateCheck.Top := ScaleY(136);
    RemoveStateCheck.Width := ScaleX(480);
    RemoveStateCheck.Caption := ExpandConstant('{cm:RemoveState}');
    RemoveStateCheck.Checked := False;

    OkButton := TNewButton.Create(OptionsForm);
    OkButton.Parent := OptionsForm;
    OkButton.Left := ScaleX(332);
    OkButton.Top := ScaleY(184);
    OkButton.Width := ScaleX(80);
    OkButton.Caption := SetupMessage(msgButtonOK);
    OkButton.ModalResult := mrOk;
    OptionsForm.ActiveControl := OkButton;

    CancelButton := TNewButton.Create(OptionsForm);
    CancelButton.Parent := OptionsForm;
    CancelButton.Left := ScaleX(420);
    CancelButton.Top := ScaleY(184);
    CancelButton.Width := ScaleX(80);
    CancelButton.Caption := SetupMessage(msgButtonCancel);
    CancelButton.ModalResult := mrCancel;

    if OptionsForm.ShowModal() <> mrOk then
      exit;
    RemoveModelsSelected := RemoveModelsCheck.Checked;
    RemoveStateSelected := RemoveStateCheck.Checked;
    if RemoveStateSelected and
       (MsgBox(ExpandConstant('{cm:StateConfirmation}'), mbConfirmation, MB_YESNO) <> IDYES) then
      exit;
    Result := True;
  finally
    OptionsForm.Free;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  RuntimeRoot: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    if CheckForMutexes('Local\AutoSpeechJournal') then
    begin
      Log('Optional runtime data was preserved because the application is running.');
      exit;
    end;
    RuntimeRoot := ExpandConstant('{localappdata}\AutoSpeechJournal');
    if RemoveModelsSelected then
    begin
      DelTree(AddBackslash(RuntimeRoot) + 'models', True, True, True);
      DelTree(AddBackslash(RuntimeRoot) + '.downloads', True, True, True);
      DelTree(AddBackslash(RuntimeRoot) + 'gpu-runtime', True, True, True);
    end;
    if RemoveStateSelected then
    begin
      DeleteFile(AddBackslash(RuntimeRoot) + 'config.json');
      DeleteFile(AddBackslash(RuntimeRoot) + 'settings-history.jsonl');
      DeleteFile(AddBackslash(RuntimeRoot) + 'state.db');
      DeleteFile(AddBackslash(RuntimeRoot) + 'state.db-wal');
      DeleteFile(AddBackslash(RuntimeRoot) + 'state.db-shm');
      DelTree(AddBackslash(RuntimeRoot) + 'spool', True, True, True);
      DelTree(AddBackslash(RuntimeRoot) + 'logs', True, True, True);
    end;
  end;
end;
