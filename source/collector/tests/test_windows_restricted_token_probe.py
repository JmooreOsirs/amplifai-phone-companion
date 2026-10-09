from __future__ import annotations

import ctypes
import io
import json
import struct
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import windows_restricted_token_probe as probe
from test_windows_storage import PTR, NativeBoundary, address, output, sid


class RestrictedBoundary(NativeBoundary):
    def __init__(self):
        super().__init__()
        self.admin = sid(32, 544)
        self.users = sid(32, 545)
        self.impersonating = False
        self.force_admin = False
        self.force_privilege = False
        self.force_wrong_user = False
        self.force_enabled_group = False
        self.restricted_args = None
        self.closed_tokens: list[int] = []

    def OpenThreadToken(self, thread, access, as_self, target):
        if self.impersonating:
            output(target, PTR, 83)
            return 1
        self.error = 1008
        return 0

    def OpenProcessToken(self, process, access, target):
        if access != 10:
            raise AssertionError("probe may only query/duplicate its existing token")
        output(target, PTR, 81)
        return 1

    def CheckTokenMembership(self, token, principal, target):
        if token is not None:
            raise AssertionError(
                "effective thread membership must be independently checked"
            )
        output(target, ctypes.c_int32, not self.impersonating or self.force_admin)
        return 1

    def CreateWellKnownSid(self, kind, domain, buffer, target):
        if kind == 26:
            ctypes.memmove(buffer, self.admin, len(self.admin))
            output(target, ctypes.c_uint32, len(self.admin))
            return 1
        return super().CreateWellKnownSid(kind, domain, buffer, target)

    def LookupPrivilegeValueW(self, system, name, target):
        if system is not None or name != "SeChangeNotifyPrivilege":
            raise AssertionError(
                "only the documented retained traversal privilege is queried"
            )
        ctypes.memmove(target, struct.pack("<Ii", 23, 0), 8)
        return 1

    def GetTokenInformation(self, token, kind, buffer, capacity, target):
        if kind == 1:
            user = self.system if token == 83 and self.force_wrong_user else self.user
            required = ctypes.sizeof(probe.SidAttributes) + len(user)
            if buffer is not None:
                principal = address(buffer) + ctypes.sizeof(probe.SidAttributes)
                ctypes.memmove(principal, user, len(user))
                ctypes.cast(buffer, ctypes.POINTER(probe.SidAttributes))[
                    0
                ].Sid = principal
        elif kind == 2:
            entries = [
                (
                    self.admin,
                    0x19
                    if self.impersonating and not self.force_enabled_group
                    else 0x0F,
                ),
                (self.users, 7),
            ]
            start = probe.TokenGroupsFirst.Groups.offset
            records_end = start + len(entries) * ctypes.sizeof(probe.SidAttributes)
            required = records_end + sum(len(principal) for principal, _ in entries)
            if buffer is not None:
                output(buffer, ctypes.c_uint32, len(entries))
                cursor = address(buffer) + records_end
                for index, (principal, attributes) in enumerate(entries):
                    ctypes.memmove(cursor, principal, len(principal))
                    record = probe.SidAttributes.from_address(
                        address(buffer)
                        + start
                        + index * ctypes.sizeof(probe.SidAttributes)
                    )
                    record.Sid, record.Attributes = cursor, attributes
                    cursor += len(principal)
        elif kind == 3:
            entries = [(23, 2)]
            if not self.impersonating or self.force_privilege:
                entries.append((20, 2))
            raw = struct.pack("<I", len(entries))
            raw += b"".join(
                struct.pack("<IiI", low, 0, attributes) for low, attributes in entries
            )
            required = len(raw)
            if buffer is not None:
                ctypes.memmove(buffer, raw, required)
        else:
            raise AssertionError(f"unexpected token query {kind}")
        output(target, ctypes.c_uint32, required)
        if buffer is None:
            self.error = 122
            return 0
        return int(capacity >= required)

    def CreateRestrictedToken(
        self,
        original,
        flags,
        count,
        disabled,
        delete_count,
        delete_list,
        restrict_count,
        restrict_list,
        target,
    ):
        self.restricted_args = (
            original,
            flags,
            count,
            delete_count,
            delete_list,
            restrict_count,
            restrict_list,
        )
        entries = ctypes.cast(disabled, ctypes.POINTER(probe.SidAttributes))
        self.disabled = [
            ctypes.string_at(entries[i].Sid, self.GetLengthSid(entries[i].Sid))
            for i in range(count)
        ]
        output(target, PTR, 82)
        return 1

    def ImpersonateLoggedOnUser(self, token):
        if token != 82:
            raise AssertionError("only the reduced copy can be impersonated")
        self.impersonating = True
        return 1

    def RevertToSelf(self):
        self.impersonating = False
        return 1

    def CloseHandle(self, handle):
        self.closed_tokens.append(handle)
        return super().CloseHandle(handle)


