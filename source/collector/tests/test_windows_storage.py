from __future__ import annotations

import ctypes
import ntpath
import os
import struct
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from amplifai_phone import windows_storage as storage

DWORD = ctypes.c_uint32
PTR = ctypes.c_void_p
FULL_CONTROL = 0x001F01FF


def sid(*subauthorities: int) -> bytes:
    return bytes((1, len(subauthorities))) + b"\0\0\0\0\0\5" + struct.pack(
        "<" + "I" * len(subauthorities), *subauthorities
    )


def ace(principal: bytes, *, kind: int = 0, flags: int = 3,
        mask: int = FULL_CONTROL) -> bytes:
    return struct.pack("<BBHI", kind, flags, 8 + len(principal), mask) + principal


def address(value: object) -> int:
    return ctypes.cast(value, PTR).value or 0


def output(target: object, kind: type, value: int) -> None:
    ctypes.cast(target, ctypes.POINTER(kind))[0] = kind(value)


class TokenUser(ctypes.Structure):
    _fields_ = [("Sid", PTR), ("Attributes", DWORD)]


class AbsoluteDescriptor(ctypes.Structure):
    _fields_ = [("Revision", ctypes.c_ubyte), ("Reserved", ctypes.c_ubyte),
                ("Control", ctypes.c_uint16), ("Owner", PTR), ("Group", PTR),
                ("Sacl", PTR), ("Dacl", PTR)]


class SecurityAttributes(ctypes.Structure):
    _fields_ = [("Length", DWORD), ("Descriptor", PTR),
                ("InheritHandle", ctypes.c_int32)]


class NativeFunction:
    def __init__(self, boundary: NativeBoundary, name: str):
        self.boundary, self.name = boundary, name

    def __call__(self, *args: object) -> object:
        self.boundary.calls.append((self.name, args))
        if self.boundary.failure == self.name:
            if self.name == "GetSecurityInfo":
                return 5
            self.boundary.error = 183
            return 0
        return getattr(self.boundary, self.name)(*args)


class NativeLibrary:
    def __init__(self, boundary: NativeBoundary):
        self.boundary = boundary
        self.functions: dict[str, NativeFunction] = {}

    def __getattr__(self, name: str) -> NativeFunction:
        if not hasattr(self.boundary, name):
            raise AttributeError(name)
        return self.functions.setdefault(name, NativeFunction(self.boundary, name))


