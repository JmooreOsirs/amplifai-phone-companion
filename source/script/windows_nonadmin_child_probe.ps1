param([Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference = 'Stop'
try {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  $profile = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
  $localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
  if (-not $profile -or -not $localAppData) { throw 'Windows did not resolve the signed-in token profile folders' }
  $userInstallerPolicy = Get-ItemProperty -Path 'HKCU:\Software\Policies\Microsoft\Windows\Installer' -ErrorAction SilentlyContinue
  [ordered]@{
    sid = $identity.User.Value
    enabledAdministratorRole = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    tokenKnownProfile = $profile
    tokenKnownLocalAppData = $localAppData
    tokenHiveLoaded = [bool](Test-Path -LiteralPath ("Registry::HKEY_USERS\" + $identity.User.Value))
    inheritedEnvironmentProfile = $env:USERPROFILE
    inheritedEnvironmentLocalAppData = $env:LOCALAPPDATA
    userInstallerPolicy = [ordered]@{
      disableMsi = if ($null -ne $userInstallerPolicy.DisableMSI) { [int]$userInstallerPolicy.DisableMSI } else { $null }
      disableUserInstalls = if ($null -ne $userInstallerPolicy.DisableUserInstalls) { [int]$userInstallerPolicy.DisableUserInstalls } else { $null }
      alwaysInstallElevated = if ($null -ne $userInstallerPolicy.AlwaysInstallElevated) { [int]$userInstallerPolicy.AlwaysInstallElevated } else { $null }
    }
  } | ConvertTo-Json -Compress | Set-Content -LiteralPath $OutputPath -Encoding utf8
} catch {
  $reason = if ($_.Exception -is [System.UnauthorizedAccessException]) { 'child-access-denied' }
    elseif ($_.Exception -is [System.ArgumentException]) { 'child-invalid-argument' }
    else { 'child-profile-probe-error' }
  [ordered]@{ errorReason = $reason } | ConvertTo-Json -Compress | Set-Content -LiteralPath $OutputPath -Encoding utf8
  exit 1
}
