"""Native Windows owner-private storage and non-destructive process checks.

Creation supplies a protected descriptor to CreateDirectoryW, not a post-mkdir
repair. The caller must hold the creation context through its first marker write.
GetSecurityInfo reads a fresh OS descriptor; no supplied ACL is trusted as proof.
See Microsoft's CreateDirectoryW, GetSecurityInfo and ACCESS_ALLOWED_ACE contracts.
No shell provider, privilege adjustment, SID/path logging or deletion is used.
"""

from __future__ import annotations

import ctypes
import ntpath
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from pathlib import Path

# Windows uses LLP64. c_ulong/wintypes.DWORD are host-sized on non-Windows
# synthetic test hosts, so every security structure uses explicit Windows widths.
_DWORD = ctypes.c_uint32
_WORD = ctypes.c_uint16
_BYTE = ctypes.c_ubyte
_BOOL = ctypes.c_int32
_PTR = ctypes.c_void_p
_PPTR = ctypes.POINTER(_PTR)
_FULL_CONTROL = 0x001F01FF
_INHERIT_CHILDREN = 0x03  # OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE
_DACL_PRESENT = 0x0004
_DACL_DEFAULTED = 0x0008
_DACL_PROTECTED = 0x1000
_MAX_SID_BYTES = 68  # SECURITY_MAX_SID_SIZE: 8 + 15 DWORD subauthorities
_MAX_TOKEN_BYTES = 4096
_INVALID_HANDLE = _PTR(-1).value
_READ_CONTROL = 0x00020000
_FILE_READ_ATTRIBUTES = 0x00000080


class _DirectoryHandleError(PermissionError):
    """Immediate OS diagnosis without a path, SID or formatted Win32 message."""

    def __init__(
        self, win32_code: int, requested_access: int, ancestor: bool, *, api_code: int
    ) -> None:
        self.native_stage = 15
        self.win32_code = win32_code
        self.requested_access = requested_access
        self.ancestor = ancestor
        self.api_code = api_code  # 1: CreateFileW; 2: attribute readback.
        super().__init__(
            "Windows private storage owner or ACL is unsafe (native policy stage 15)"
        )


class _TokenUser(ctypes.Structure):
    _fields_ = [("Sid", _PTR), ("Attributes", _DWORD)]


class _Acl(ctypes.Structure):
    _fields_ = [("Revision", _BYTE), ("Reserved", _BYTE), ("Size", _WORD),
                ("Count", _WORD), ("Reserved2", _WORD)]


class _AceHeader(ctypes.Structure):
    _fields_ = [("Type", _BYTE), ("Flags", _BYTE), ("Size", _WORD)]


class _AllowedAce(ctypes.Structure):
    _fields_ = [("Header", _AceHeader), ("Mask", _DWORD), ("SidStart", _DWORD)]


class _AclSizeInformation(ctypes.Structure):
    _fields_ = [("Count", _DWORD), ("BytesInUse", _DWORD), ("BytesFree", _DWORD)]


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = [("Attributes", _DWORD), ("ReparseTag", _DWORD)]


class _SecurityDescriptor(ctypes.Structure):
    _fields_ = [("Revision", _BYTE), ("Reserved", _BYTE), ("Control", _WORD),
                ("Owner", _PTR), ("Group", _PTR), ("Sacl", _PTR), ("Dacl", _PTR)]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("Length", _DWORD), ("Descriptor", _PTR), ("InheritHandle", _BOOL)]


def _require(value: object, stage: int) -> None:
    if not value:
        raise PermissionError(
            "Windows private storage owner or ACL is unsafe "
            f"(native policy stage {stage})"
        ) from None


def _bind(library: object, name: str, result: object, *arguments: object) -> None:
    function = getattr(library, name)
    function.argtypes = arguments
    function.restype = result


