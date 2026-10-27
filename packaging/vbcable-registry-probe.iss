; This minimal installer exercises the exact Pascal code used by the release
; installer. It aborts before installation and is never shipped.
[Setup]
AppName=VB-CABLE registry probe
AppVersion=1
DefaultDirName={localappdata}\VB-CABLE-registry-probe
PrivilegesRequired=lowest
OutputBaseFilename=vbcable-registry-probe

[Code]
#include "vbcable-installed.iss"

function InitializeSetup: Boolean;
var
  Status: String;
begin
  if VBCableInstalled then
    Status := 'present'
  else
    Status := 'absent';
  if not SaveStringToFile(GetEnv('VBCABLE_PROBE_RESULT'), Status, False) then
    RaiseException('Could not save VB-CABLE registry probe result');
  Result := False;
end;
