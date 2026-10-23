"""Connection failures retain safe next actions through the real engine wire."""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import AsyncMock, patch

from amplifai_phone.agent import run_admin, run_connect
from pymobiledevice3.exceptions import ConnectionFailedToUsbmuxdError, UserDeniedPairingError
from session import BACKUP_CHECK_MODE, ProtocolError, Session

PRIVATE_DETAIL = "SYNTHETIC-UDID private-device-name C:/private/backup"


def failure_packets(error: Exception, mode: str) -> tuple[int, list[dict]]:
    sink = io.StringIO()
    operation = AsyncMock(side_effect=error)
    if mode == "connect":
        result = run_connect(io.StringIO(), sink, collector=operation)
    else:
        with patch("amplifai_phone.agent.inspect_backup_encryption", operation):
            result = run_admin(mode, sink)
    operation.assert_awaited_once()
    if PRIVATE_DETAIL in sink.getvalue():
        raise AssertionError("Raw device details escaped into the helper wire")
    return result, [json.loads(line) for line in sink.getvalue().splitlines()]


def inspected_session() -> Session:
    state = Session()
    state.begin("inspect")
    state.handle({"kind": "residue", "sessions": []})
    state.exited(0)
    return state


class ConnectionErrorTests(unittest.TestCase):
    def test_connection_failures_keep_the_existing_safe_taxonomy(self):
        cases = (
            (ConnectionFailedToUsbmuxdError(PRIVATE_DETAIL), "phone_connection"),
            (UserDeniedPairingError(PRIVATE_DETAIL), "trust_required"),
            (TimeoutError(PRIVATE_DETAIL), "connection_timeout"),
            (ConnectionError(PRIVATE_DETAIL), "connection_lost"),
            (RuntimeError(PRIVATE_DETAIL), "collection_failed"),
        )
        for error, code in cases:
            for mode in ("connect", BACKUP_CHECK_MODE):
                with self.subTest(error=type(error).__name__, mode=mode):
                    result, packets = failure_packets(error, mode)
                    self.assertEqual(result, 1)
                    self.assertEqual(packets[-1]["code"], code)
                    self.assertEqual(packets[-1]["kind"], "error")
                    self.assertLessEqual(set(packets[-1]), {"kind", "code", "stage"})

    def test_known_failures_offer_next_actions_without_changing_lifecycle(self):
        cases = (
            (ConnectionFailedToUsbmuxdError(PRIVATE_DETAIL), "Apple Devices"),
            (RuntimeError("Connect exactly one trusted iPhone; found 0"), "Apple Devices"),
            (UserDeniedPairingError(PRIVATE_DETAIL), "Trust"),
            (TimeoutError(PRIVATE_DETAIL), "timed out"),
            (ConnectionError(PRIVATE_DETAIL), "interrupted"),
        )
        for error, hint in cases:
            for mode in ("connect", BACKUP_CHECK_MODE):
                with self.subTest(error=type(error).__name__, mode=mode):
                    result, packets = failure_packets(error, mode)
                    state = inspected_session()
                    state.approve(True)
                    state.begin(mode)
                    for packet in packets:
                        state.handle(packet)
                    self.assertTrue(state.running)
                    self.assertFalse(state.can_connect)
                    with self.assertRaises(ProtocolError):
                        state.begin("connect")
                    state.exited(result)
                    message = state.status if mode == "connect" else state.backup_check_status
                    self.assertIn(hint, message)
                    self.assertNotIn(PRIVATE_DETAIL, message)
                    self.assertTrue(state.needs_inspection)
                    self.assertFalse(state.saved_acknowledged)
                    self.assertIsNone(state.checked_backup_setting)
                    self.assertFalse(state.can_connect)
                    if mode == "connect":
                        self.assertIn("cleanup", message)
                    else:
                        self.assertIn("Unknown", message)
                        self.assertIn("No setting was changed", message)

    def test_unknown_errors_do_not_invent_a_device_diagnosis(self):
        for mode in ("connect", BACKUP_CHECK_MODE):
            result, packets = failure_packets(RuntimeError(PRIVATE_DETAIL), mode)
            state = inspected_session()
            state.approve(True)
            state.begin(mode)
            for packet in packets:
                state.handle(packet)
            state.exited(result)
            if mode == "connect":
                self.assertTrue(state.status.startswith("Collection or handoff failed."))
            else:
                self.assertTrue(state.backup_check_status.startswith("Backup setting: Unknown."))
            self.assertNotIn(PRIVATE_DETAIL, state.status + state.backup_check_status)
            self.assertTrue(state.needs_inspection)

    def test_storage_failure_keeps_its_existing_classification(self):
        result, packets = failure_packets(PermissionError(PRIVATE_DETAIL), BACKUP_CHECK_MODE)
        self.assertEqual(result, 1)
        self.assertEqual(packets, [{"kind": "error", "code": "workspace_unavailable"}])

    def test_inspection_errors_are_not_diagnosed_as_phone_connections(self):
        for error in (TimeoutError(PRIVATE_DETAIL), ConnectionError(PRIVATE_DETAIL)):
            sink = io.StringIO()
            with patch("amplifai_phone.agent.abandoned_sessions", side_effect=error):
                self.assertEqual(run_admin("inspect", sink), 1)
            self.assertEqual(json.loads(sink.getvalue()),
                             {"kind": "error", "code": "workspace_unavailable"})

    def test_setting_check_does_not_reuse_an_earlier_failure_hint(self):
        state = inspected_session()
        state.begin(BACKUP_CHECK_MODE)
        state.handle({"kind": "error", "code": "trust_required"})
        state.exited(1)
        self.assertIn("Trust", state.backup_check_status)
        for result, packet in (
            (1, {"kind": "error", "code": "collection_failed"}),
            (0, {"kind": "backup-encryption-status", "backupEncrypted": True}),
            (0, {"kind": "backup-encryption-status", "backupEncrypted": False}),
        ):
            state.begin("inspect")
            state.handle({"kind": "residue", "sessions": []})
            state.exited(0)
            state.begin(BACKUP_CHECK_MODE)
            state.handle(packet)
            state.exited(result)
            self.assertNotIn("Trust", state.backup_check_status)
            self.assertIs(state.checked_backup_setting, packet.get("backupEncrypted"))


if __name__ == "__main__":
    unittest.main()