class _NativeSecurity:
    def __init__(self) -> None:
        try:
            # The bootstrap DLL is restricted to System32; all subsequent DLLs
            # use the OS's actual system-directory path, never PATH/cwd/env.
            self.kernel = ctypes.WinDLL(
                "kernel32.dll", use_last_error=True, winmode=0x00000800
            )
            _bind(self.kernel, "GetSystemDirectoryW", _DWORD, ctypes.c_wchar_p, _DWORD)
            directory = ctypes.create_unicode_buffer(32768)
            length = self.kernel.GetSystemDirectoryW(directory, len(directory))
            _require(0 < length < len(directory), 10)
            self.security = ctypes.WinDLL(
                ntpath.join(directory.value, "advapi32.dll"),
                use_last_error=True, winmode=0x00000800,
            )
            for name in ("GetCurrentProcess", "GetCurrentThread"):
                _bind(self.kernel, name, _PTR)
            _bind(self.kernel, "CreateFileW", _PTR, ctypes.c_wchar_p, _DWORD,
                  _DWORD, _PTR, _DWORD, _DWORD, _PTR)
            _bind(self.kernel, "CreateDirectoryW", _BOOL, ctypes.c_wchar_p, _PTR)
            _bind(self.kernel, "GetFileInformationByHandleEx", _BOOL,
                  _PTR, ctypes.c_int32, _PTR, _DWORD)
            _bind(self.kernel, "CloseHandle", _BOOL, _PTR)
            _bind(self.kernel, "LocalFree", _PTR, _PTR)
            _bind(self.security, "OpenThreadToken", _BOOL, _PTR, _DWORD, _BOOL, _PPTR)
            _bind(self.security, "OpenProcessToken", _BOOL, _PTR, _DWORD, _PPTR)
            _bind(self.security, "GetTokenInformation", _BOOL, _PTR,
                  ctypes.c_int32, _PTR, _DWORD, ctypes.POINTER(_DWORD))
            _bind(self.security, "CreateWellKnownSid", _BOOL, ctypes.c_int32,
                  _PTR, _PTR, ctypes.POINTER(_DWORD))
            _bind(self.security, "IsValidSid", _BOOL, _PTR)
            _bind(self.security, "GetLengthSid", _DWORD, _PTR)
            _bind(self.security, "EqualSid", _BOOL, _PTR, _PTR)
            _bind(self.security, "InitializeAcl", _BOOL, _PTR, _DWORD, _DWORD)
            _bind(self.security, "AddAccessAllowedAceEx", _BOOL,
                  _PTR, _DWORD, _DWORD, _DWORD, _PTR)
            _bind(self.security, "InitializeSecurityDescriptor", _BOOL, _PTR, _DWORD)
            _bind(self.security, "SetSecurityDescriptorOwner", _BOOL, _PTR, _PTR, _BOOL)
            _bind(self.security, "SetSecurityDescriptorDacl", _BOOL,
                  _PTR, _BOOL, _PTR, _BOOL)
            _bind(self.security, "SetSecurityDescriptorControl", _BOOL, _PTR, _WORD, _WORD)
            _bind(self.security, "GetSecurityInfo", _DWORD, _PTR,
                  ctypes.c_int32, _DWORD, _PPTR, _PPTR, _PPTR, _PPTR, _PPTR)
            _bind(self.security, "IsValidSecurityDescriptor", _BOOL, _PTR)
            _bind(self.security, "GetSecurityDescriptorLength", _DWORD, _PTR)
            _bind(self.security, "GetSecurityDescriptorControl", _BOOL,
                  _PTR, ctypes.POINTER(_WORD), ctypes.POINTER(_DWORD))
            _bind(self.security, "GetSecurityDescriptorDacl", _BOOL, _PTR,
                  ctypes.POINTER(_BOOL), _PPTR, ctypes.POINTER(_BOOL))
            _bind(self.security, "IsValidAcl", _BOOL, _PTR)
            _bind(self.security, "GetAclInformation", _BOOL, _PTR,
                  _PTR, _DWORD, ctypes.c_int32)
            _bind(self.security, "GetAce", _BOOL, _PTR, _DWORD, _PPTR)
        except (OSError, AttributeError, ValueError):
            _require(False, 10)

    def close(self, handle: object) -> None:
        _require(self.kernel.CloseHandle(handle), 16)

    def sid_length(self, principal: int, lower: int, upper: int, stage: int) -> int:
        _require(lower <= principal and principal + 8 <= upper, stage)
        revision, count = ctypes.string_at(principal, 2)
        length = 8 + 4 * count
        _require(revision == 1 and count <= 15 and principal + length <= upper, stage)
        _require(self.security.IsValidSid(principal), stage)
        _require(self.security.GetLengthSid(principal) == length, stage)
        return length

    def current_sid(self) -> object:
        token = _PTR()
        opened = self.security.OpenThreadToken(
            self.kernel.GetCurrentThread(), 0x0008, True, ctypes.byref(token)
        )
        if not opened:
            # Only an absent impersonation token permits process-token fallback.
            _require(ctypes.get_last_error() == 1008, 11)
            _require(self.security.OpenProcessToken(
                self.kernel.GetCurrentProcess(), 0x0008, ctypes.byref(token)
            ), 11)
        _require(token.value not in (None, _INVALID_HANDLE), 11)
        try:
            needed = _DWORD()
            result = self.security.GetTokenInformation(
                token, 1, None, 0, ctypes.byref(needed)
            )
            _require(not result and ctypes.get_last_error() == 122, 11)
            _require(ctypes.sizeof(_TokenUser) + 8 <= needed.value <= _MAX_TOKEN_BYTES, 11)
            buffer = ctypes.create_string_buffer(needed.value)
            _require(self.security.GetTokenInformation(
                token, 1, buffer, len(buffer), ctypes.byref(needed)
            ), 11)
            _require(ctypes.sizeof(_TokenUser) + 8 <= needed.value <= len(buffer), 11)
            base = ctypes.addressof(buffer)
            principal = _TokenUser.from_buffer(buffer).Sid or 0
            length = self.sid_length(principal, base + ctypes.sizeof(_TokenUser),
                                     base + needed.value, 11)
            return ctypes.create_string_buffer(ctypes.string_at(principal, length), length)
        finally:
            self.close(token.value)

    def principals(self) -> tuple[object, ...]:
        owner = self.current_sid()
        system = ctypes.create_string_buffer(_MAX_SID_BYTES)
        length = _DWORD(len(system))
        _require(self.security.CreateWellKnownSid(
            22, None, system, ctypes.byref(length)
        ), 12)
        _require(8 <= length.value <= len(system), 12)
        base = ctypes.addressof(system)
        self.sid_length(base, base, base + length.value, 12)
        if self.security.EqualSid(owner, system):
            return (owner,)
        return owner, system

    def descriptor(self, principals: tuple[object, ...]) -> tuple[object, object]:
        size = ctypes.sizeof(_Acl) + sum(
            _AllowedAce.SidStart.offset + self.security.GetLengthSid(sid)
            for sid in principals
        )
        acl = ctypes.create_string_buffer(size)
        _require(self.security.InitializeAcl(acl, size, 2), 13)
        for principal in principals:
            _require(self.security.AddAccessAllowedAceEx(
                acl, 2, _INHERIT_CHILDREN, _FULL_CONTROL, principal
            ), 13)
        descriptor = _SecurityDescriptor()
        pointer = ctypes.byref(descriptor)
        _require(self.security.InitializeSecurityDescriptor(pointer, 1), 13)
        _require(self.security.SetSecurityDescriptorOwner(pointer, principals[0], False), 13)
        _require(self.security.SetSecurityDescriptorDacl(pointer, True, acl, False), 13)
        _require(self.security.SetSecurityDescriptorControl(
            pointer, _DACL_PROTECTED, _DACL_PROTECTED
        ), 13)
        return descriptor, acl

    def open_directory(
        self, path: Path, lifetime: ExitStack, *, ancestor: bool = False
    ) -> object:
        # OPEN_REPARSE_POINT inspects the directory itself, not a junction target.
        # No FILE_SHARE_DELETE: the parent/root cannot be replaced while pinned.
        # Zero-access ancestor handles can inspect attributes without requiring
        # FILE_READ_ATTRIBUTES on protected parents. The root still needs its
        # independent owner/DACL readback rights.
        # Only the root requires READ_CONTROL for independent owner/DACL readback.
        access = 0 if ancestor else _FILE_READ_ATTRIBUTES | _READ_CONTROL
        handle = self.kernel.CreateFileW(
            str(path), access, 0x03, None, 3, 0x02200000, None
        )
        if handle in (None, 0, _INVALID_HANDLE):
            raise _DirectoryHandleError(
                ctypes.get_last_error(), access, ancestor, api_code=1
            ) from None
        lifetime.callback(self.close, handle)
        information = _FileAttributeTagInfo()
        if not self.kernel.GetFileInformationByHandleEx(
            handle, 9, ctypes.byref(information), ctypes.sizeof(information)
        ):
            raise _DirectoryHandleError(
                ctypes.get_last_error(), access, ancestor, api_code=2
            ) from None
        _require(information.Attributes & 0x10 and not information.Attributes & 0x400
                 and information.ReparseTag == 0, 15)
        return handle

    def pin_parents(self, path: Path, lifetime: ExitStack) -> None:
        # A final-component no-follow flag alone still traverses ancestor
        # junctions. Inspect and pin the whole chain before a root is admitted.
        for ancestor in reversed(path.parents):
            self.open_directory(ancestor, lifetime, ancestor=True)

    def verify(self, handle: object, principals: tuple[object, ...]) -> None:
        owner, dacl, descriptor = _PTR(), _PTR(), _PTR()
        try:
            # A fresh OS allocation is independent of the creation descriptor.
            _require(self.security.GetSecurityInfo(
                handle, 1, 0x05, ctypes.byref(owner), None,
                ctypes.byref(dacl), None, ctypes.byref(descriptor)
            ) == 0 and descriptor.value, 20)
            _require(self.security.IsValidSecurityDescriptor(descriptor), 21)
            length = self.security.GetSecurityDescriptorLength(descriptor)
            _require(20 <= length <= 131072, 21)
            base, end = descriptor.value, descriptor.value + length
            control, revision = _WORD(), _DWORD()
            _require(self.security.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)
            ), 21)
            required = _DACL_PRESENT | _DACL_PROTECTED
            _require(revision.value == 1 and control.value & required == required
                     and not control.value & _DACL_DEFAULTED, 21)
            self.sid_length(owner.value or 0, base, end, 22)
            _require(self.security.EqualSid(owner, principals[0]), 22)
            present, defaulted, checked_dacl = _BOOL(), _BOOL(), _PTR()
            _require(self.security.GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(checked_dacl),
                ctypes.byref(defaulted)
            ), 23)
            _require(present.value and not defaulted.value and dacl.value
                     and dacl.value == checked_dacl.value, 23)
            _require(base <= dacl.value and dacl.value + ctypes.sizeof(_Acl) <= end, 23)
            header = _Acl.from_address(dacl.value)
            _require(header.Revision == 2 and header.Size >= ctypes.sizeof(_Acl)
                     and dacl.value + header.Size <= end, 23)
            _require(self.security.IsValidAcl(dacl), 23)
            information = _AclSizeInformation()
            _require(self.security.GetAclInformation(
                dacl, ctypes.byref(information), ctypes.sizeof(information), 2
            ), 24)
            _require(header.Count == information.Count == len(principals)
                     and information.BytesInUse + information.BytesFree == header.Size
                     and ctypes.sizeof(_Acl) <= information.BytesInUse <= header.Size, 24)
            cursor = dacl.value + ctypes.sizeof(_Acl)
            acl_end = dacl.value + information.BytesInUse
            seen: set[int] = set()
            for index in range(information.Count):
                entry = _PTR()
                _require(self.security.GetAce(dacl, index, ctypes.byref(entry)), 25)
                _require(entry.value == cursor and cursor + 4 <= acl_end, 25)
                ace = _AceHeader.from_address(cursor)
                _require(ace.Type == 0 and ace.Flags == _INHERIT_CHILDREN
                         and ace.Size >= 16 and ace.Size % 4 == 0
                         and cursor + ace.Size <= acl_end, 25)
                allowed = _AllowedAce.from_address(cursor)
                _require(allowed.Mask == _FULL_CONTROL, 25)
                sid = cursor + _AllowedAce.SidStart.offset
                sid_length = self.sid_length(sid, sid, cursor + ace.Size, 25)
                _require(_AllowedAce.SidStart.offset + sid_length == ace.Size, 25)
                matches = [n for n, principal in enumerate(principals)
                           if self.security.EqualSid(sid, principal)]
                _require(len(matches) == 1 and matches[0] not in seen, 25)
                seen.add(matches[0])
                cursor += ace.Size
            _require(cursor == acl_end and len(seen) == len(principals), 25)
        finally:
            if descriptor.value:
                _require(not self.kernel.LocalFree(descriptor), 29)


