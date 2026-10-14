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
$controlStagedMsi = $null
$controlMsiHash = $null
$controlMsiBytes = $null
$controlInstalled = $null
$controlInstallLog = $null
$controlUninstallLog = $null
$controlInstallExit = $null
$controlUninstallExit = $null
$controlInventoryVerified = $false
$controlPackageRemoved = $null
$productStagedRemoved = $null
$controlStagedRemoved = $null
$privateLogsRemoved = $null
$tokenFile = Join-Path $env:RUNNER_TEMP 'amplifai-nonadmin-token-private.json'
$installLog = $null
$uninstallLog = $null
$osProductType = $null
$machineInstallerPolicy = $null
$userInstallerPolicy = $null
$msiProperties = $null
$msiPropertyReadReason = $null
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
function Get-BoundedMsiPolicyReasons([string]$path) {
  if (-not $path -or -not (Test-Path -LiteralPath $path)) { return @() }
  $text = Get-Content $path -Tail 400
  $reasons = [System.Collections.Generic.HashSet[string]]::new()
  foreach ($line in $text) {
    if ($line -match '(?i)installation is forbidden by system policy') { [void]$reasons.Add('system-policy-forbidden') }
    if ($line -match '(?i)disablemsi') { [void]$reasons.Add('disablemsi-name-mentioned-value-unverified') }
    if ($line -match '(?i)disableuserinstalls') { [void]$reasons.Add('disableuserinstalls-name-mentioned-value-unverified') }
    if ($line -match '(?i)software restriction polic') { [void]$reasons.Add('software-restriction-policy-mentioned-outcome-unverified') }
    if ($line -match '(?i)applocker') { [void]$reasons.Add('applocker-policy') }
    if ($line -match '(?i)digital signature policy|signature.*(?:reject|blocked)') { [void]$reasons.Add('signature-policy') }
  }
  return @($reasons | Sort-Object | Select-Object -First 8)
}
function Get-BoundedMsiLogPolicyValues([string]$path) {
  $values = [ordered]@{ disableMsi = $null; disableUserInstalls = $null; softwareRestrictionOutcome = $null }
  if (-not $path -or -not (Test-Path -LiteralPath $path)) { return $values }
  foreach ($line in (Get-Content $path -Tail 400)) {
    if ($line -match '(?i)\bDisableMSI\b\s*(?:=|:|(?:policy\s+)?value(?:\s+is)?)\s*([012])\b') { $values.disableMsi = [int]$Matches[1] }
    if ($line -match '(?i)\bDisableUserInstalls\b\s*(?:=|:|(?:policy\s+)?value(?:\s+is)?)\s*([01])\b') { $values.disableUserInstalls = [int]$Matches[1] }
    if ($line -match '(?i)SOFTWARE RESTRICTION POLICY.*\b(?:disallowed|not allowed|forbidden)\b') { $values.softwareRestrictionOutcome = 'disallowed' }
    elseif ($line -match '(?i)SOFTWARE RESTRICTION POLICY.*\b(?:unrestricted|allowed|permitted)\b') { $values.softwareRestrictionOutcome = 'allowed' }
  }
  return $values
}
function Get-InstallerPolicy([string]$path) {
  $policy = Get-ItemProperty -Path $path -ErrorAction SilentlyContinue
  return [ordered]@{
    disableMsi = if ($null -ne $policy.DisableMSI) { [int]$policy.DisableMSI } else { $null }
    disableUserInstalls = if ($null -ne $policy.DisableUserInstalls) { [int]$policy.DisableUserInstalls } else { $null }
    alwaysInstallElevated = if ($null -ne $policy.AlwaysInstallElevated) { [int]$policy.AlwaysInstallElevated } else { $null }
  }
}
function Get-ExactMsiProperties([string]$path) {
  $installer = New-Object -ComObject WindowsInstaller.Installer
  $database = $installer.OpenDatabase($path, 0)
  $properties = [ordered]@{}
  foreach ($property in @('ALLUSERS', 'MSIINSTALLPERUSER', 'ProductVersion')) {
    $sql = "SELECT ``Value`` FROM ``Property`` WHERE ``Property`` = '$property'"
    $view = $database.OpenView($sql)
    [void]$view.Execute()
    $record = $view.Fetch()
    $properties[$property] = if ($record) { $record.StringData(1) } else { $null }
    [void]$view.Close()
  }
  return $properties
}
function Remove-ExactDisposableFile([string]$path) {
  if (-not $path) { return $null }
  for ($attempt = 0; $attempt -lt 5; $attempt++) {
    try { Remove-Item -LiteralPath $path -Force -ErrorAction Stop }
    catch { }
    if (-not (Test-Path -LiteralPath $path)) { return $true }
    Start-Sleep -Milliseconds 500
  }
  if ($user -and (Get-Command Invoke-ProfiledProcess -ErrorAction SilentlyContinue)) {
    $cleanupScript = (Resolve-Path 'source/script/windows_nonadmin_cleanup.ps1').Path
    $arguments = "-NoLogo -NoProfile -NonInteractive -File `"$cleanupScript`" -Path `"$path`""
    try {
      $cleanupExit = Invoke-ProfiledProcess -Username $name -Password $password -FilePath (Get-Command pwsh.exe).Source -Arguments $arguments -WorkingDirectory (Get-Location).Path
      if ($cleanupExit -eq 0 -and -not (Test-Path -LiteralPath $path)) { return $true }
    } catch { }
  }
  return $false
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
  $userInstallerPolicy = $token.userInstallerPolicy
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
  $osProductType = [int](Get-CimInstance Win32_OperatingSystem).ProductType
  $machineInstallerPolicy = Get-InstallerPolicy 'HKLM:\Software\Policies\Microsoft\Windows\Installer'
  try { $msiProperties = Get-ExactMsiProperties $stagedMsi }
  catch { $msiPropertyReadReason = 'installer-com-property-read-unavailable' }
  $stage = 'classify-runner-installer-policy'
  if ($machineInstallerPolicy.disableMsi -in @(1, 2) -or $machineInstallerPolicy.disableUserInstalls -eq 1) {
    throw 'Machine policy blocks this non-elevated per-user install; do not repeat the MSI attempt'
  }

  $stage = 'build-disposable-policy-control-msi'
  $controlOutput = Join-Path $env:RUNNER_TEMP 'AmplifaiDisposablePolicyControl'
  $control = & (Resolve-Path 'source/script/windows_msi_policy_control.ps1').Path -OutputDirectory $controlOutput
  $controlMsiHash = $control.sha256
  $controlMsiBytes = $control.bytes
  $controlStagedMsi = Join-Path $token.tokenKnownLocalAppData 'AmplifaiPolicyControl.msi'
  if (Test-Path -LiteralPath $controlStagedMsi) { throw 'Disposable control MSI staging path is not clean' }
  Copy-Item -LiteralPath $control.path -Destination $controlStagedMsi -ErrorAction Stop
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $controlStagedMsi).Hash.ToLowerInvariant() -ne $controlMsiHash) { throw 'Disposable control MSI staging copy differs' }
  $controlInstalled = Join-Path $token.tokenKnownLocalAppData 'AMPLIFaiPolicyControl'
  $controlInstallLog = Join-Path $token.tokenKnownLocalAppData 'amplifai-control-install.log'
  $controlUninstallLog = Join-Path $token.tokenKnownLocalAppData 'amplifai-control-uninstall.log'
  if (Test-Path -LiteralPath $controlInstalled) { throw 'Disposable control install path is not clean' }
  $stage = 'install-disposable-policy-control-msi'
  $controlInstallExit = Invoke-ProfiledProcess -Username $name -Password $password -FilePath (Get-Command msiexec.exe).Source -Arguments "/i `"$controlStagedMsi`" /qn /norestart /L*V `"$controlInstallLog`"" -WorkingDirectory (Get-Location).Path
  if ($controlInstallExit -ne 0) { throw "Disposable per-user policy control MSI install failed: $controlInstallExit" }
  $controlFile = Join-Path $controlInstalled 'control.txt'
  $controlInventoryVerified = (Test-Path -LiteralPath $controlFile) -and (Get-Content -Raw -LiteralPath $controlFile).Trim() -eq 'synthetic per-user MSI policy control'
  if (-not $controlInventoryVerified) { throw 'Disposable policy control install inventory is incomplete' }
  $stage = 'uninstall-disposable-policy-control-msi'
  $controlUninstallExit = Invoke-ProfiledProcess -Username $name -Password $password -FilePath (Get-Command msiexec.exe).Source -Arguments "/x `"$controlStagedMsi`" /qn /norestart /L*V `"$controlUninstallLog`"" -WorkingDirectory (Get-Location).Path
  if ($controlUninstallExit -ne 0) { throw "Disposable per-user policy control MSI uninstall failed: $controlUninstallExit" }
  $controlPackageRemoved = -not (Test-Path -LiteralPath $controlInstalled)
  if (-not $controlPackageRemoved) { throw 'Disposable policy control package file remains after uninstall' }

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
  if ($user -and $controlStagedMsi -and $controlInstalled -and (Test-Path $controlInstalled) -and -not $controlPackageRemoved -and (Get-Command Invoke-ProfiledProcess -ErrorAction SilentlyContinue)) {
    try {
      $controlCleanupExit = Invoke-ProfiledProcess -Username $name -Password $password -FilePath (Get-Command msiexec.exe).Source -Arguments "/x `"$controlStagedMsi`" /qn /norestart /L*V `"$controlUninstallLog`"" -WorkingDirectory (Get-Location).Path
      if ($controlCleanupExit -eq 0) { $controlPackageRemoved = -not (Test-Path -LiteralPath $controlInstalled) }
    } catch { $cleanupFailureReason = 'control-profiled-uninstall-retry-failed' }
  }
  $installCodes = @(Get-BoundedMsiCodes $installLog)
  $uninstallCodes = @(Get-BoundedMsiCodes $uninstallLog)
  $installFailureActions = @(Get-BoundedMsiFailureActions $installLog)
  $uninstallFailureActions = @(Get-BoundedMsiFailureActions $uninstallLog)
  $installPolicyReasons = @(Get-BoundedMsiPolicyReasons $installLog)
  $uninstallPolicyReasons = @(Get-BoundedMsiPolicyReasons $uninstallLog)
  $controlInstallCodes = @(Get-BoundedMsiCodes $controlInstallLog)
  $controlUninstallCodes = @(Get-BoundedMsiCodes $controlUninstallLog)
  $controlInstallPolicyReasons = @(Get-BoundedMsiPolicyReasons $controlInstallLog)
  $installLogPolicyValues = Get-BoundedMsiLogPolicyValues $installLog
  $controlInstallLogPolicyValues = Get-BoundedMsiLogPolicyValues $controlInstallLog
  $productStagedRemoved = Remove-ExactDisposableFile $stagedMsi
  $controlStagedRemoved = Remove-ExactDisposableFile $controlStagedMsi
  $logRemoved = @($installLog, $uninstallLog, $controlInstallLog, $controlUninstallLog) | Where-Object { $_ } | ForEach-Object { Remove-ExactDisposableFile $_ }
  $privateLogsRemoved = @($logRemoved | Where-Object { $_ -eq $false }).Count -eq 0
  if ($productStagedRemoved -eq $false -or $controlStagedRemoved -eq $false -or -not $privateLogsRemoved) {
    $cleanupFailureReason = 'disposable-profile-file-cleanup-incomplete'
    if (-not $firstFailure) { $firstFailure = 'remove-disposable-test-files' }
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
    installLogPolicyReasons = $installPolicyReasons
    installLogPolicyValues = $installLogPolicyValues
    uninstallLogPolicyReasons = $uninstallPolicyReasons
    controlMsiSha256 = $controlMsiHash
    controlMsiBytes = $controlMsiBytes
    controlMsiScope = 'perUser-limited-synthetic'
    controlInstallExitCode = $controlInstallExit
    controlUninstallExitCode = $controlUninstallExit
    controlInstalledInventoryVerified = $controlInventoryVerified
    controlUninstallPackageResidueAbsent = $controlPackageRemoved
    controlInstallLogErrorCodes = $controlInstallCodes
    controlUninstallLogErrorCodes = $controlUninstallCodes
    controlInstallLogPolicyReasons = $controlInstallPolicyReasons
    controlInstallLogPolicyValues = $controlInstallLogPolicyValues
    osProductType = $osProductType
    machineInstallerPolicy = $machineInstallerPolicy
    userInstallerPolicy = $userInstallerPolicy
    exactMsiProperties = $msiProperties
    exactMsiPropertyReadReason = $msiPropertyReadReason
    stagedMsiCopyRemoved = $productStagedRemoved
    controlMsiCopyRemoved = $controlStagedRemoved
    disposablePrivateLogsRemoved = $privateLogsRemoved
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
