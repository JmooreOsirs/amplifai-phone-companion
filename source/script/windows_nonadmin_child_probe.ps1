$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$profile = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
$localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
if (-not $profile -or -not $localAppData) { throw 'Windows did not resolve the signed-in token profile folders' }
[ordered]@{
  sid = $identity.User.Value
  enabledAdministratorRole = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  tokenKnownProfile = $profile
  tokenKnownLocalAppData = $localAppData
  tokenHiveLoaded = [bool](Test-Path -LiteralPath ("Registry::HKEY_USERS\" + $identity.User.Value))
  inheritedEnvironmentProfile = $env:USERPROFILE
  inheritedEnvironmentLocalAppData = $env:LOCALAPPDATA
} | ConvertTo-Json -Compress
