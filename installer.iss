; Установщик NapsterVPN (Inno Setup 6).
; Сборка: build_installer.bat  ->  installer\NapsterVPN-Setup.exe
;
; Ставит NapsterVPN.exe в Program Files, создаёт ярлыки и (по галочке)
; автозапуск при входе в Windows — та же задача Планировщика, что и галочка
; в самом приложении. Релиз zapret приложение скачает само при первом запуске.
; Старая версия (Zapret Manager) переезжает в новую папку вместе с настройками.
; Удаление останавливает обход, убирает автозапуск и скачанные файлы.

#define AppName "NapsterVPN"
#define AppExe "NapsterVPN.exe"
#define AppVersion "2.4"
#define TaskName "NapsterVPNAutostart"
#define OldDir "ZapretManager"
#define OldExe "ZapretManager.exe"
#define OldTask "ZapretManagerAutostart"

[Setup]
; AppId прежний: новая версия заменяет старую в «Приложениях», а не ставится рядом
AppId={{6C1F3B7E-4A52-4C2B-9E3D-7A0F5B8D2C41}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
DefaultDirName={autopf}\{#AppName}
UsePreviousAppDir=no
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer
OutputBaseFilename=NapsterVPN-Setup
SetupIconFile=icon.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "autostart"; Description: "Запускать вместе с Windows"; GroupDescription: "Дополнительно:"
Name: "desktopicon"; Description: "Ярлык на рабочем столе"; GroupDescription: "Дополнительно:"

[InstallDelete]
; ярлыки старой версии
Type: files; Name: "{autodesktop}\Zapret Manager.lnk"
Type: filesandordirs; Name: "{autoprograms}\Zapret Manager"

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; задачу автозапуска создаёт сама программа (правильные настройки: работает
; от батареи и без 72-часового лимита) и запоминает выбор в config.json
Filename: "{app}\{#AppExe}"; Parameters: "--set-autostart on"; Flags: runhidden waituntilterminated runascurrentuser; Tasks: autostart
Filename: "{app}\{#AppExe}"; Parameters: "--set-autostart off"; Flags: runhidden waituntilterminated runascurrentuser; Tasks: not autostart
; runascurrentuser: программе нужны права администратора, установщик их уже имеет
; (без этого флага Windows отказывает: CreateProcess, код 740)
Filename: "{app}\{#AppExe}"; Description: "Запустить {#AppName}"; Flags: postinstall nowait skipifsilent runascurrentuser

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "KillApp"
Filename: "{app}\{#AppExe}"; Parameters: "--reset-proxy"; Flags: runhidden waituntilterminated; RunOnceId: "ResetProxy"
Filename: "taskkill.exe"; Parameters: "/F /IM zm-xray.exe"; Flags: runhidden; RunOnceId: "KillXray"
Filename: "taskkill.exe"; Parameters: "/F /IM winws.exe"; Flags: runhidden; RunOnceId: "KillWinws"
Filename: "taskkill.exe"; Parameters: "/F /T /IM TgWsProxy*"; Flags: runhidden; RunOnceId: "KillTg"
Filename: "net.exe"; Parameters: "stop WinDivert"; Flags: runhidden; RunOnceId: "StopWinDivert"
Filename: "schtasks.exe"; Parameters: "/Delete /TN {#TaskName} /F"; Flags: runhidden; RunOnceId: "DelTask"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\zapret"
Type: filesandordirs; Name: "{app}\tgws"
Type: filesandordirs; Name: "{app}\xray"
Type: filesandordirs; Name: "{app}\_tmp_update"
Type: files; Name: "{app}\config.json"
Type: dirifempty; Name: "{app}"

[Code]
procedure Run(const Exe, Params: String);
var
  Code: Integer;
begin
  Exec(Exe, Params, '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

// Перед установкой: вернуть системный прокси (VLESS), закрыть программу и обход,
// перенести старую папку ZapretManager со всеми настройками в новую.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  OldPath, NewPath: String;
begin
  OldPath := ExpandConstant('{autopf}\{#OldDir}');
  NewPath := ExpandConstant('{app}');
  if FileExists(OldPath + '\{#OldExe}') then
    Run(OldPath + '\{#OldExe}', '--reset-proxy');
  if FileExists(NewPath + '\{#AppExe}') then
    Run(NewPath + '\{#AppExe}', '--reset-proxy');
  Run('taskkill.exe', '/F /IM {#OldExe}');
  Run('taskkill.exe', '/F /IM {#AppExe}');
  Run('taskkill.exe', '/F /IM zm-xray.exe');
  Run('taskkill.exe', '/F /IM winws.exe');
  Run('taskkill.exe', '/F /T /IM TgWsProxy*');
  Run('schtasks.exe', '/Delete /TN {#OldTask} /F');
  Sleep(800);
  if DirExists(OldPath) and not DirExists(NewPath) and (CompareText(OldPath, NewPath) <> 0) then
  begin
    if RenameFile(OldPath, NewPath) then
    begin
      DeleteFile(NewPath + '\{#OldExe}');
      DeleteFile(NewPath + '\unins000.exe');
      DeleteFile(NewPath + '\unins000.dat');
      Log('Старая папка перенесена: ' + OldPath + ' -> ' + NewPath);
    end else
      Log('Не удалось перенести старую папку, ставлю начисто');
  end;
  Result := '';
end;
