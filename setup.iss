; Inno Setup script — SOCAR CAPE Commercial-automation
; Build steps (Windows only):
;   1. pyinstaller app.spec          → produces dist\Commercial-automation.exe
;   2. iscc setup.iss                → produces Output\Commercial-automation_Setup_<version>.exe
;
; To release a new version, edit VERSION only — it's the single source of
; truth for both the in-app version and this installer's version.
;
; Requires: Inno Setup 6  https://jrsoftware.org/isinfo.php

#define AppName      "SOCAR CAPE Commercial-automation"
; AppVersion is read from VERSION — the same file the running app reads for
; __version__ — so the installer and in-app version can't drift.
#define AppVersion   Trim(FileRead(FileOpen("VERSION")))
#define AppPublisher "Magsud Abbaszade"
#define AppExeName   "Commercial-automation.exe"
#define AppURL       ""

[Setup]
AppId={{8F3A2C1D-47B6-4E9A-BC52-D0F1E3A29C87}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\SOCAR Cape automation\Commercial-automation
DefaultGroupName=SOCAR Cape
AllowNoIcons=yes
; Upgrade over an existing install (same AppId): reuse the folder and Start
; Menu group chosen last time instead of asking again. A first install still
; shows both pages.
DisableDirPage=auto
DisableProgramGroupPage=auto
; Inno would otherwise force-close a running copy of the app, losing unsaved
; work. InitializeSetup below asks the user to save and close it instead.
CloseApplications=no
RestartApplications=no
; Installer output
OutputDir=Output
OutputBaseFilename=Commercial-automation_Setup_{#AppVersion}
SetupIconFile=app_icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Require admin to write to Program Files
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
; Minimum Windows 10
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; Main executable (built by PyInstaller)
Source: "dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}";        Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
// ── LibreOffice prerequisite check ─────────────────────────────────────────
// Checks HKLM\SOFTWARE\LibreOffice and the typical install path.
// If missing, warns the user — the installer still proceeds so they can
// install LibreOffice afterwards and the app will work for Excel output
// even without it (PDF export is skipped with a clear message).

function LibreOfficeInstalled: Boolean;
var
  InstPath: String;
begin
  // Check registry key written by the LibreOffice installer
  Result := RegKeyExists(HKLM, 'SOFTWARE\LibreOffice') or
            RegKeyExists(HKLM, 'SOFTWARE\WOW6432Node\LibreOffice');
  // Fallback: look for soffice.exe in the default install location
  if not Result then
    Result := FileExists(ExpandConstant('{pf}\LibreOffice\program\soffice.exe')) or
              FileExists(ExpandConstant('{pf32}\LibreOffice\program\soffice.exe'));
end;

// ── Running-app check ──────────────────────────────────────────────────────
// True while Commercial-automation.exe is running (any user session).
function AppIsRunning: Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'),
    '/C tasklist /FI "IMAGENAME eq {#AppExeName}" /NH | find /I "{#AppExeName}" >nul',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function IsUpgrade: Boolean;
var
  Key: String;
begin
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8F3A2C1D-47B6-4E9A-BC52-D0F1E3A29C87}_is1';
  Result := RegKeyExists(HKLM, Key) or RegKeyExists(HKCU, Key);
end;

procedure InitializeWizard;
begin
  if IsUpgrade then
    WizardForm.WelcomeLabel2.Caption := WizardForm.WelcomeLabel2.Caption + #13#10 + #13#10 +
      'This will update the version already installed. Your settings, aliases, ' +
      'renames and presets are kept. Please save your work and close the app first.';
end;

function InitializeSetup: Boolean;
begin
  Result := True;   // always allow install to continue
  // Replacing the exe while it runs would fail or lose unsaved work, so ask
  // the user to save and close it. Retry re-checks; Cancel stops Setup.
  // (Silent installs skip the prompt.)
  while Result and AppIsRunning and not WizardSilent do
    if MsgBox('{#AppName} is running.' + #13#10 + #13#10 +
              'Save your work and close the app, then press Retry.',
              mbConfirmation, MB_RETRYCANCEL) = IDCANCEL then
      Result := False;
  if not Result then Exit;
  if not LibreOfficeInstalled then
    MsgBox(
      'LibreOffice does not appear to be installed on this machine.' + #13#10 + #13#10 +
      'The app works fully without it — only automatic PDF export after CTR' + #13#10 +
      'generation requires LibreOffice. You can install it later from:' + #13#10 +
      'https://www.libreoffice.org/download/download/' + #13#10 + #13#10 +
      'Setup will now continue.',
      mbInformation, MB_OK
    );
end;
