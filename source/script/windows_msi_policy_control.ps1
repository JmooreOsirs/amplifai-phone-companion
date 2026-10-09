param([Parameter(Mandatory=$true)][string]$OutputDirectory)

$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Disposable policy-control output already exists' }
New-Item -ItemType Directory -Path $OutputDirectory -ErrorAction Stop | Out-Null
$wixBin = 'C:\Program Files (x86)\WiX Toolset v3.14\bin'
$candle = Join-Path $wixBin 'candle.exe'
$light = Join-Path $wixBin 'light.exe'
if (-not (Test-Path -LiteralPath $candle) -or -not (Test-Path -LiteralPath $light)) { throw 'Pinned WiX 3.14 toolchain is unavailable' }
if ((Get-Item $candle).VersionInfo.FileVersion -notlike '3.14.*' -or (Get-Item $light).VersionInfo.FileVersion -notlike '3.14.*') { throw 'Unexpected WiX toolchain version' }

$payload = Join-Path $OutputDirectory 'control.txt'
Set-Content -LiteralPath $payload -Value 'synthetic per-user MSI policy control' -Encoding ascii
$source = [Security.SecurityElement]::Escape($payload)
$upgradeCode = [Guid]::NewGuid().ToString().ToUpperInvariant()
$componentCode = [Guid]::NewGuid().ToString().ToUpperInvariant()
$xml = @"
<?xml version="1.0" encoding="utf-8"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">
  <Product Id="*" Name="AMPLIFai disposable MSI policy control" Language="1033" Version="1.0.0" Manufacturer="Satoris" UpgradeCode="$upgradeCode">
    <Package InstallerVersion="500" Compressed="yes" InstallScope="perUser" InstallPrivileges="limited" />
    <MediaTemplate EmbedCab="yes" />
    <Directory Id="TARGETDIR" Name="SourceDir">
      <Directory Id="LocalAppDataFolder">
        <Directory Id="ControlDirectory" Name="AMPLIFaiPolicyControl">
          <Component Id="ControlComponent" Guid="$componentCode">
            <File Id="ControlFile" Name="control.txt" Source="$source" />
            <RegistryValue Root="HKCU" Key="Software\Satoris\AMPLIFaiPolicyControl" Name="installed" Value="1" Type="integer" KeyPath="yes" />
            <RemoveFolder Id="RemoveControlDirectory" Directory="ControlDirectory" On="uninstall" />
          </Component>
        </Directory>
      </Directory>
    </Directory>
    <Feature Id="Complete" Title="Policy control" Level="1">
      <ComponentRef Id="ControlComponent" />
    </Feature>
  </Product>
</Wix>
"@
$wxs = Join-Path $OutputDirectory 'policy-control.wxs'
$object = Join-Path $OutputDirectory 'policy-control.wixobj'
$msi = Join-Path $OutputDirectory 'policy-control.msi'
$candleLog = Join-Path $OutputDirectory 'candle-private.log'
$lightLog = Join-Path $OutputDirectory 'light-private.log'
Set-Content -LiteralPath $wxs -Value $xml -Encoding utf8
& $candle -nologo -arch x64 -out $object $wxs *> $candleLog
if ($LASTEXITCODE -ne 0) { throw "Disposable WiX compile failed: $LASTEXITCODE" }
& $light -nologo -out $msi $object *> $lightLog
if ($LASTEXITCODE -ne 0) { throw "Disposable WiX link failed: $LASTEXITCODE" }
[ordered]@{
  path = $msi
  sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $msi).Hash.ToLowerInvariant()
  bytes = (Get-Item $msi).Length
  scope = 'perUser-limited'
}
