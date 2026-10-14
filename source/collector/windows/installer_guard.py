"""Native Windows file pinning for the explicitly selected MSI installer."""
from __future__ import annotations

import ctypes
import os
import stat
from pathlib import Path

REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class InstallerGuardError(ValueError):
    pass


def windows_installer_path() -> Path:
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetSystemDirectoryW.argtypes = (wintypes.LPWSTR, wintypes.UINT)
    kernel.GetSystemDirectoryW.restype = wintypes.UINT
    value = ctypes.create_unicode_buffer(32768)
    length = kernel.GetSystemDirectoryW(value, len(value))
    if not 0 < length < len(value):
        raise InstallerGuardError("Windows system directory could not be verified.")
    return Path(value.value) / "msiexec.exe"


class InstallerReadGuard:
    """Windows handles pin the directory and allow MSI reads while denying edits/deletion."""
    def __init__(self, directory: Path, path: Path) -> None:
        self.directory, self.path = directory, path
        self.kernel = None
        self.directory_handle = None
        self.stream = None

    def open(self) -> None:
        import msvcrt
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        invalid = ctypes.c_void_p(-1).value
        parent = self.directory.lstat()
        if not stat.S_ISDIR(parent.st_mode) or getattr(parent, "st_file_attributes", 0) & REPARSE_POINT:
            raise InstallerGuardError("Prepared update directory changed.")
        # READ_ATTRIBUTES, READ sharing only (no data-write or DELETE sharing), OPEN_EXISTING, backup+reparse flags.
        self.directory_handle = kernel.CreateFileW(str(self.directory), 0x80, 1, None, 3, 0x02200000, None)
        if self.directory_handle in {None, invalid}:
            self.directory_handle = None
            raise InstallerGuardError("Prepared update directory could not be protected.")
        try:
            parent = self.directory.lstat()
            if not stat.S_ISDIR(parent.st_mode) or getattr(parent, "st_file_attributes", 0) & REPARSE_POINT:
                raise InstallerGuardError("Prepared update directory changed.")
            # GENERIC_READ, READ sharing only, OPEN_EXISTING, OPEN_REPARSE_POINT.
            handle = kernel.CreateFileW(str(self.path), 0x80000000, 1, None, 3, 0x00200000, None)
            if handle in {None, invalid}:
                raise InstallerGuardError("Prepared update file could not be protected.")
            try:
                descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except OSError:
                kernel.CloseHandle(handle)
                raise
            self.stream = os.fdopen(descriptor, "rb")
            item = os.fstat(self.stream.fileno())
            if not stat.S_ISREG(item.st_mode) or getattr(item, "st_file_attributes", 0) & REPARSE_POINT:
                raise InstallerGuardError("Prepared update file is not a regular immutable MSI.")
        except (OSError, InstallerGuardError):
            self.close()
            raise

    def read(self, limit: int) -> bytes:
        if self.stream is None:
            raise InstallerGuardError("Prepared update file is not protected.")
        return self.stream.read(limit)

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self.directory_handle is not None:
            self.kernel.CloseHandle(self.directory_handle)
            self.directory_handle = None
