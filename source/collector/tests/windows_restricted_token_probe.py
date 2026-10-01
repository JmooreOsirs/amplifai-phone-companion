"""Finite credential-free, storage-only reduced-token software probe.

This is not a normal-user installer/GUI/phone acceptance test. It restricts a
copy of this test process's own token and impersonates only on the synchronous
test thread. No user, password, logon, privilege adjustment or child process is
created. A failed reduction never falls back to administrator authority.

Microsoft contracts: CreateRestrictedToken, ImpersonateLoggedOnUser,
GetTokenInformation, CheckTokenMembership and RevertToSelf.
"""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from amplifai_phone import windows_storage as storage
from amplifai_phone.workspace import SessionWorkspace

DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32
PTR = ctypes.c_void_p
PPTR = ctypes.POINTER(PTR)
MAX_TOKEN_BYTES = 65536


class SidAttributes(ctypes.Structure):
    _fields_ = [("Sid", PTR), ("Attributes", DWORD)]


class TokenGroupsFirst(ctypes.Structure):
    _fields_ = [("Count", DWORD), ("Groups", SidAttributes * 1)]


class Luid(ctypes.Structure):
    _fields_ = [("Low", DWORD), ("High", ctypes.c_int32)]


class PrivilegeAttributes(ctypes.Structure):
    _fields_ = [("Luid", Luid), ("Attributes", DWORD)]


class TokenPrivilegesFirst(ctypes.Structure):
    _fields_ = [("Count", DWORD), ("Privileges", PrivilegeAttributes * 1)]


def require(condition: object, stage: int) -> None:
    if not condition:
        raise PermissionError(
            f"Reduced-token storage probe failed (stage {stage})"
        ) from None


def token_info(api: storage._NativeSecurity, token: int, kind: int) -> object:
    needed = DWORD()
    result = api.security.GetTokenInformation(
        token, kind, None, 0, ctypes.byref(needed)
    )
    require(not result and ctypes.get_last_error() == 122, 71)
    require(4 <= needed.value <= MAX_TOKEN_BYTES, 71)
    buffer = ctypes.create_string_buffer(needed.value)
    require(
        api.security.GetTokenInformation(
            token, kind, buffer, len(buffer), ctypes.byref(needed)
        ),
        71,
    )
    require(4 <= needed.value <= len(buffer), 71)
    return buffer


def copy_sid(
    api: storage._NativeSecurity, pointer: int, buffer: object, first_byte: int
) -> object:
    base = ctypes.addressof(buffer)
    length = api.sid_length(pointer, base + first_byte, base + len(buffer), 72)
    return ctypes.create_string_buffer(ctypes.string_at(pointer, length), length)


def token_user(api: storage._NativeSecurity, token: int) -> object:
    buffer = token_info(api, token, 1)
    require(len(buffer) >= ctypes.sizeof(SidAttributes) + 8, 72)
    pointer = SidAttributes.from_buffer(buffer).Sid or 0
    return copy_sid(api, pointer, buffer, ctypes.sizeof(SidAttributes))


def token_groups(api: storage._NativeSecurity, token: int) -> list[tuple[object, int]]:
    buffer = token_info(api, token, 2)
    count = DWORD.from_buffer(buffer).value
    offset = TokenGroupsFirst.Groups.offset
    end = offset + count * ctypes.sizeof(SidAttributes)
    require(count <= 1024 and end <= len(buffer), 72)
    groups = []
    for index in range(count):
        entry = SidAttributes.from_buffer(
            buffer, offset + index * ctypes.sizeof(SidAttributes)
        )
        groups.append((copy_sid(api, entry.Sid or 0, buffer, end), entry.Attributes))
    return groups


def special_privileges(
    api: storage._NativeSecurity, token: int, traversal: Luid
) -> int:
    buffer = token_info(api, token, 3)
    count = DWORD.from_buffer(buffer).value
    offset = TokenPrivilegesFirst.Privileges.offset
    require(
        count <= 1024
        and offset + count * ctypes.sizeof(PrivilegeAttributes) <= len(buffer),
        73,
    )
    unexpected = 0
    for index in range(count):
        entry = PrivilegeAttributes.from_buffer(
            buffer, offset + index * ctypes.sizeof(PrivilegeAttributes)
        )
        if entry.Attributes & 2 and (entry.Luid.Low, entry.Luid.High) != (
            traversal.Low,
            traversal.High,
        ):
            unexpected += 1
    return unexpected


def well_known_sid(api: storage._NativeSecurity, kind: int) -> object:
    principal = ctypes.create_string_buffer(68)
    length = DWORD(len(principal))
    require(
        api.security.CreateWellKnownSid(kind, None, principal, ctypes.byref(length)), 72
    )
    require(8 <= length.value <= len(principal), 72)
    base = ctypes.addressof(principal)
    api.sid_length(base, base, base + length.value, 72)
    return principal


def administrators_enabled(api: storage._NativeSecurity, admin: object) -> bool:
    member = BOOL()
    require(api.security.CheckTokenMembership(None, admin, ctypes.byref(member)), 74)
    return bool(member.value)


