; Установщик Транскрибатора.
;
; Ставится только для текущего пользователя (PrivilegesRequired=lowest):
; права администратора не нужны, и программа спокойно пишет рядом с собой
; историю, настройки и токен.
;
; Сам .exe компактный — Python, библиотеки и модель докачивает bootstrap.ps1.
; Он запускается скрыто, а о ходе работы сообщает через progress.txt, который
; читает процедура RunBootstrap ниже и двигает полосу загрузки.

#define AppName "Транскрибатор"
#define AppVersion "1.0"
#define AppPublisher "Transcribator"
#define AppExeName "Транскрибатор.bat"

[Setup]
AppId={{8F3C1A62-4D7E-4B15-9C2A-16B0E7A45D31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\Transcribator
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=no
AllowNoIcons=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=TranscribatorSetup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\icon.ico
UninstallDisplayName={#AppName}
; Сам .exe крошечный, но после него докачиваются Python, PyTorch и модель.
; Без этой поправки установщик обещал бы «нужно 4 МБ» и пускал бы ставить
; программу на забитый диск.
ExtraDiskSpaceRequired=5100000000
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableWelcomePage=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Messages]
russian.WelcomeLabel2=Будет установлен [name/ver] — программа для расшифровки аудио и видео в документы Word.%n%nПосле копирования файлов установщик докачает Python, библиотеки и модель распознавания речи: около 6 ГБ, 20–40 минут в зависимости от скорости интернета. Нужен работающий интернет.
russian.SelectDirLabel3=Программа будет установлена в указанную папку. Выбирайте путь покороче и без кириллицы: внутри PyTorch есть очень длинные имена файлов, и в глубоко вложенной папке установка не пройдёт.

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"
Name: "skipmodels"; Description: "Не скачивать модель сейчас (загрузится при первой расшифровке)"; GroupDescription: "Дополнительно:"; Flags: unchecked

[Files]
Source: "..\transcribe.py";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\gui.py";               DestDir: "{app}"; Flags: ignoreversion
Source: "..\first_run.py";         DestDir: "{app}"; Flags: ignoreversion
Source: "..\models.py";            DestDir: "{app}"; Flags: ignoreversion
Source: "..\tkutil.py";            DestDir: "{app}"; Flags: ignoreversion
Source: "..\glossary.py";          DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt";     DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";            DestDir: "{app}"; Flags: ignoreversion
Source: "..\icon.ico";             DestDir: "{app}"; Flags: ignoreversion
Source: "..\Транскрибатор.bat";    DestDir: "{app}"; Flags: ignoreversion
Source: "..\Расшифровать.bat";     DestDir: "{app}"; Flags: ignoreversion
Source: "bootstrap.ps1";           DestDir: "{app}\installer"; Flags: ignoreversion
Source: "prefetch.py";             DestDir: "{app}\installer"; Flags: ignoreversion

[Dirs]
Name: "{app}\input"
Name: "{app}\output"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\icon.ico"; WorkingDir: "{app}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\icon.ico"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Запустить {#AppName}"; \
  Flags: postinstall nowait skipifsilent shellexec

; Окружение и служебные файлы созданы установщиком — их и убираем.
; История, токен и расшифровки остаются: это данные человека.
[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\installer"
Type: files; Name: "{app}\machine.json"
Type: files; Name: "{app}\install.log"
Type: files; Name: "{app}\install.done"
Type: files; Name: "{app}\progress.txt"
Type: files; Name: "{app}\gui_settings.json"
Type: files; Name: "{app}\gui_error.log"

[Code]
var
  BootstrapPage: TOutputProgressWizardPage;

// Слишком длинный путь ломает установку PyTorch (предел 260 символов),
// поэтому предупреждаем на той же странице, где путь выбирают.
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    if Length(WizardDirValue) > 90 then
    begin
      MsgBox('Слишком длинный путь установки (' + IntToStr(Length(WizardDirValue)) +
             ' символов).' + Chr(13) + Chr(10) + Chr(13) + Chr(10) +
             'Внутри PyTorch есть файлы с очень длинными именами, и в такой папке ' +
             'установка не пройдёт. Выберите путь покороче, например C:\Transcribator',
             mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function SkipModelsFlag(): string;
begin
  if WizardIsTaskSelected('skipmodels') then
    Result := ' -SkipModels'
  else
    Result := '';
end;

// Разбирает строку progress.txt вида «18|62|Ставлю PyTorch».
function ReadProgress(var Percent: Integer; var Ceiling: Integer;
                      var Text: string): Boolean;
var
  Lines: TArrayOfString;
  Line, Head: string;
  Bar: Integer;
begin
  Result := False;
  if not FileExists(ExpandConstant('{app}\progress.txt')) then Exit;
  if not LoadStringsFromFile(ExpandConstant('{app}\progress.txt'), Lines) then Exit;
  if GetArrayLength(Lines) = 0 then Exit;

  Line := Trim(Lines[0]);
  Bar := Pos('|', Line);
  if Bar = 0 then Exit;
  Head := Copy(Line, 1, Bar - 1);
  Line := Copy(Line, Bar + 1, Length(Line));
  Percent := StrToIntDef(Head, -999);

  Bar := Pos('|', Line);
  if Bar = 0 then Exit;
  Ceiling := StrToIntDef(Copy(Line, 1, Bar - 1), Percent);
  Text := Copy(Line, Bar + 1, Length(Line));
  Result := Percent <> -999;
end;

procedure RunBootstrap();
var
  Command, Text, LastText: string;
  ResultCode, Percent, Ceiling, Shown, Ticks: Integer;
  DoneFile: string;
begin
  DoneFile := ExpandConstant('{app}\install.done');
  DeleteFile(DoneFile);
  DeleteFile(ExpandConstant('{app}\progress.txt'));

  BootstrapPage := CreateOutputProgressPage(
    'Настройка Транскрибатора',
    'Скачиваются Python, библиотеки и модель распознавания речи. ' +
    'Это делается один раз и занимает 20-40 минут.');
  BootstrapPage.SetProgress(0, 100);
  BootstrapPage.SetText('Запускаю…', '');
  BootstrapPage.Show;

  try
    Command := '-ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File "' +
               ExpandConstant('{app}\installer\bootstrap.ps1') + '" -AppDir "' +
               ExpandConstant('{app}') + '" -Unattended' + SkipModelsFlag();
    // ewNoWait: ждём сами, чтобы полоса двигалась, а не висела до конца.
    if not Exec('powershell.exe', Command, '', SW_HIDE, ewNoWait, ResultCode) then
    begin
      MsgBox('Не удалось запустить настройку (powershell.exe).', mbError, MB_OK);
      Exit;
    end;

    Shown := 0;
    Ticks := 0;
    Percent := 0;
    Ceiling := 0;
    while not FileExists(DoneFile) do
    begin
      if ReadProgress(Percent, Ceiling, Text) then
      begin
        if Percent = -1 then
        begin
          BootstrapPage.SetText('Ошибка: ' + Text, '');
          Break;
        end;
        if Percent > Shown then Shown := Percent;
        if Text <> LastText then
        begin
          BootstrapPage.SetText(Text, '');
          LastText := Text;
        end;
      end;

      // Между отметками полоса ползёт сама, иначе на долгом шаге
      // (загрузка PyTorch) она выглядела бы зависшей.
      Ticks := Ticks + 1;
      if (Ticks mod 12 = 0) and (Shown < Ceiling - 1) then Shown := Shown + 1;

      BootstrapPage.SetProgress(Shown, 100);
      Sleep(500);
    end;

    if ReadProgress(Percent, Ceiling, Text) and (Percent = -1) then
      MsgBox('Настройка не завершилась:' + Chr(13) + Chr(10) + Chr(13) + Chr(10) +
             Text + Chr(13) + Chr(10) + Chr(13) + Chr(10) +
             'Подробности в файле install.log в папке программы. ' +
             'Программа попробует доустановиться сама при первом запуске.',
             mbError, MB_OK)
    else
      BootstrapPage.SetProgress(100, 100);
  finally
    BootstrapPage.Hide;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RunBootstrap();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Keep, NewLine, Message: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Keep := ExpandConstant('{app}');
    if FileExists(Keep + '\history.jsonl') or FileExists(Keep + '\hf_token.txt') then
    begin
      // Строка не должна начинаться с #: препроцессор Inno примет её за директиву.
      NewLine := Chr(13) + Chr(10);
      Message := 'История расшифровок и токен остались в папке:' + NewLine + Keep;
      Message := Message + NewLine + NewLine +
        'Удалите её вручную, если они больше не нужны.';
      MsgBox(Message, mbInformation, MB_OK);
    end;
  end;
end;
