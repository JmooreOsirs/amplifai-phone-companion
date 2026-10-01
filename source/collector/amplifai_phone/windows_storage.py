"""Windows-only owner-private storage and non-destructive process checks."""

from __future__ import annotations

import ctypes
import ntpath
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

# LiteralPath and environment input keep paths out of PowerShell source and output.
_ACL_SCRIPT = r"""$ErrorActionPreference = 'Stop'
$phase = 10
try {
    $folder = $env:AMPLIFAI_PRIVATE_STORAGE_PATH
    $current = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    if ($env:AMPLIFAI_PRIVATE_STORAGE_ACTION -eq 'protect') {
        $phase = 11
        $acl = [System.Security.AccessControl.DirectorySecurity]::new()
        $phase = 12
        $acl.SetOwner($current)
        $acl.SetAccessRuleProtection($true, $false)
        $phase = 13
        foreach ($sid in @($current, [System.Security.Principal.SecurityIdentifier]'S-1-5-18')) {
            $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
                $sid,
                [System.Security.AccessControl.FileSystemRights]::FullControl,
                ([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
                 [System.Security.AccessControl.InheritanceFlags]::ObjectInherit),
                [System.Security.AccessControl.PropagationFlags]::None,
                [System.Security.AccessControl.AccessControlType]::Allow)
            $acl.AddAccessRule($rule)
        }
        $phase = 14
        [System.IO.DirectoryInfo]::new($folder).SetAccessControl($acl)
    }
    $phase = 15
    $acl = Get-Acl -LiteralPath $folder
    $phase = 16
    if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $current.Value) { exit 3 }
    if (-not $acl.AreAccessRulesProtected) { exit 3 }
    $ownerAllowed = $false
    foreach ($rule in $acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow') {
            if ($rule.IdentityReference.Value -notin @($current.Value, 'S-1-5-18')) { exit 3 }
            if ($rule.IdentityReference.Value -eq $current.Value -and
                ($rule.FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -eq
                 [System.Security.AccessControl.FileSystemRights]::FullControl) { $ownerAllowed = $true }
        }
    }
    if (-not $ownerAllowed) { exit 3 }
    exit 0
} catch { exit $phase }
"""


def _windows_powershell() -> str:
    """Use the OS-owned executable, never PATH or the current working directory."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = (wintypes.LPWSTR, wintypes.UINT)
    kernel.GetSystemDirectoryW.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetSystemDirectoryW(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise OSError("Windows system directory could not be resolved")
    return ntpath.join(buffer.value, "WindowsPowerShell", "v1.0", "powershell.exe")


def private_windows_directory(path: Path, *, create: bool = False) -> None:
    """Protect newly-created directories; reject unsafe existing ACLs, never repair them."""
    if os.name != "nt":
        raise OSError("Windows storage guard called on a different platform")
    environment = os.environ.copy()
    environment["AMPLIFAI_PRIVATE_STORAGE_PATH"] = str(path)
    environment["AMPLIFAI_PRIVATE_STORAGE_ACTION"] = "protect" if create else "verify"
    try:
        result = subprocess.run(
            [
                _windows_powershell(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _ACL_SCRIPT,
            ],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OSError("Windows private storage could not be checked") from error
    if result.returncode != 0:
        # The numeric phase is safe diagnostic evidence; never expose a SID,
        # storage path, raw PowerShell exception or user-controlled stderr.
        raise PermissionError(
            "Windows private storage owner or ACL is unsafe "
            f"(policy exit code {result.returncode})"
        )


def windows_process_alive(pid: int) -> bool:
    """Open a synchronize-only handle; never use os.kill on Windows."""
    if pid <= 0:
        return False
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        code = ctypes.get_last_error()
        if code == 87:
            return False
        if code == 5:
            return True
        raise OSError("Windows process status could not be checked")
    try:
        status = kernel.WaitForSingleObject(handle, 0)
        if status == 258:
            return True
        if status == 0:
            return False
        raise OSError("Windows process status could not be checked")
    finally:
        kernel.CloseHandle(handle)