@contextmanager
def restricted_storage_token() -> Iterator[dict[str, object]]:
    require(os.name == "nt", 70)
    api = storage._NativeSecurity()  # DLLs remain confined to the OS System32.
    storage._bind(
        api.security,
        "CreateRestrictedToken",
        BOOL,
        PTR,
        DWORD,
        DWORD,
        PTR,
        DWORD,
        PTR,
        DWORD,
        PTR,
        PPTR,
    )
    storage._bind(
        api.security, "CheckTokenMembership", BOOL, PTR, PTR, ctypes.POINTER(BOOL)
    )
    storage._bind(
        api.security,
        "LookupPrivilegeValueW",
        BOOL,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.POINTER(Luid),
    )
    storage._bind(api.security, "ImpersonateLoggedOnUser", BOOL, PTR)
    storage._bind(api.security, "RevertToSelf", BOOL)
    # Never replace an unrelated pre-existing impersonation context.
    original_thread = PTR()
    if api.security.OpenThreadToken(
        api.kernel.GetCurrentThread(), 8, True, ctypes.byref(original_thread)
    ):
        api.close(original_thread.value)
        require(False, 75)
    require(ctypes.get_last_error() == 1008, 75)
    with ExitStack() as lifetime:
        original = PTR()
        require(
            api.security.OpenProcessToken(
                api.kernel.GetCurrentProcess(), 10, ctypes.byref(original)
            ),
            76,
        )
        require(original.value not in (None, storage._INVALID_HANDLE), 76)
        lifetime.callback(api.close, original.value)
        owner = token_user(api, original.value)
        require(not api.security.EqualSid(owner, well_known_sid(api, 22)), 76)
        admin = well_known_sid(api, 26)
        before = administrators_enabled(api, admin)
        groups = token_groups(api, original.value)
        disabled = [
            principal
            for principal, attributes in groups
            if not api.security.EqualSid(principal, owner)
            and (attributes & 8 or api.security.EqualSid(principal, admin))
        ]
        require(disabled, 76)
        entries = (SidAttributes * len(disabled))(
            *(SidAttributes(ctypes.addressof(principal), 0) for principal in disabled)
        )
        reduced = PTR()
        # DISABLE_MAX_PRIVILEGE retains only SeChangeNotifyPrivilege; no policy-
        # bypass/LUA guessing, restricting identity, or privilege enablement.
        require(
            api.security.CreateRestrictedToken(
                original.value,
                1,
                len(entries),
                entries,
                0,
                None,
                0,
                None,
                ctypes.byref(reduced),
            ),
            77,
        )
        require(reduced.value not in (None, storage._INVALID_HANDLE), 77)
        lifetime.callback(api.close, reduced.value)
        try:
            require(api.security.ImpersonateLoggedOnUser(reduced.value), 78)
            effective = PTR()
            require(
                api.security.OpenThreadToken(
                    api.kernel.GetCurrentThread(), 8, True, ctypes.byref(effective)
                ),
                79,
            )
            require(effective.value not in (None, storage._INVALID_HANDLE), 79)
            lifetime.callback(api.close, effective.value)
            require(api.security.EqualSid(token_user(api, effective.value), owner), 79)
            during = administrators_enabled(api, admin)
            require(not during, 79)
            actual_groups = token_groups(api, effective.value)
            for principal in disabled:
                matches = [
                    attributes
                    for actual, attributes in actual_groups
                    if api.security.EqualSid(principal, actual)
                ]
                require(
                    len(matches) == 1 and matches[0] & 16 and not matches[0] & 6, 79
                )
            traversal = Luid()
            require(
                api.security.LookupPrivilegeValueW(
                    None, "SeChangeNotifyPrivilege", ctypes.byref(traversal)
                ),
                79,
            )
            unexpected = special_privileges(api, effective.value, traversal)
            require(unexpected == 0, 79)
            yield {
                "administrators_enabled_before": before,
                "administrators_enabled_during": during,
                "disabled_owner_groups": len(disabled),
                "enabled_special_privileges": unexpected,
                "effective_user_matches": True,
            }
        finally:
            # Never continue after an unconfirmed restoration, including when
            # impersonation itself failed. Only this disposable test exits.
            if not api.security.RevertToSelf():
                os._exit(70)


def main() -> None:
    # Storage operations remain on this thread; no worker/subprocess inherits
    # the unrestricted process token. Only synthetic bytes are ever written.
    with tempfile.TemporaryDirectory(prefix="amplifai-reduced-storage-") as parent:
        root = Path(parent) / "sessions"
        with restricted_storage_token() as proof:
            workspace = SessionWorkspace(root)
            with workspace as directory:
                synthetic = directory / "synthetic-source"
                synthetic.write_bytes(b"synthetic-only")
                require(synthetic.read_bytes() == b"synthetic-only", 80)
                storage.private_windows_directory(root)
            require(not directory.exists(), 80)
            storage.private_windows_directory(root)
        print(
            json.dumps(
                {
                    "kind": "reduced-token-storage",
                    "ready": True,
                    "scope": "same-identity synchronous storage only",
                    **proof,
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
