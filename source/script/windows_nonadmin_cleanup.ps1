param([Parameter(Mandatory=$true)][string]$Path)

$ErrorActionPreference = 'Stop'
$localAppData = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
$target = [IO.Path]::GetFullPath($Path)
$allowed = @(
  'AmplifaiPhone-rc14-unsigned.msi', 'AmplifaiPolicyControl.msi',
  'amplifai-nonadmin-install.log', 'amplifai-nonadmin-uninstall.log',
  'amplifai-control-install.log', 'amplifai-control-uninstall.log'
)
if ([IO.Path]::GetDirectoryName($target) -ne $localAppData -or [IO.Path]::GetFileName($target) -notin $allowed) { exit 2 }
for ($attempt = 0; $attempt -lt 5; $attempt++) {
  try { Remove-Item -LiteralPath $target -Force -ErrorAction Stop }
  catch { }
  if (-not (Test-Path -LiteralPath $target)) { exit 0 }
  Start-Sleep -Milliseconds 500
}
exit 1