class RestrictedProbeTest(unittest.TestCase):
    def test_actual_workspace_failure_is_a_numeric_packet_not_a_private_traceback(self):
        native = NativeBoundary()
        native.failure = "CreateFileW"

        @contextmanager
        def synthetic_os_boundary():
            with native.installed():
                yield {}

        output_stream = io.StringIO()
        with (patch.object(probe, "restricted_storage_token", synthetic_os_boundary),
              patch.object(probe, "fixture_owner_matches_user", return_value=None),
              patch("sys.stdout", output_stream)):
            self.assertEqual(probe.main(), 1)
        self.assertEqual(json.loads(output_stream.getvalue()), {
            "kind": "reduced-token-storage", "ready": False,
            "native_stage": 15, "win32_code": 183, "requested_access": 0,
            "api_code": 1, "ancestor_index": 0,
            "old_fixture_owner_matches_user": None,
            "failed_is_fixture_parent": False, "failed_ancestor_index": 0,
        })
        self.assertIsNone(native.created)
        self.assertNotIn("Traceback", output_stream.getvalue())
        self.assertNotIn("amplifai-reduced-storage-", output_stream.getvalue())

    def test_legacy_ancestor_observation_is_numeric_only_and_never_creates_storage(self):
        class AncestorDenied(NativeBoundary):
            def CreateFileW(self, path, access, *arguments):
                self.error = 5
                return probe.storage._INVALID_HANDLE

        native = AncestorDenied()
        path = Path("synthetic-parent/private")
        with native.installed():
            diagnostic = probe.observe_legacy_ancestor_access(path)
        self.assertEqual(diagnostic, {
            "all_readable": False,
            "failed_ancestor_index": 0,
            "native_stage": 15,
            "api_code": 1,
            "win32_code": 5,
            "requested_access": 0x20080,
        })
        self.assertIsNone(native.created)
        self.assertFalse(native.handles)
        self.assertNotIn("synthetic", str(diagnostic))

    def test_legacy_observation_cannot_ignore_reparse_identity_failure(self):
        native = NativeBoundary()
        native.reparse_paths.add(".")
        path = Path("synthetic-parent/private")
        with native.installed(), self.assertRaises(PermissionError):
            probe.observe_legacy_ancestor_access(path)
        self.assertIsNone(native.created)
        self.assertEqual(set(native.closed), set(native.handles))

    def test_reduced_copy_is_verified_from_effective_thread_before_any_storage(self):
        native = RestrictedBoundary()
        with native.installed(), probe.restricted_storage_token() as proof:
            self.assertTrue(native.impersonating)
            self.assertEqual(proof["administrators_enabled_before"], True)
            self.assertEqual(proof["administrators_enabled_during"], False)
            self.assertEqual(proof["disabled_owner_groups"], 1)
            self.assertEqual(proof["enabled_special_privileges"], 0)
            self.assertEqual(native.restricted_args, (81, 1, 1, 0, None, 0, None))
            self.assertEqual(native.disabled, [native.admin])
        self.assertFalse(native.impersonating)
        self.assertEqual(set(native.closed_tokens), {81, 82, 83})
        names = [name for name, _ in native.calls]
        self.assertEqual(names.count("RevertToSelf"), 1)
        self.assertEqual(names.count("CheckTokenMembership"), 2)
        self.assertLess(
            names.index("ImpersonateLoggedOnUser"),
            max(i for i, name in enumerate(names) if name == "OpenThreadToken"),
        )
        effective_reads = native.calls[names.index("ImpersonateLoggedOnUser") + 1 :]
        self.assertTrue(
            [args for name, args in effective_reads if name == "GetTokenInformation"]
        )
        self.assertTrue(
            all(
                args[0] == 83
                for name, args in effective_reads
                if name == "GetTokenInformation"
            )
        )
        self.assertFalse(
            set(names)
            & {
                "LogonUser",
                "CreateProcessAsUserW",
                "AdjustTokenPrivileges",
                "AdjustTokenGroups",
            }
        )

    def test_preexisting_impersonation_is_not_replaced_or_reverted(self):
        native = RestrictedBoundary()
        native.impersonating = True
        with (
            native.installed(),
            self.assertRaises(PermissionError),
            probe.restricted_storage_token(),
        ):
            self.fail("the caller context must not be replaced")
        self.assertTrue(native.impersonating)
        names = [name for name, _ in native.calls]
        self.assertNotIn("CreateRestrictedToken", names)
        self.assertNotIn("RevertToSelf", names)

    def test_admin_authority_or_special_privilege_readback_fails_closed(self):
        for attribute in ("force_admin", "force_privilege"):
            native = RestrictedBoundary()
            setattr(native, attribute, True)
            with (
                native.installed(),
                self.assertRaises(PermissionError),
                probe.restricted_storage_token(),
            ):
                self.fail("no storage may run under unexpected authority")
            self.assertFalse(native.impersonating)
            self.assertEqual(set(native.closed_tokens), {81, 82, 83})

    def test_impersonation_failure_never_falls_back_to_process_authority(self):
        native = RestrictedBoundary()
        native.failure = "ImpersonateLoggedOnUser"
        with (
            native.installed(),
            self.assertRaises(PermissionError) as caught,
            probe.restricted_storage_token(),
        ):
            self.fail("an unsuccessful reduction cannot continue")
        self.assertIsNone(caught.exception.__cause__)
        self.assertFalse(native.impersonating)
        self.assertEqual([name for name, _ in native.calls].count("RevertToSelf"), 1)

    def test_user_and_deny_only_groups_are_checked_even_when_membership_says_nonadmin(
        self,
    ):
        for attribute in ("force_wrong_user", "force_enabled_group"):
            native = RestrictedBoundary()
            setattr(native, attribute, True)
            with (
                native.installed(),
                self.assertRaises(PermissionError),
                probe.restricted_storage_token(),
            ):
                self.fail("independent token readback cannot be skipped")
            self.assertFalse(native.impersonating)
            self.assertEqual(set(native.closed_tokens), {81, 82, 83})

    def test_restoration_failure_terminates_without_continuing_privileged_work(self):
        native = RestrictedBoundary()
        native.failure = "RevertToSelf"
        with (
            native.installed(),
            patch.object(probe.os, "_exit", side_effect=SystemExit(70)) as abort,
            self.assertRaises(SystemExit),
            probe.restricted_storage_token(),
        ):
            pass
        abort.assert_called_once_with(70)

    def test_body_failure_still_restores_and_closes_every_token(self):
        native = RestrictedBoundary()
        with (
            native.installed(),
            self.assertRaisesRegex(ValueError, "synthetic failure"),
            probe.restricted_storage_token(),
        ):
            raise ValueError("synthetic failure")
        self.assertFalse(native.impersonating)
        self.assertEqual(set(native.closed_tokens), {81, 82, 83})


if __name__ == "__main__":
    unittest.main()
