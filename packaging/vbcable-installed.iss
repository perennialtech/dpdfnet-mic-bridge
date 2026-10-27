function VBCableInstalled: Boolean;
begin
  { Both official VB-CABLE driver versions expose this service. Pascal strings
    use literal backslashes, not C-style backslash escapes. }
  Result := RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\VBAudioVACMME');
end;
