#ifndef AppVersion
  #error AppVersion must be supplied with /DAppVersion=...
#endif

[Setup]
AppId=dpdfnet-mic-bridge
AppName=DPDFNet Mic Bridge
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\DPDFNet Mic Bridge
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\release
OutputBaseFilename=dpdfnet-mic-bridge-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\dpdfnet-mic-bridge.exe
LicenseFile=..\LICENSE
CloseApplications=yes
UsePreviousTasks=no

[Files]
Source: "..\dist\dpdfnet-mic-bridge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Preserve the complete original VB-Audio package, including its readme and driver signatures.
Source: "..\build\vbcable\*"; DestDir: "{app}\vendor\vbcable"; Flags: ignoreversion

[Tasks]
Name: "installvbcable"; Description: "Install VB-CABLE virtual audio driver (by VB-Audio; donationware, www.vb-cable.com; administrator approval and restart required)"; GroupDescription: "Optional Windows driver:"; Flags: unchecked; Check: not VBCableInstalled

[Icons]
Name: "{autoprograms}\DPDFNet Mic Bridge"; Filename: "{app}\dpdfnet-mic-bridge.exe"

[Run]
; Do not run in unattended installs, or if the vendor driver is already installed.
; Its GUI installs the driver; it is not ours to silently remove or upgrade.
Filename: "{app}\vendor\vbcable\VBCABLE_Setup_x64.exe"; WorkingDir: "{app}\vendor\vbcable"; Verb: "runas"; Flags: shellexec waituntilterminated skipifsilent; Tasks: installvbcable; Check: CanLaunchVBCableSetup
Filename: "{app}\dpdfnet-mic-bridge.exe"; Description: "Open DPDFNet Mic Bridge"; Flags: nowait postinstall skipifsilent

[Code]
#include "vbcable-installed.iss"

function CanLaunchVBCableSetup: Boolean;
begin
  Result := False;
  if VBCableInstalled then
    Exit;
  try
    Result := CompareText(
      GetSHA256OfFile(ExpandConstant('{app}\vendor\vbcable\VBCABLE_Setup_x64.exe')),
      '734c35dfa6d98f48782a451633ceb471166ec70d60482fd89a1123d0ee3c4f41') = 0;
  except
    Result := False;
  end;
  if not Result then
    MsgBox('VB-CABLE setup is missing or modified; please download it from www.vb-cable.com.', mbError, MB_OK);
end;
