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

[Files]
Source: "..\dist\dpdfnet-mic-bridge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\DPDFNet Mic Bridge"; Filename: "{app}\dpdfnet-mic-bridge.exe"

[Run]
Filename: "{app}\dpdfnet-mic-bridge.exe"; Description: "Open DPDFNet Mic Bridge"; Flags: nowait postinstall skipifsilent
