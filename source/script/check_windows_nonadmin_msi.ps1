param(
  [Parameter(Mandatory=$true)][string]$Msi,
  [Parameter(Mandatory=$true)][string]$FrozenReceipt
)

$ErrorActionPreference = 'Stop'
$name = 'ampfiqa' + $env:GITHUB_RUN_ID.Substring([Math]::Max(0, $env:GITHUB_RUN_ID.Length - 5))
$password = ConvertTo-SecureString (([Guid]::NewGuid().ToString('N')) + 'aA1!') -AsPlainText -Force
$user = $null
$profile = $null
$installed = $null
$installExit = $null
$uninstallExit = $null
$tokenVerified = $false
$profileVerified = $false
$profileRecordVerified = $false
$knownFoldersVerified = $false
$profileMismatchReason = $null
$inheritedEnvironmentMatchesTokenFolders = $false
$inventoryVerified = $false
$packageRemoved = $false
$firstFailure = $null
$firstFailureHResult = $null
$firstFailureNativeCode = $null
$stage = 'load-profiled-process-helper'
$accountRemoved = $false
$stagedMsi = $null
$tokenFile = Join-Path $env:RUNNER_TEMP 'amplifai-nonadmin-token-private.json'
$installLog = $null
$uninstallLog = $null
function Get-BoundedMsiCodes([string]$path) {
  if (-not $path -or -not (Test-Path -LiteralPath $path)) { return @() }
  $codes = [System.Collections.Generic.HashSet[string]]::new()
  foreach ($line in (Get-Content $path -Tail 400)) {
    if ($line -match '(?i)(?:error|MainEngineThread is returning)\s+([0-9]{4})') { [void]$codes.Add($Matches[1]) }
  }
  return @($codes | Sort-Object | Select-Object -First 8)
}
function Get-BoundedMsiFailureActions([string]$path) {
  if (-not $path -or -not (Test-Path -LiteralPath $path)) { return @() }
  $actions = [System.Collections.Generic.HashSet[string]]::new()
  foreach ($line in (Get-Content $path -Tail 400)) {
    if ($line -match '(?i)Action ended .*?:\s*([A-Za-z][A-Za-z0-9_.-]{0,79})\. Return value 3') { [void]$actions.Add($Matches[1]) }
  }
  return @($actions | Sort-Object | Select-Object -First 8)
}
$childErrorReason = $null
$cleanupFailureReason = $null
try {
  . (Resolve-Path 'source/script/windows_profile_process.ps1').Path
  $stage = 'create-disposable-user'
  $user = New-LocalUser -Name $name -Password $password -Description 'Disposable AMPLIFai MSI acceptance fixture'
  $administrators = Get-LocalGroup -SID 'S-1-5-32-544'
  $isAdmin = Get-LocalGroupMember -Group $administrators.Name | Where-Object { $_.SID.Value -eq $user.SID.Value }
  if ($isAdmin) { throw 'Disposable account unexpectedly has administrative membership' }
  $stage = 'probe-standard-user-token'
  New-Item -ItemType File -Path $tokenFile -ErrorAction Stop | Out-Null
  $tokenAcl = Get-Acl -LiteralPath $tokenFile
  $tokenRule = [Security.AccessControl.FileSystemAccessRule]::new($user.SID, [Security.AccessControl.FileSystemRights]::Modify, [Security.AccessControl.AccessControlType]::Allow)
  $tokenAcl.AddAccessRule($tokenRule)
  Set-Acl -LiteralPath $tokenFile -AclObject $tokenAcl
  $probe = (Resolve-Path 'source/script/windows_nonadmin_child_probe.ps1').Path
  $probeArguments = "-NoLogo -NoProfile -NonInteractive -File `"$probe`" -OutputPath `"$tokenFile`""
  $probeExit = Invoke-ProfiledProcess -Username $name -Password $password -FilePath (Get-Command pwsh.exe).Source -Arguments $probeArguments -WorkingDirectory (Get-Location).Path
  if ($probeExit -ne 0) {
    if (Test-Path -LiteralPath $tokenFile) {
      $childError = Get-Content -Raw -LiteralPath $tokenFile | ConvertFrom-Json
      $childErrorReason = $childError.errorReason
    }
    if (-not $childErrorReason) { $childErrorReason = 'no-child-error-receipt' }
    throw "Standard-user token probe failed: $probeExit"
  }
  $token = Get-Content -Raw $tokenFile | ConvertFrom-Json
  if ($token.sid -ne $user.SID.Value -or $token.enabledAdministratorRole -ne $false) { throw 'Child process token is not the disposable standard user' }
  $tokenVerified = $true
  $profileRecord = Get-CimInstance Win32_UserProfile -Filter "SID='$($user.SID.Value)'"
  if (-not $profileRecord -or -not $profileRecord.LocalPath) { throw 'Disposable user has no Windows profile record' }
  $profile = $profileRecord.LocalPath
  $profileRecordVerified = Test-Path $profile
  if (-not $profileRecordVerified) { throw 'Disposable user profile directory is absent' }
  $profilePathMatches = $token.tokenKnownProfile -eq $profile
  $localAppDataMatches = $token.tokenKnownLocalAppData -eq (Join-Path $profile 'AppData\Local')
  $knownFoldersVerified = $token.tokenHiveLoaded -eq $true -and $profilePathMatches -and $localAppDataMatches
  if (-not $token.tokenHiveLoaded) { $profileMismatchReason = 'disposable-hkcu-hive-unavailable' }
  elseif (-not $profilePathMatches) { $profileMismatchReason = 'child-profile-folder-differs-from-os-record' }
  elseif (-not $localAppDataMatches) { $profileMismatchReason = 'child-local-app-data-differs-from-os-record' }
  if (-not $knownFoldersVerified) { throw 'Token-bound known folders or HKCU hive do not match the OS profile record' }
  $inheritedEnvironmentMatchesTokenFolders = $token.inheritedEnvironmentProfile -eq $token.tokenKnownProfile -and $token.inheritedEnvironmentLocalAppData -eq $token.tokenKnownLocalAppData
  $profileVerified = $true
  $installed = Join-Path $token.tokenKnownLocalAppData 'AMPLIFaiPhone'
  if (Test-Path $installed) { throw 'Disposable install path is not clean' }

  $stage = 'stage-authenticated-msi-for-disposable-user'
  $stagedMsi = Join-Path $token.tokenKnownLocalAppData 'AmplifaiPhone-rc14-unsigned.msi'
  if (Test-Path $stagedMsi) { throw 'Disposable MSI staging path is not clean' }
  $expectedMsiHash = '887d367e602fccc8d8245964443f1d62eb3885a7311039d471a3c07d819cfafb'
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Msi).Hash.ToLowerInvariant() -ne $expectedMsiHash) { throw 'Retained MSI identity mismatch' }
  Copy-Item -LiteralPath $Msi -Destination $stagedMsi -ErrorAction Stop
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $stagedMsi).Hash.ToLowerInvariant() -ne $expectedMsiHash) { throw 'Disposable MSI staging copy differs' }
  $installLog = Join-Path $token.tokenKnownLocalAppData 'amplifai-nonadmin-install.log'
  $uninstallLog = Join-Path $token.tokenKnownLocalAppData 'amplifai-nonadmin-uninstall.log'

  $stage = 'install-private-msi'
  $installExit = Invoke-ProfiledProcess -Username $name -Password $password -FilePath (Get-Command msiexec.exe).Source -Arguments "/i `"$stagedMsi`" /qn /norestart /L*V `"$installLog`"" -WorkingDirectory (Get-Location).Path
  if ($installExit -ne 0) { throw "Standard-user MSI install failed: $installExit" }
  $stage = 'verify-installed-frozen-inventory'
  python source/script/verify_windows_install.py --install $installed --receipt $FrozenReceipt
  if ($LASTEXITCODE -ne 0) { throw 'Standard-user installed inventory failed' }
  $inventoryVerified = $true

  $stage = 'uninstall-private-msi'
  $uninstallExit = Invoke-ProfiledProcess -Username $name -Password $password -FilePath (Get-Command msiexec.exe).Source -Arguments "/x `"$stagedMsi`" /qn /norestart /L*V `"$uninstallLog`"" -WorkingDirectory (Get-Location).Path
  if ($uninstallExit -ne 0) { throw "Standard-user MSI uninstall failed: $uninstallExit" }
  $stage = 'verify-no-installed-package-residue'
  python source/script/verify_windows_install.py --install $installed --receipt $FrozenReceipt --removed
  if ($LASTEXITCODE -ne 0) { throw 'Standard-user uninstall left package files' }
  $packageRemoved = $true
} catch {
  $firstFailure = $stage
  $firstFailureHResult = $_.Exception.HResult
  if ($_.Exception -is [System.ComponentModel.Win32Exception]) { $firstFailureNativeCode = $_.Exception.NativeErrorCode }
  elseif ($_.Exception.InnerException -is [System.ComponentModel.Win32Exception]) { $firstFailureNativeCode = $_.Exception.InnerException.NativeErrorCode }
  Write-Error "Focused standard-user check failed at $stage (HRESULT $firstFailureHResult; native $firstFailureNativeCode)" -ErrorAction Continue
} finally {
  if ($user -and $stagedMsi -and $installed -and (Test-Path $installed) -and -not $packageRemoved -and (Get-Command Invoke-ProfiledProcess -ErrorAction SilentlyContinue)) {
    try {
      $cleanupExit = Invoke-ProfiledProcess -Username $name -Password $password -FilePath (Get-Command msiexec.exe).Source -Arguments "/x `"$stagedMsi`" /qn /norestart /L*V `"$uninstallLog`"" -WorkingDirectory (Get-Location).Path
      if ($cleanupExit -eq 0) { $packageRemoved = -not (Test-Path $installed) }
    } catch { $cleanupFailureReason = 'profiled-uninstall-retry-failed' }
  }
  $installCodes = @(Get-BoundedMsiCodes $installLog)
  $uninstallCodes = @(Get-BoundedMsiCodes $uninstallLog)
  $installFailureActions = @(Get-BoundedMsiFailureActions $installLog)
  $uninstallFailureActions = @(Get-BoundedMsiFailureActions $uninstallLog)
  if ($stagedMsi) { Remove-Item -LiteralPath $stagedMsi -Force -ErrorAction SilentlyContinue }
  if ($stagedMsi -and (Test-Path -LiteralPath $stagedMsi) -and -not $firstFailure) { $firstFailure = 'remove-disposable-msi-copy' }
  foreach ($log in @($installLog, $uninstallLog)) {
    if ($log) { Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue }
  }
  if ($user) {
    Remove-LocalUser -Name $name -ErrorAction SilentlyContinue
    $accountRemoved = -not [bool](Get-LocalUser -Name $name -ErrorAction SilentlyContinue)
    if (-not $accountRemoved -and -not $firstFailure) { $firstFailure = 'remove-disposable-user' }
  }
  $receipt = [ordered]@{
    status = if ($firstFailure) { 'focused-standard-user-check-failed' } else { 'disposable-standard-user-private-unsigned-msi-pass' }
    childTokenMatchedDisposableSid = $tokenVerified
    childAdministratorRoleEnabled = if ($tokenVerified) { $false } else { $null }
    windowsProfileRecordVerified = $profileRecordVerified
    tokenKnownFoldersAndHiveVerified = $knownFoldersVerified
    tokenProfileMismatchReason = $profileMismatchReason
    inheritedEnvironmentMatchesTokenFolders = $inheritedEnvironmentMatchesTokenFolders
    childProfileAndLocalAppDataVerified = $profileVerified
    installedInventoryMatchedFrozenReceipt = $inventoryVerified
    uninstallPackageResidueAbsent = $packageRemoved
    disposableAccountRemoved = $accountRemoved
    installExitCode = $installExit
    uninstallExitCode = $uninstallExit
    installLogErrorCodes = $installCodes
    uninstallLogErrorCodes = $uninstallCodes
    installLogFailureActions = $installFailureActions
    uninstallLogFailureActions = $uninstallFailureActions
    stagedMsiCopyRemoved = if ($stagedMsi) { -not (Test-Path -LiteralPath $stagedMsi) } else { $null }
    installedPathClass = 'disposable-local-user-LocalAppData'
    ownerPhoneOrAccountUsed = $false
    firstFailureStage = $firstFailure
    firstFailureHResult = $firstFailureHResult
    firstFailureNativeCode = $firstFailureNativeCode
    childErrorReason = $childErrorReason
    cleanupFailureReason = $cleanupFailureReason
    processLaunchMode = 'CreateProcessWithLogonW-profiled-null-environment'
  }
  $receipt | ConvertTo-Json -Depth 3 | Set-Content -Encoding utf8 (Join-Path $env:RUNNER_TEMP 'nonadmin-receipt.json')
  Remove-Item $tokenFile -Force -ErrorAction SilentlyContinue
}
if ($firstFailure) { exit 1 }
$receipt.status
