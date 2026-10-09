param(
  [Parameter(Mandatory=$true)][string]$Msi,
  [Parameter(Mandatory=$true)][string]$FrozenReceipt
)

$ErrorActionPreference = 'Stop'
$name = 'ampfiqa' + $env:GITHUB_RUN_ID.Substring([Math]::Max(0, $env:GITHUB_RUN_ID.Length - 5))
$password = ConvertTo-SecureString (([Guid]::NewGuid().ToString('N')) + 'aA1!') -AsPlainText -Force
$user = $null
$credential = $null
$profile = $null
$installed = $null
$installExit = $null
$uninstallExit = $null
$tokenVerified = $false
$profileVerified = $false
$inventoryVerified = $false
$packageRemoved = $false
$firstFailure = $null
$firstFailureHResult = $null
$stage = 'create-disposable-user'
$accountRemoved = $false
$tokenFile = Join-Path $env:RUNNER_TEMP 'amplifai-nonadmin-token-private.json'
$installLog = Join-Path $env:RUNNER_TEMP 'amplifai-nonadmin-install.log'
$uninstallLog = Join-Path $env:RUNNER_TEMP 'amplifai-nonadmin-uninstall.log'
try {
  $user = New-LocalUser -Name $name -Password $password -Description 'Disposable AMPLIFai MSI acceptance fixture'
  $administrators = Get-LocalGroup -SID 'S-1-5-32-544'
  $isAdmin = Get-LocalGroupMember -Group $administrators.Name | Where-Object { $_.SID.Value -eq $user.SID.Value }
  if ($isAdmin) { throw 'Disposable account unexpectedly has administrative membership' }
  $credential = [pscredential]::new(".\$name", $password)
  $profile = Join-Path $env:SystemDrive "Users\$name"
  $installed = Join-Path $profile 'AppData\Local\AMPLIFaiPhone'
  if (Test-Path $installed) { throw 'Disposable install path is not clean' }

  $stage = 'probe-standard-user-token-and-profile'
  $probe = (Resolve-Path 'source/script/windows_nonadmin_child_probe.ps1').Path
  $child = Start-Process -FilePath 'pwsh.exe' -Credential $credential -LoadUserProfile -ArgumentList @('-NoLogo', '-NoProfile', '-NonInteractive', '-File', "`"$probe`"") -RedirectStandardOutput $tokenFile -Wait -PassThru
  if ($child.ExitCode -ne 0) { throw "Standard-user token probe failed: $($child.ExitCode)" }
  $token = Get-Content -Raw $tokenFile | ConvertFrom-Json
  if ($token.sid -ne $user.SID.Value -or $token.enabledAdministratorRole -ne $false) { throw 'Child process token is not the disposable standard user' }
  $tokenVerified = $true
  if ($token.profile -ne $profile -or $token.localAppData -ne (Join-Path $profile 'AppData\Local') -or -not (Test-Path $profile)) { throw 'Child process profile was not loaded as expected' }
  $profileVerified = $true

  $stage = 'install-private-msi'
  $install = Start-Process -FilePath 'msiexec.exe' -Credential $credential -LoadUserProfile -ArgumentList "/i `"$Msi`" /qn /norestart /L*V `"$installLog`"" -Wait -PassThru
  $installExit = $install.ExitCode
  if ($installExit -ne 0) { throw "Standard-user MSI install failed: $installExit" }
  $stage = 'verify-installed-frozen-inventory'
  python source/script/verify_windows_install.py --install $installed --receipt $FrozenReceipt
  if ($LASTEXITCODE -ne 0) { throw 'Standard-user installed inventory failed' }
  $inventoryVerified = $true

  $stage = 'uninstall-private-msi'
  $uninstall = Start-Process -FilePath 'msiexec.exe' -Credential $credential -LoadUserProfile -ArgumentList "/x `"$Msi`" /qn /norestart /L*V `"$uninstallLog`"" -Wait -PassThru
  $uninstallExit = $uninstall.ExitCode
  if ($uninstallExit -ne 0) { throw "Standard-user MSI uninstall failed: $uninstallExit" }
  $stage = 'verify-no-installed-package-residue'
  python source/script/verify_windows_install.py --install $installed --receipt $FrozenReceipt --removed
  if ($LASTEXITCODE -ne 0) { throw 'Standard-user uninstall left package files' }
  $packageRemoved = $true
} catch {
  $firstFailure = $stage
  $firstFailureHResult = $_.Exception.HResult
  Write-Error "Focused standard-user check failed at $stage (HRESULT $firstFailureHResult)" -ErrorAction Continue
} finally {
  if ($credential -and $installed -and (Test-Path $installed) -and -not $packageRemoved) {
    $cleanup = Start-Process -FilePath 'msiexec.exe' -Credential $credential -LoadUserProfile -ArgumentList "/x `"$Msi`" /qn /norestart /L*V `"$uninstallLog`"" -Wait -PassThru -ErrorAction SilentlyContinue
    if ($cleanup -and $cleanup.ExitCode -eq 0) { $packageRemoved = -not (Test-Path $installed) }
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
    childProfileAndLocalAppDataVerified = $profileVerified
    installedInventoryMatchedFrozenReceipt = $inventoryVerified
    uninstallPackageResidueAbsent = $packageRemoved
    disposableAccountRemoved = $accountRemoved
    installExitCode = $installExit
    uninstallExitCode = $uninstallExit
    installedPathClass = 'disposable-local-user-LocalAppData'
    ownerPhoneOrAccountUsed = $false
    firstFailureStage = $firstFailure
    firstFailureHResult = $firstFailureHResult
  }
  $receipt | ConvertTo-Json -Depth 3 | Set-Content -Encoding utf8 (Join-Path $env:RUNNER_TEMP 'nonadmin-receipt.json')
  Remove-Item $tokenFile -Force -ErrorAction SilentlyContinue
}
if ($firstFailure) { exit 1 }
$receipt.status