class NativeBoundary:
    """Synthetic OS boundary; real policy, structure parsing and call order execute."""

    def __init__(self):
        self.user, self.system, self.other = sid(21, 1001), sid(18), sid(32, 544)
        self.owner = self.user
        self.aces = [ace(self.user), ace(self.system)]
        self.control = 0x9004
        self.null_dacl = False
        self.failure = ""
        self.error = 0
        self.thread_error = 1008
        self.token_length = 0
        self.reparse_paths: set[str] = set()
        self.calls: list[tuple[str, tuple]] = []
        self.loads: list[tuple[str, dict]] = []
        self.buffers: list[object] = []
        self.handles: dict[int, str] = {}
        self.closed: list[int] = []
        self.freed: list[int] = []
        self.descriptors: dict[int, tuple[int, int, int]] = {}
        self.created: tuple[bytes, bytes, int, bool] | None = None
        self.create_hook = None
        self.kernel, self.security = NativeLibrary(self), NativeLibrary(self)

    def load(self, name: str, **kwargs: object) -> NativeLibrary:
        self.loads.append((name, kwargs))
        return self.kernel if ntpath.basename(name).lower() == "kernel32.dll" else self.security

    def GetSystemDirectoryW(self, buffer: object, length: int) -> int:
        buffer.value = r"C:\Windows\System32"
        return len(buffer.value)

    def GetCurrentProcess(self) -> int:
        return 11

    def GetCurrentThread(self) -> int:
        return 12

    def OpenThreadToken(self, thread: int, access: int, as_self: bool, out: object) -> int:
        if self.thread_error:
            self.error = self.thread_error
            return 0
        output(out, PTR, 51)
        return 1

    def OpenProcessToken(self, process: int, access: int, out: object) -> int:
        output(out, PTR, 51)
        return 1

    def GetTokenInformation(self, token: int, kind: int, buffer: object,
                            length: int, out: object) -> int:
        required = self.token_length or (ctypes.sizeof(TokenUser) + len(self.user))
        output(out, DWORD, required)
        if buffer is None:
            self.error = 122
            return 0
        principal = address(buffer) + ctypes.sizeof(TokenUser)
        ctypes.memmove(principal, self.user, len(self.user))
        ctypes.cast(buffer, ctypes.POINTER(TokenUser))[0].Sid = principal
        return 1

    def CreateWellKnownSid(self, kind: int, domain: object, buffer: object, out: object) -> int:
        if kind != 22 or domain is not None:
            raise AssertionError("Only LocalSystem is allowed")
        ctypes.memmove(buffer, self.system, len(self.system))
        output(out, DWORD, len(self.system))
        return 1

    def IsValidSid(self, principal: object) -> int:
        return int(ctypes.string_at(principal, 2)[0] == 1)

    def GetLengthSid(self, principal: object) -> int:
        return 8 + 4 * ctypes.string_at(principal, 2)[1]

    def EqualSid(self, left: object, right: object) -> int:
        return int(ctypes.string_at(left, self.GetLengthSid(left)) ==
                   ctypes.string_at(right, self.GetLengthSid(right)))

    def InitializeAcl(self, acl: object, length: int, revision: int) -> int:
        ctypes.memmove(acl, struct.pack("<BBHHH", revision, 0, length, 0, 0), 8)
        return 1

    def AddAccessAllowedAceEx(self, acl: object, revision: int, flags: int,
                              mask: int, principal: object) -> int:
        offset, count = self.acl_usage(acl)
        entry = ace(ctypes.string_at(principal, self.GetLengthSid(principal)),
                    flags=flags, mask=mask)
        ctypes.memmove(address(acl) + offset, entry, len(entry))
        ctypes.memmove(address(acl) + 4, struct.pack("<H", count + 1), 2)
        return 1

    def InitializeSecurityDescriptor(self, descriptor: object, revision: int) -> int:
        ctypes.cast(descriptor, ctypes.POINTER(AbsoluteDescriptor))[0].Revision = revision
        return 1

    def SetSecurityDescriptorOwner(self, descriptor: object, principal: object,
                                    defaulted: bool) -> int:
        ctypes.cast(descriptor, ctypes.POINTER(AbsoluteDescriptor))[0].Owner = address(principal)
        return int(not defaulted)

    def SetSecurityDescriptorDacl(self, descriptor: object, present: bool,
                                  acl: object, defaulted: bool) -> int:
        native = ctypes.cast(descriptor, ctypes.POINTER(AbsoluteDescriptor))[0]
        native.Dacl = address(acl)
        native.Control |= 4 if present else 0
        return int(not defaulted)

    def SetSecurityDescriptorControl(self, descriptor: object, interest: int,
                                     bits: int) -> int:
        native = ctypes.cast(descriptor, ctypes.POINTER(AbsoluteDescriptor))[0]
        native.Control = (native.Control & ~interest) | bits
        return 1

    def CreateDirectoryW(self, path: str, attributes: object) -> int:
        native = ctypes.cast(attributes, ctypes.POINTER(SecurityAttributes))[0]
        descriptor = ctypes.cast(native.Descriptor, ctypes.POINTER(AbsoluteDescriptor))[0]
        length = struct.unpack("<H", ctypes.string_at(descriptor.Dacl + 2, 2))[0]
        self.created = (ctypes.string_at(descriptor.Owner, self.GetLengthSid(descriptor.Owner)),
                        ctypes.string_at(descriptor.Dacl, length), descriptor.Control,
                        bool(native.InheritHandle))
        if native.Length != ctypes.sizeof(SecurityAttributes):
            raise AssertionError("SECURITY_ATTRIBUTES ABI mismatch")
        if self.create_hook:
            self.create_hook()
        return 1

    def CreateFileW(self, path: str, access: int, share: int, attributes: object,
                    disposition: int, flags: int, template: object) -> int:
        handle = 301 + len(self.handles)
        self.handles[handle] = path
        return handle

    def GetFileInformationByHandleEx(self, handle: int, kind: int,
                                      buffer: object, length: int) -> int:
        reparse = self.handles[handle] in self.reparse_paths
        ctypes.memmove(buffer, struct.pack("<II", 0x410 if reparse else 0x10,
                                          0xA0000003 if reparse else 0), 8)
        return 1

    def GetSecurityInfo(self, handle: int, kind: int, information: int,
                        owner: object, group: object, dacl: object, sacl: object,
                        descriptor: object) -> int:
        acl = struct.pack("<BBHHH", 2, 0, 8 + sum(map(len, self.aces)), len(self.aces), 0)
        acl += b"".join(self.aces)
        acl_offset = (20 + len(self.owner) + 3) & ~3
        raw = struct.pack("<BBHIIII", 1, 0, self.control, 20, 0, 0,
                          0 if self.null_dacl else acl_offset)
        raw += self.owner + b"\0" * (acl_offset - 20 - len(self.owner)) + acl
        buffer = ctypes.create_string_buffer(raw)
        self.buffers.append(buffer)
        base = ctypes.addressof(buffer)
        dacl_address = 0 if self.null_dacl else base + acl_offset
        self.descriptors[base] = (base + 20, dacl_address, len(raw))
        output(owner, PTR, base + 20)
        output(dacl, PTR, dacl_address)
        output(descriptor, PTR, base)
        return 0

    def IsValidSecurityDescriptor(self, descriptor: object) -> int:
        return 1

    def GetSecurityDescriptorLength(self, descriptor: object) -> int:
        return self.descriptors[address(descriptor)][2]

    def GetSecurityDescriptorControl(self, descriptor: object, control: object,
                                      revision: object) -> int:
        output(control, ctypes.c_uint16, self.control)
        output(revision, DWORD, 1)
        return 1

    def GetSecurityDescriptorDacl(self, descriptor: object, present: object,
                                 dacl: object, defaulted: object) -> int:
        output(present, ctypes.c_int32, bool(self.control & 4))
        output(defaulted, ctypes.c_int32, bool(self.control & 8))
        output(dacl, PTR, self.descriptors[address(descriptor)][1])
        return 1

    def IsValidAcl(self, acl: object) -> int:
        return 1

    def acl_usage(self, acl: object) -> tuple[int, int]:
        count = struct.unpack("<H", ctypes.string_at(address(acl) + 4, 2))[0]
        offset = 8
        for _ in range(count):
            offset += struct.unpack("<H", ctypes.string_at(address(acl) + offset + 2, 2))[0]
        return offset, count

    def GetAclInformation(self, acl: object, buffer: object, length: int, kind: int) -> int:
        used, count = self.acl_usage(acl)
        size = struct.unpack("<H", ctypes.string_at(address(acl) + 2, 2))[0]
        ctypes.memmove(buffer, struct.pack("<III", count, used, size - used), 12)
        return 1

    def GetAce(self, acl: object, index: int, out: object) -> int:
        offset = 8
        for _ in range(index):
            offset += struct.unpack("<H", ctypes.string_at(address(acl) + offset + 2, 2))[0]
        output(out, PTR, address(acl) + offset)
        return 1

    def LocalFree(self, descriptor: object) -> None:
        self.freed.append(address(descriptor))

    def CloseHandle(self, handle: int) -> int:
        self.closed.append(handle)
        return 1

    @contextmanager
    def installed(self):
        with (patch.object(storage.os, "name", "nt"),
              patch.object(storage.ctypes, "WinDLL", side_effect=self.load, create=True),
              patch.object(storage.ctypes, "get_last_error", side_effect=lambda: self.error, create=True),
              patch("subprocess.run", side_effect=AssertionError("PowerShell is not an ACL provider"))):
            yield


class WindowsNativePolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path("synthetic-parent") / "synthetic-private"

    def test_new_root_is_created_with_private_descriptor_then_independently_read(self) -> None:
        native = NativeBoundary()
        with native.installed(), storage.create_private_windows_directory(self.path):
            self.assertIsNotNone(native.created)
            owner, dacl, control, inherit = native.created
            self.assertEqual(owner, native.user)
            self.assertEqual(dacl, struct.pack("<BBHHH", 2, 0, 8 + sum(map(len, native.aces)), 2, 0) + b"".join(native.aces))
            self.assertEqual(control, 0x1004)
            self.assertFalse(inherit)
            self.assertEqual(native.closed, [51], "directory pins remain open through marker write")
        self.assertEqual(set(native.closed), {51, *native.handles})
        self.assertEqual(len(native.freed), 1)
        names = [name for name, _ in native.calls]
        self.assertLess(names.index("CreateDirectoryW"), names.index("GetSecurityInfo"))
        self.assertEqual(native.loads[0], ("kernel32.dll", {"use_last_error": True, "winmode": 0x800}))
        self.assertEqual(native.loads[1][0], r"C:\Windows\System32\advapi32.dll")
        for name, args in native.calls:
            if name == "CreateFileW":
                self.assertEqual(args[1], 0x20080 if args[0] == str(self.path) else 0x80)
                self.assertEqual(args[2], 3, "no share-delete or privilege-granting access")
                self.assertEqual(args[4], 3)
                self.assertEqual(args[5], 0x02200000)
            if name == "GetSecurityInfo":
                self.assertEqual(args[1:3], (1, 5))

    def test_ancestor_pinning_does_not_require_reading_ancestor_acls(self) -> None:
        target = str(self.path)

        class MetadataAncestorBoundary(NativeBoundary):
            def CreateFileW(self, path, access, *arguments):
                if path != target and access & 0x20000:
                    self.error = 5
                    return storage._INVALID_HANDLE
                return super().CreateFileW(path, access, *arguments)

        native = MetadataAncestorBoundary()
        with native.installed(), storage.create_private_windows_directory(self.path):
            self.assertIsNotNone(native.created)
            self.assertEqual(len(native.freed), 1, "root ACL readback still completed")
            self.assertEqual(native.closed, [51], "all directory identities remain pinned")
        self.assertEqual(set(native.closed), {51, *native.handles})
        root = [args for name, args in native.calls
                if name == "CreateFileW" and args[0] == target]
        self.assertEqual(root[0][1], 0x20080, "root must retain READ_CONTROL")

    def test_handle_failures_preserve_only_the_immediate_numeric_os_diagnostic(self) -> None:
        for failure in ("CreateFileW", "GetFileInformationByHandleEx"):
            native = NativeBoundary()
            native.failure = failure
            with (native.installed(), self.assertRaises(PermissionError) as caught,
                  storage.create_private_windows_directory(self.path)):
                self.fail("OS handle failure cannot allow marker/data")
            error = caught.exception
            self.assertEqual(getattr(error, "win32_code", None), 183)
            self.assertEqual(getattr(error, "native_stage", None), 15)
            self.assertEqual(getattr(error, "api_code", None),
                             1 if failure == "CreateFileW" else 2)
            self.assertEqual(getattr(error, "requested_access", None), 0x80)
            self.assertEqual(getattr(error, "ancestor", None), True)
            self.assertNotIn(str(self.path), str(error))
            self.assertNotIn("1001", str(error))
            self.assertIsNone(error.__cause__)
            self.assertEqual(set(native.closed), {51, *native.handles})

    def test_verify_never_creates_or_repairs_an_existing_directory(self) -> None:
        native = NativeBoundary()
        with native.installed():
            storage.private_windows_directory(self.path)
        names = [name for name, _ in native.calls]
        self.assertNotIn("CreateDirectoryW", names)
        self.assertNotIn("InitializeAcl", names)
        self.assertEqual(len(native.freed), 1)
        self.assertEqual(set(native.closed), {51, *native.handles})

    def test_every_owner_dacl_and_ace_requirement_is_enforced_without_repair(self) -> None:
        cases = {
            "wrong owner": lambda n: setattr(n, "owner", n.other),
            "unprotected": lambda n: setattr(n, "control", 0x8004),
            "absent DACL": lambda n: setattr(n, "control", 0x9000),
            "defaulted DACL": lambda n: setattr(n, "control", 0x900C),
            "NULL DACL": lambda n: setattr(n, "null_dacl", True),
            "extra principal": lambda n: n.aces.append(ace(n.other)),
            "missing SYSTEM": lambda n: setattr(n, "aces", [ace(n.user)]),
            "duplicate owner": lambda n: setattr(n, "aces", [ace(n.user), ace(n.user)]),
            "inherited": lambda n: setattr(n, "aces", [ace(n.user, flags=19), ace(n.system)]),
            "inherit only": lambda n: setattr(n, "aces", [ace(n.user, flags=11), ace(n.system)]),
            "no child inheritance": lambda n: setattr(n, "aces", [ace(n.user, flags=0), ace(n.system)]),
            "deny ACE": lambda n: setattr(n, "aces", [ace(n.user, kind=1), ace(n.system)]),
            "callback ACE": lambda n: setattr(n, "aces", [ace(n.user, kind=9), ace(n.system)]),
            "partial control": lambda n: setattr(n, "aces", [ace(n.user, mask=0x120089), ace(n.system)]),
        }
        for name, mutate in cases.items():
            for create in (False, True):
                with self.subTest(name=name, create=create):
                    native = NativeBoundary()
                    mutate(native)
                    with native.installed(), self.assertRaises(PermissionError):
                        if create:
                            with storage.create_private_windows_directory(self.path):
                                self.fail("unsafe readback must not permit marker/data writes")
                        else:
                            storage.private_windows_directory(self.path)
                    self.assertEqual(len(native.freed), 1)
                    self.assertEqual(set(native.closed), {51, *native.handles})

    def test_creation_race_and_reparse_substitution_fail_without_marker_or_deletion(self) -> None:
        for failure, reparse in (("CreateDirectoryW", ""), ("", str(self.path)),
                                 ("", str(self.path.parent))):
            with self.subTest(failure=failure, reparse=reparse):
                native = NativeBoundary()
                native.failure = failure
                native.reparse_paths.add(reparse)
                with (native.installed(), self.assertRaises(PermissionError),
                      storage.create_private_windows_directory(self.path)):
                    self.fail("a raced root must not be admitted")
                self.assertEqual(set(native.closed), {51, *native.handles})
                if reparse == str(self.path.parent):
                    self.assertIsNone(native.created)

    def test_effective_token_is_queried_and_other_thread_errors_do_not_fallback(self) -> None:
        for thread_error in (0, 1008, 5):
            with self.subTest(thread_error=thread_error):
                native = NativeBoundary()
                native.thread_error = thread_error
                with native.installed():
                    if thread_error == 5:
                        with self.assertRaises(PermissionError):
                            storage.private_windows_directory(self.path)
                    else:
                        storage.private_windows_directory(self.path)
                calls = dict(native.calls)
                self.assertEqual("OpenProcessToken" in calls, thread_error == 1008)
                self.assertEqual(calls["OpenThreadToken"][1:3], (8, True))

    def test_api_failures_are_sanitized_and_resources_are_released(self) -> None:
        for failure in ("CreateWellKnownSid", "InitializeAcl", "AddAccessAllowedAceEx",
                        "SetSecurityDescriptorControl", "CreateFileW",
                        "GetFileInformationByHandleEx", "GetSecurityInfo",
                        "GetSecurityDescriptorControl", "GetAclInformation", "GetAce"):
            with self.subTest(failure=failure):
                native = NativeBoundary()
                native.failure = failure
                with (native.installed(), self.assertRaisesRegex(PermissionError, r"native policy stage \d+\)$") as caught,
                      storage.create_private_windows_directory(self.path)):
                    self.fail("failed OS call must fail closed")
                self.assertNotIn(str(self.path), str(caught.exception))
                self.assertNotIn("1001", str(caught.exception))
                self.assertIsNone(caught.exception.__cause__)
                self.assertEqual(set(native.closed), {51, *native.handles})
                self.assertEqual(len(native.freed), len(native.descriptors))

    def test_oversize_token_and_raw_loader_error_fail_closed_without_identifiers(self) -> None:
        native = NativeBoundary()
        native.token_length = 8192
        with native.installed(), self.assertRaises(PermissionError):
            storage.private_windows_directory(self.path)
        self.assertEqual(native.closed, [51])
        self.assertFalse(native.handles)
        with (native.installed(),
              patch.object(storage.ctypes, "WinDLL", side_effect=OSError("raw SID/private-path")),
              self.assertRaisesRegex(PermissionError, r"native policy stage \d+\)$") as caught):
            storage.private_windows_directory(self.path)
        self.assertNotIn("raw SID/private-path", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_nul_path_cannot_truncate_the_target_at_the_native_boundary(self) -> None:
        path = Path("synthetic-parent") / "private\0substitution"
        for create in (False, True):
            with self.subTest(create=create):
                native = NativeBoundary()
                with native.installed(), self.assertRaises(PermissionError):
                    if create:
                        with storage.create_private_windows_directory(path):
                            self.fail("NUL cannot identify a different directory")
                    else:
                        storage.private_windows_directory(path)
                self.assertFalse(native.handles)
                self.assertIsNone(native.created)

    def test_reparse_ancestor_is_rejected_before_creating_a_root(self) -> None:
        native = NativeBoundary()
        native.reparse_paths.add(str(self.path.parent.parent))
        with (native.installed(), self.assertRaises(PermissionError),
              storage.create_private_windows_directory(self.path)):
            self.fail("creation must not traverse a substituted ancestor")
        self.assertIsNone(native.created)
        self.assertEqual(set(native.closed), {51, *native.handles})


@unittest.skipUnless(os.name == "nt", "actual Windows security APIs required")
class WindowsStorageTest(unittest.TestCase):
    def test_atomic_private_creation_and_inherited_existing_directory_rejection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-win-acl-") as parent:
            inherited = Path(parent) / "inherited"
            inherited.mkdir()
            with self.assertRaises(PermissionError):
                storage.private_windows_directory(inherited)
            with (self.assertRaises(PermissionError),
                  storage.create_private_windows_directory(inherited)):
                self.fail("existing directories cannot be claimed")
            private = Path(parent) / "private"
            with storage.create_private_windows_directory(private):
                child = private / "synthetic-source"
                child.write_bytes(b"synthetic-only")
            storage.private_windows_directory(private)
            self.assertEqual(child.read_bytes(), b"synthetic-only")

    def test_process_probe_keeps_live_process_and_recognizes_exited_process(self) -> None:
        self.assertTrue(storage.windows_process_alive(os.getpid()))
        process = subprocess.Popen(["cmd.exe", "/c", "exit", "0"])
        process.wait(timeout=10)
        self.assertFalse(storage.windows_process_alive(process.pid))


if __name__ == "__main__":
    unittest.main()