def _require_windows() -> None:
    if os.name != "nt":
        raise OSError("Windows storage guard called on a different platform")


@contextmanager
def create_private_windows_directory(path: Path) -> Iterator[None]:
    """Atomically create a new private root; pin parent/root until first bytes finish.

    Pre-existing or raced paths are rejected, never repaired or removed. Failure
    may leave an empty private directory; callers must not delete an unverified
    path. A filesystem that discards the descriptor fails independent readback.
    """
    _require_windows()
    _require("\0" not in str(path), 15)
    api = _NativeSecurity()
    principals = api.principals()
    descriptor_buffers = api.descriptor(principals)
    descriptor = descriptor_buffers[0]
    attributes = _SecurityAttributes(
        ctypes.sizeof(_SecurityAttributes), ctypes.addressof(descriptor), False
    )
    with ExitStack() as lifetime:
        api.pin_parents(path, lifetime)
        _require(api.kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)), 14)
        handle = api.open_directory(path, lifetime)
        api.verify(handle, principals)
        # descriptor_buffers retains the absolute descriptor's borrowed ACL.
        yield


def private_windows_directory(path: Path) -> None:
    """Verify an existing directory's exact private policy; never create or repair."""
    _require_windows()
    _require("\0" not in str(path), 15)
    api = _NativeSecurity()
    principals = api.principals()
    with ExitStack() as lifetime:
        api.pin_parents(path, lifetime)
        handle = api.open_directory(path, lifetime)
        api.verify(handle, principals)


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
