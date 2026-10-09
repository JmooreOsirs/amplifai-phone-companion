$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class AmplifaiKnownFolderProbe {
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SHGetKnownFolderPath(ref Guid folderId, uint flags, IntPtr token, out IntPtr path);
    public static string Read(string id) {
        var folderId = new Guid(id);
        IntPtr path;
        var result = SHGetKnownFolderPath(ref folderId, 0, IntPtr.Zero, out path);
        if (result != 0) Marshal.ThrowExceptionForHR(result);
        try { return Marshal.PtrToStringUni(path); }
        finally { Marshal.FreeCoTaskMem(path); }
    }
}
'@
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
[ordered]@{
  sid = $identity.User.Value
  enabledAdministratorRole = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  tokenKnownProfile = [AmplifaiKnownFolderProbe]::Read('5E6C858F-0E22-4760-9AFE-EA3317B67173')
  tokenKnownLocalAppData = [AmplifaiKnownFolderProbe]::Read('F1B32785-6FBA-4FCF-9D55-7B8E7F157091')
  tokenHiveLoaded = Test-Path ("Registry::HKEY_USERS\" + $identity.User.Value)
  inheritedEnvironmentProfile = $env:USERPROFILE
  inheritedEnvironmentLocalAppData = $env:LOCALAPPDATA
} | ConvertTo-Json -Compress
