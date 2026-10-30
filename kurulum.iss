; Kasa Takip - Inno Setup kurulum betigi (Inno Setup 6.3+ veya 7)
; exe_olustur.bat bu dosyayi otomatik derler. Elle: ISCC.exe kurulum.iss

#define AppName "Kasa Takip"
#define AppVersion "1.2.0"
#define AppExe "KasaTakip.exe"

[Setup]
AppId={{8C1B7E5A-3F4D-4C1E-9B7A-6D2E5F9A1C33}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Tahir Ekinalan
AppPublisherURL=https://tahirekinalan.com
AppCopyright=Copyright (C) 2026 Tahir Ekinalan - tahirekinalan.com - MIT Lisansi.
DefaultDirName={autopf}\KasaTakip
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Kurulum
OutputBaseFilename=KasaTakip_Kurulum_{#AppVersion}
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\KasaTakip\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "LISANS.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{#AppName} (Deneme Modu)"; Filename: "{app}\{#AppExe}"; Parameters: "--demo"
Name: "{group}\{#AppName} Kaldir"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; Not: Veriler (veritabani, yedekler, raporlar) program klasorunde degildir; kaldirma islemi verileri SILMEZ.
;   Veritabani : %LOCALAPPDATA%\KasaTakip\veri\kasa.kdb (sifreli)
;   Yedekler   : Belgeler\KasaTakip\Yedekler
;   Raporlar   : Belgeler\KasaTakip\Raporlar

[Code]
{ Internet engeli: Windows Guvenlik Duvari'nda programin disari ve iceri baglantisini engelleyen kural.
  Kurulumda eklenir (once eski kural silinir, tekrar kurulumda cift kural olusmaz), kaldirmada silinir.
  Kurulum yonetici olarak calismiyorsa kural icin yonetici onayi (UAC) istenir; onay verilmezse kurulum
  yine tamamlanir, program kendi icindeki ag engeliyle (kasatakip/netguard.py) calisir. }
const
  FwRule = 'Kasa Takip - internet engeli';

procedure FirewallRule(Add: Boolean);
var
  Params, Exe: String;
  ResultCode: Integer;
begin
  Exe := ExpandConstant('{app}\{#AppExe}');
  Params := '/c netsh advfirewall firewall delete rule name="' + FwRule + '" >nul 2>&1';
  if Add then
    Params := Params +
      ' & netsh advfirewall firewall add rule name="' + FwRule + '" dir=out action=block enable=yes program="' + Exe + '"' +
      ' & netsh advfirewall firewall add rule name="' + FwRule + '" dir=in action=block enable=yes program="' + Exe + '"';
  if IsAdminInstallMode then
    Exec(ExpandConstant('{cmd}'), Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
  else
    ShellExec('runas', ExpandConstant('{cmd}'), Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    FirewallRule(True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    FirewallRule(False);
end;
