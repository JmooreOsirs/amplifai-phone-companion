$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
[ordered]@{
  sid = $identity.User.Value
  enabledAdministratorRole = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  profile = $env:USERPROFILE
  localAppData = $env:LOCALAPPDATA
} | ConvertTo-Json -Compress
