; Установщик Hidden Path (Inno Setup 6).
; Сборка: build_installer.bat  ->  installer\HiddenPath-Setup.exe
;
; Ставит HiddenPath.exe в Program Files, создаёт ярлыки и (по галочке)
; автозапуск при входе в Windows — та же задача Планировщика, что и галочка
; в самом приложении. Релизы zapret и Xray приложение скачает само при первом запуске.
; Удаление останавливает обход, убирает автозапуск и скачанные файлы.

#define AppName "Hidden Path"
#define AppExe "HiddenPath.exe"
#define AppVersion "1.1.0"
#define TaskName "HiddenPathAutostart"
#define XrayExe "hp-xray.exe"

[Setup]
AppId={{9E4B2D71-3C8A-4F15-B6E2-5A0D7C3F1B84}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=NapsterFO
AppPublisherURL=https://github.com/NapsterFO/HiddenPath
AppSupportURL=https://github.com/NapsterFO/HiddenPath/issues
DefaultDirName={autopf}\HiddenPath
UsePreviousAppDir=no
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer
OutputBaseFilename=HiddenPath-Setup
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

[Files]
Source: "dist\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; задачу автозапуска создаёт сама программа (правильные настройки: работает
; от батареи и без 72-часового лимита) и запоминает выбор в config.json
Filename: "{app}\{#AppExe}"; Parameters: "--set-autostart on"; Flags: runhidden waituntilterminated runascurrentuser; Tasks: autostart; Check: not WizardSilent
Filename: "{app}\{#AppExe}"; Parameters: "--set-autostart off"; Flags: runhidden waituntilterminated runascurrentuser; Tasks: not autostart; Check: not WizardSilent
; runascurrentuser: программе нужны права администратора, установщик их уже имеет
; (без этого флага Windows отказывает: CreateProcess, код 740)
Filename: "{app}\{#AppExe}"; Description: "Запустить {#AppName}"; Flags: postinstall nowait skipifsilent runascurrentuser
; обновление из самой программы (тихая установка): автозапуск не трогаем и сразу запускаем новую версию
Filename: "{app}\{#AppExe}"; Flags: nowait runascurrentuser; Check: WizardSilent

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/F /IM {#AppExe}"; Flags: runhidden; RunOnceId: "KillApp"
Filename: "{app}\{#AppExe}"; Parameters: "--reset-proxy"; Flags: runhidden waituntilterminated; RunOnceId: "ResetProxy"
Filename: "taskkill.exe"; Parameters: "/F /IM {#XrayExe}"; Flags: runhidden; RunOnceId: "KillXray"
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

// Перед установкой (и обновлением): вернуть системный прокси, закрыть программу и обход.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  AppPath: String;
begin
  AppPath := ExpandConstant('{app}');
  if FileExists(AppPath + '\{#AppExe}') then
    Run(AppPath + '\{#AppExe}', '--reset-proxy');
  Run('taskkill.exe', '/F /IM {#AppExe}');
  Run('taskkill.exe', '/F /IM {#XrayExe}');
  Run('taskkill.exe', '/F /IM winws.exe');
  Run('taskkill.exe', '/F /T /IM TgWsProxy*');
  Sleep(800);
  Result := '';
end;
