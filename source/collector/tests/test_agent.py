from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from amplifai_phone.agent import MAX_COMMAND_BYTES, _command, run_admin, run_connect
from amplifai_phone.backup_stream import DeviceBackupRejected
from amplifai_phone.backup_watchdog import (
    BackupCleanupIncomplete,
    BackupNoFileProgress,
    BackupStalled,
    BackupTimeLimit,
)
from amplifai_phone.ios_backup import IPhoneCapture
from amplifai_phone.metadata import (
    BackupControlFrameLimit,
    BackupControlInvalid,
    BackupControlMetadataLimit,
    BackupControlPathLimit,
    Contact,
    ContactsCapacityLimit,
    ContactsSchemaUnsupported,
    Interaction,
    SelectedPayloadIntegrityError,
    SelectedPayloadMissing,
    SourceCapacityLimit,
    SourceResult,
    UnsupportedSchema,
)
from amplifai_phone.record_store import RecordStore
from amplifai_phone.workspace import SessionWorkspace, WorkspaceError
from pymobiledevice3.exceptions import (
    ConnectionTerminatedError,
    NotEnoughDiskSpaceError,
)


def synthetic_capture() -> IPhoneCapture:
    return IPhoneCapture(
        SourceResult(1, (Contact(1, "Ada Synthetic", ("+15551234567",), ()),), 0),
        SourceResult(
            1,
            (
                Interaction(
                    1,
                    "call",
                    "call",
                    "2026-09-01T12:00:00+00:00",
                    "outgoing",
                    ("+15551234567",),
                    60,
                ),
            ),
            0,
        ),
        SourceResult(
            1,
            (
                Interaction(
                    1,
                    "message",
                    "sms",
                    "2026-09-01T13:00:00+00:00",
                    "incoming",
                    ("+15551234567",),
                    None,
                ),
            ),
            0,
        ),
        "2026-09-23T00:00:00+00:00",
        "2026-03-23T00:00:00+00:00",
        (),
    )


class AgentProtocolTest(unittest.TestCase):
    def test_processing_failures_have_distinct_safe_categories_without_private_details(self) -> None:
        cases = (
            (BackupControlInvalid, "backup_control_invalid"),
            (SelectedPayloadMissing, "selected_payload_missing"),
            (SelectedPayloadIntegrityError, "selected_payload_invalid"),
            (ContactsSchemaUnsupported, "contacts_schema"),
            (UnsupportedSchema, "unsupported_schema"),
        )
        for failure_type, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                async def failed(**kwargs):
                    kwargs["transfer_callback"]({"stage": "processing", "processedBytes": 0})
                    raise failure_type("private /Users/tester/phone-backup/sms.db")

                output = io.StringIO()
                self.assertEqual(run_connect(io.StringIO(), output, failed), 1)
                self.assertEqual(
                    json.loads(output.getvalue().splitlines()[-1]),
                    {"kind": "error", "code": expected_code, "stage": "processing"},
                )
                self.assertNotIn("/Users/tester", output.getvalue())

    def test_device_host_space_refusal_is_specific_and_contains_no_private_detail(self) -> None:
        async def failed_collector(**_kwargs: object) -> IPhoneCapture:
            raise NotEnoughDiskSpaceError("private phone owner detail")

        sink = io.StringIO()
        self.assertEqual(run_connect(io.StringIO(), sink, failed_collector), 1)
        self.assertEqual(
            json.loads(sink.getvalue().splitlines()[-1]),
            {"kind": "error", "code": "backup_host_space", "stage": "connecting"},
        )
        self.assertNotIn("private", sink.getvalue())

    def test_metadata_capacity_error_is_distinct_and_never_emits_private_details(self) -> None:
        async def failed(**_kwargs):
            raise SourceCapacityLimit("private /Users/tester/source count detail")

        output = io.StringIO()
        self.assertEqual(run_connect(io.StringIO(), output, failed), 1)
        self.assertEqual(json.loads(output.getvalue().splitlines()[-1]), {"kind": "error", "code": "source_capacity_limit", "stage": "connecting"})
        self.assertNotIn("/Users/tester", output.getvalue())

    def test_capacity_subtypes_preserve_safe_stage_without_private_details(self) -> None:
        cases = (
            (BackupControlFrameLimit, "backup_control_frame_limit", "backup"),
            (BackupControlPathLimit, "backup_control_path_limit", "backup"),
            (BackupControlMetadataLimit, "backup_control_metadata_limit", "processing"),
            (ContactsCapacityLimit, "contacts_capacity_limit", "processing"),
        )
        for failure_type, expected_code, stage in cases:
            with self.subTest(code=expected_code):
                async def failed(**kwargs):
                    kwargs["transfer_callback"]({"stage": stage, "processedBytes": 0})
                    raise failure_type("private /Users/tester/source detail")

                output = io.StringIO()
                self.assertEqual(run_connect(io.StringIO(), output, failed), 1)
                self.assertEqual(
                    json.loads(output.getvalue().splitlines()[-1]),
                    {"kind": "error", "code": expected_code, "stage": stage},
                )
                self.assertNotIn("/Users/tester", output.getvalue())

    def test_byte_progress_advances_independently_of_flat_backup_percentage(self) -> None:
        async def capture(**kwargs):
            kwargs["progress_callback"](7)
            for count in (1024**3, 2 * 1024**3):
                kwargs["transfer_callback"]({"stage": "backup", "receivedBytes": count,
                                             "retainedBytes": count // 2, "discardedBytes": count // 2,
                                             "filesReceived": 9, "elapsedSeconds": 10, "bytesPerSecond": 1000})
            return synthetic_capture()

        output = io.StringIO()
        self.assertEqual(run_connect(io.StringIO('{"action":"disconnect"}\n'), output, capture), 0)
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([event["receivedBytes"] for event in events if event["kind"] == "transfer"], [1024**3, 2 * 1024**3])
        self.assertEqual(len([event for event in events if event["kind"] == "progress"]), 1)

    def test_storage_reasons_survive_connect_and_admin_without_private_details(
        self,
    ) -> None:
        for code in (
            "workspace_low_space",
            "workspace_size_limit",
            "workspace_unsafe",
            "workspace_unavailable",
        ):
            with self.subTest(code=code):
                error = WorkspaceError(
                    "private path /Users/tester/private-backup must not be emitted"
                )
                error.code = code

                async def failed_collector(
                    _failure: BaseException = error, **_kwargs: object
                ) -> IPhoneCapture:
                    raise _failure

                sink = io.StringIO()
                self.assertEqual(run_connect(io.StringIO(), sink, failed_collector), 1)
                self.assertEqual(
                    json.loads(sink.getvalue().splitlines()[-1]),
                    {"kind": "error", "code": code},
                )
                self.assertNotIn("private-backup", sink.getvalue())
                admin = io.StringIO()
                with patch(
                    "amplifai_phone.agent.abandoned_sessions", side_effect=error
                ):
                    self.assertEqual(run_admin("inspect", admin), 1)
                self.assertEqual(
                    json.loads(admin.getvalue()), {"kind": "error", "code": code}
                )

    def test_connection_timeout_does_not_claim_a_one_hour_backup(self) -> None:
        async def failed_collector(**_kwargs: object) -> IPhoneCapture:
            raise TimeoutError("private connection detail")

        sink = io.StringIO()
        self.assertEqual(run_connect(io.StringIO(), sink, failed_collector), 1)
        self.assertEqual(
            json.loads(sink.getvalue().splitlines()[-1]),
            {"kind": "error", "code": "connection_timeout"},
        )
        self.assertNotIn("private connection detail", sink.getvalue())

    def test_device_link_disconnect_is_not_a_generic_collection_failure(self) -> None:
        async def failed_collector(**_kwargs: object) -> IPhoneCapture:
            raise ConnectionTerminatedError("private transport detail")

        sink = io.StringIO()
        self.assertEqual(run_connect(io.StringIO(), sink, failed_collector), 1)
        self.assertEqual(
            json.loads(sink.getvalue().splitlines()[-1]),
            {"kind": "error", "code": "connection_lost", "stage": "connecting"},
        )
        self.assertNotIn("private transport detail", sink.getvalue())

    def test_device_rejection_preserves_only_safe_status_and_backup_stage(self) -> None:
        async def failed_collector(**kwargs: object) -> IPhoneCapture:
            kwargs["transfer_callback"]({
                "stage": "backup", "receivedBytes": 1024, "retainedBytes": 0,
                "discardedBytes": 1024, "filesReceived": 1,
                "elapsedSeconds": 1800, "bytesPerSecond": 0.5,
            })
            raise DeviceBackupRejected(205)

        sink = io.StringIO()
        self.assertEqual(run_connect(io.StringIO(), sink, failed_collector), 1)
        self.assertEqual(
            json.loads(sink.getvalue().splitlines()[-1]),
            {"kind": "error", "code": "device_backup_failed", "stage": "backup", "deviceCode": 205},
        )

    def test_incomplete_cleanup_keeps_transport_reason_and_review_gate(self) -> None:
        async def failed_collector(**_kwargs: object) -> IPhoneCapture:
            raise BackupCleanupIncomplete("connection_lost")

        sink = io.StringIO()
        self.assertEqual(run_connect(io.StringIO(), sink, failed_collector), 1)
        self.assertEqual(
            json.loads(sink.getvalue().splitlines()[-1]),
            {"kind": "error", "code": "connection_lost", "stage": "connecting", "cleanupRequired": True},
        )

    def test_incomplete_cleanup_keeps_first_numeric_device_status(self) -> None:
        async def failed_collector(**_kwargs: object) -> IPhoneCapture:
            try:
                raise DeviceBackupRejected(205)
            except DeviceBackupRejected as first:
                raise BackupCleanupIncomplete("device_backup_failed") from first

        sink = io.StringIO()
        self.assertEqual(run_connect(io.StringIO(), sink, failed_collector), 1)
        self.assertEqual(
            json.loads(sink.getvalue().splitlines()[-1]),
            {"kind": "error", "code": "device_backup_failed", "stage": "connecting", "deviceCode": 205, "cleanupRequired": True},
        )

    def test_cleanup_failure_keeps_the_primary_reason_and_requires_recovery(
        self,
    ) -> None:
        primary = TimeoutError("private connection detail")
        cleanup = WorkspaceError("private cleanup path")
        cleanup.code = "workspace_cleanup"
        cleanup.primary_error = primary

        async def failed_collector(**_kwargs: object) -> IPhoneCapture:
            raise cleanup

        sink = io.StringIO()
        self.assertEqual(run_connect(io.StringIO(), sink, failed_collector), 1)
        self.assertEqual(
            json.loads(sink.getvalue().splitlines()[-1]),
            {"kind": "error", "code": "connection_timeout", "cleanupRequired": True},
        )
        self.assertNotIn("private", sink.getvalue())

    def test_backup_deadlines_and_incomplete_cleanup_keep_specific_recovery(
        self,
    ) -> None:
        cases = [
            (BackupStalled(), {"kind": "error", "code": "backup_stalled"}),
            (BackupTimeLimit(), {"kind": "error", "code": "backup_time_limit"}),
            (BackupNoFileProgress(), {"kind": "error", "code": "backup_no_file_progress"}),
            (
                BackupCleanupIncomplete("backup_stalled"),
                {"kind": "error", "code": "backup_stalled", "cleanupRequired": True},
            ),
            (
                BackupCleanupIncomplete("cancelled"),
                {"kind": "error", "code": "cancelled", "cleanupRequired": True},
            ),
        ]
        for error, expected in cases:
            with self.subTest(error=type(error).__name__, code=expected["code"]):

                async def failed_collector(
                    _failure: BaseException = error, **_kwargs: object
                ) -> IPhoneCapture:
                    raise _failure

                sink = io.StringIO()
                self.assertEqual(run_connect(io.StringIO(), sink, failed_collector), 1)
                self.assertEqual(json.loads(sink.getvalue().splitlines()[-1]), expected)

    def test_admin_filesystem_error_is_private_and_recoverable(self) -> None:
        sink = io.StringIO()
        with patch(
            "amplifai_phone.agent.abandoned_sessions",
            side_effect=OSError("private path"),
        ):
            self.assertEqual(run_admin("inspect", sink), 1)
        self.assertEqual(
            json.loads(sink.getvalue()),
            {"kind": "error", "code": "workspace_unavailable"},
        )
        self.assertNotIn("private", sink.getvalue())

    def test_runtime_check_imports_capture_dependencies_without_device(self) -> None:
        sink = io.StringIO()
        self.assertEqual(run_admin("runtime-check", sink), 0)
        self.assertEqual(
            json.loads(sink.getvalue()), {"kind": "runtime", "ready": True}
        )

    def test_synthetic_process_protocol_requires_selection(self) -> None:
        async def fake_collector(**kwargs: object) -> IPhoneCapture:
            kwargs["progress_callback"](42)
            return synthetic_capture()

        review_id = "00000000-0000-4000-8000-000000000001"
        source = io.StringIO(
            json.dumps({"action": "review", "ids": [1], "reviewId": review_id})
            + '\n{"action":"disconnect"}\n'
        )
        sink = io.StringIO()
        self.assertEqual(run_connect(source, sink, fake_collector), 0)
        events = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(
            [item["kind"] for item in events],
            ["state", "progress", "capture", "review", "state"],
        )
        self.assertEqual(events[3]["matched_calls"], 1)
        self.assertEqual(events[3]["reviewId"], review_id)
        self.assertEqual(events[3]["matched_messages"], 1)
        self.assertEqual(
            events[3]["observed_call_earliest"], "2026-09-01T12:00:00+00:00"
        )
        self.assertEqual(
            events[3]["observed_message_latest"], "2026-09-01T13:00:00+00:00"
        )
        self.assertEqual(events[2]["contacts"][0]["phoneEnds"], ["4567"])
        self.assertNotIn("+15551234567", sink.getvalue())

    def test_bridge_close_failure_still_closes_private_capture(self) -> None:
        async def fake_collector(**_kwargs: object) -> IPhoneCapture:
            return synthetic_capture()

        bridge = MagicMock()
        bridge.close.side_effect = OSError("handoff unlink failed")
        bridge.code = "0000000000"
        bridge.port = 48751
        bridge.handoff_id = "00000000-0000-4000-8000-000000000001"
        bridge.payload_sha256 = "0" * 64
        source = io.StringIO('{"action":"review","ids":[1]}\n{"action":"pair"}\n{"action":"disconnect"}\n')
        with (patch("amplifai_phone.agent.BridgeServer", return_value=bridge),
              patch.object(IPhoneCapture, "close") as capture_close,
              self.assertRaises(OSError)):
            run_connect(source, io.StringIO(), fake_collector)
        capture_close.assert_called_once()

    def test_paged_selection_binds_exact_ids_without_one_shot_review(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-selection-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            directory = workspace.__enter__()
            store = RecordStore(directory / "sanitized.sqlite3")
            for source_id in (1, 2):
                store.add("contacts", Contact(source_id, f"Person {source_id}",
                                              (f"+1555123456{source_id}",), ()))
            store.add("calls", Interaction(9, "call", "call", "2026-09-01T00:00:00Z",
                                            "incoming", ("+15551234561",), 20))

            async def fake_collector(**_kwargs: object) -> IPhoneCapture:
                return IPhoneCapture(SourceResult(2, store.collection("contacts"), 0),
                                     SourceResult(1, store.collection("calls"), 0),
                                     SourceResult(0, store.collection("messages"), 0),
                                     "2026-09-23T00:00:00Z", "2026-03-23T00:00:00Z", (),
                                     workspace, store)

            review_id = "00000000-0000-4000-8000-000000000001"
            commands = "".join(json.dumps(command) + "\n" for command in (
                {"action": "review-page", "reviewId": review_id, "cursor": 0, "total": 2, "ids": [1]},
                {"action": "review-page", "reviewId": review_id, "cursor": 1, "total": 2, "ids": [2]},
                {"action": "disconnect"},
            ))
            sink = io.StringIO()
            self.assertEqual(run_connect(io.StringIO(commands), sink, fake_collector), 0)
            events = [json.loads(line) for line in sink.getvalue().splitlines()]
            self.assertEqual([event["kind"] for event in events],
                             ["state", "capture", "review-page", "review-page", "review", "state"])
            self.assertEqual(events[4]["selected_contacts"], 2)
            self.assertEqual(events[4]["matched_calls"], 1)
            self.assertEqual(events[4]["selection_sha256"], hashlib.sha256(b"1\n2\n").hexdigest())
            self.assertFalse(directory.exists())

    def test_review_rejects_invalid_binding_without_pairing(self) -> None:
        async def fake_collector(**_kwargs: object) -> IPhoneCapture:
            return synthetic_capture()

        source = io.StringIO(
            '{"action":"review","ids":[1],"reviewId":"wrong"}\n'
            '{"action":"pair"}\n'
            '{"action":"disconnect"}\n'
        )
        sink = io.StringIO()
        self.assertEqual(run_connect(source, sink, fake_collector), 0)
        events = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(
            [item["kind"] for item in events],
            ["state", "capture", "error", "error", "state"],
        )
        self.assertTrue(all(item.get("code") == "selection" for item in events[2:4]))

    def test_contact_preview_pages_preserve_search_and_selection(self) -> None:
        contacts = tuple(
            Contact(index, f"Person {index:04d} " + "x" * 200, ("+15551234567",), ())
            for index in range(1, 451)
        )

        async def fake_collector(**_kwargs: object) -> IPhoneCapture:
            return IPhoneCapture(
                SourceResult(450, contacts, 0),
                SourceResult(0, (), 0),
                SourceResult(0, (), 0),
                "2026-10-08T00:00:00+00:00",
                "2001-01-01T00:00:00+00:00",
                (),
            )

        source = io.StringIO(
            '{"action":"contacts","query":"","cursor":200}\n'
            '{"action":"contacts","query":"","cursor":400}\n'
            '{"action":"contacts","query":"Person 0449","cursor":0}\n'
            '{"action":"review","ids":[449]}\n'
            '{"action":"disconnect"}\n'
        )
        sink = io.StringIO()
        self.assertEqual(run_connect(source, sink, fake_collector), 0)
        lines = sink.getvalue().splitlines()
        events = [json.loads(line) for line in lines]
        self.assertEqual(
            [event["kind"] for event in events],
            ["state", "capture", "contacts", "contacts", "contacts", "review", "state"],
        )
        self.assertEqual(
            (
                events[1]["totalContacts"],
                len(events[1]["contacts"]),
                events[1]["nextCursor"],
            ),
            (450, 200, 200),
        )
        self.assertEqual(
            (events[2]["cursor"], len(events[2]["contacts"]), events[2]["nextCursor"]),
            (200, 200, 400),
        )
        self.assertEqual(
            (events[3]["cursor"], len(events[3]["contacts"]), events[3]["nextCursor"]),
            (400, 50, None),
        )
        self.assertEqual([item["id"] for item in events[4]["contacts"]], [449])
        self.assertEqual(events[5]["selected_contacts"], 1)
        self.assertLess(max(len(line.encode("utf-8")) for line in lines), 128 * 1024)
        self.assertNotIn("+15551234567", sink.getvalue())

    def test_invalid_contact_page_request_fails_closed_without_private_query(
        self,
    ) -> None:
        async def fake_collector(**_kwargs: object) -> IPhoneCapture:
            return synthetic_capture()

        source = io.StringIO(
            '{"action":"contacts","query":"private person","cursor":true}\n'
        )
        sink = io.StringIO()
        self.assertEqual(run_connect(source, sink, fake_collector), 1)
        self.assertEqual(
            json.loads(sink.getvalue().splitlines()[-1]),
            {"kind": "error", "code": "collection_failed", "stage": "review"},
        )
        self.assertNotIn("private person", sink.getvalue())

    def test_large_contact_directory_cannot_fill_one_capture_line(self) -> None:
        name = "Synthetic " + "x" * 230
        contacts = tuple(
            Contact(index, name, ("+15551234567",), ()) for index in range(1, 60_001)
        )

        async def fake_collector(**_kwargs: object) -> IPhoneCapture:
            return IPhoneCapture(
                SourceResult(len(contacts), contacts, 0),
                SourceResult(0, (), 0),
                SourceResult(0, (), 0),
                "2026-10-08T00:00:00+00:00",
                "2001-01-01T00:00:00+00:00",
                (),
            )

        sink = io.StringIO()
        self.assertEqual(
            run_connect(io.StringIO('{"action":"disconnect"}\n'), sink, fake_collector),
            0,
        )
        lines = sink.getvalue().splitlines()
        capture = json.loads(lines[1])
        self.assertEqual(
            (capture["totalContacts"], len(capture["contacts"]), capture["nextCursor"]),
            (60_000, 200, 200),
        )
        self.assertLess(max(len(line.encode("utf-8")) for line in lines), 256 * 1024)

    def test_helper_reads_the_full_advertised_selection_without_truncation(self) -> None:
        ids = list(range(9_223_372_036_854_750_000, 9_223_372_036_854_775_000))
        line = json.dumps({"action": "review", "ids": ids}, separators=(",", ":")) + "\n"
        self.assertLessEqual(len(line.encode("utf-8")), MAX_COMMAND_BYTES)
        self.assertEqual(_command(io.StringIO(line))["ids"], ids)

    def test_password_prompt_stays_in_pipe_and_is_not_echoed(self) -> None:
        async def fake_collector(**kwargs: object) -> IPhoneCapture:
            self.assertEqual(kwargs["password_provider"](), "synthetic-secret")
            return synthetic_capture()

        source = io.StringIO(
            '{"action":"password","value":"synthetic-secret"}\n'
            '{"action":"disconnect"}\n'
        )
        sink = io.StringIO()
        self.assertEqual(run_connect(source, sink, fake_collector), 0)
        self.assertIn("password_required", sink.getvalue())
        self.assertNotIn("synthetic-secret", sink.getvalue())

    def test_transport_event_reports_existing_wifi_path_without_phone_details(
        self,
    ) -> None:
        async def fake_collector(**kwargs: object) -> IPhoneCapture:
            kwargs["connection_callback"]("wifi")
            return synthetic_capture()

        source = io.StringIO('{"action":"disconnect"}\n')
        sink = io.StringIO()
        self.assertEqual(run_connect(source, sink, fake_collector), 0)
        events = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(events[1], {"kind": "connection", "transport": "wifi"})
        self.assertNotIn("+15551234567", sink.getvalue())

    def test_browser_pairing_requires_review_and_closes_on_disconnect(self) -> None:
        async def fake_collector(**_kwargs: object) -> IPhoneCapture:
            return synthetic_capture()

        source = io.StringIO(
            '{"action":"pair"}\n'
            '{"action":"review","ids":[1]}\n'
            '{"action":"pair"}\n'
            '{"action":"review","ids":[999]}\n'
            '{"action":"pair"}\n'
            '{"action":"disconnect"}\n'
        )
        sink = io.StringIO()
        bridge = MagicMock()
        bridge.code = "1234567890"
        bridge.port = 48751
        bridge.handoff_id = "00000000-0000-4000-8000-000000000001"
        bridge.payload_sha256 = "a" * 64
        with patch("amplifai_phone.agent.BridgeServer", return_value=bridge) as factory:
            self.assertEqual(run_connect(source, sink, fake_collector), 0)
        events = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(
            [event["kind"] for event in events],
            [
                "state", "capture", "error", "review", "pairing", "error", "error", "state"
            ],
        )
        self.assertEqual(events[5:7], [{"kind": "error", "code": "selection"}] * 2)
        self.assertEqual(events[4]["pairCode"], "1234567890")
        self.assertEqual(events[4]["handoffId"], bridge.handoff_id)
        factory.assert_called_once()
        self.assertEqual(factory.call_args.args[1], {1})
        bridge.start.assert_called_once()
        bridge.close.assert_called_once()

    def test_saved_finish_requires_exact_current_run_and_explicit_ack(self) -> None:
        async def fake_collector(**_kwargs: object) -> IPhoneCapture:
            return synthetic_capture()

        run_id = "00000000-0000-4000-8000-000000000001"
        for acknowledged in (False, True):
            with self.subTest(acknowledged=acknowledged):
                bridge = MagicMock()
                bridge.code, bridge.port = "1234567890", 48751
                bridge.handoff_id, bridge.payload_sha256 = run_id, "a" * 64
                bridge.handoff_state.return_value = "saved" if acknowledged else "received"
                source = io.StringIO(
                    '{"action":"review","ids":[1]}\n{"action":"pair"}\n'
                    '{"action":"finish","handoffId":"wrong"}\n'
                    f'{{"action":"handoff-status","handoffId":"{run_id}"}}\n'
                    f'{{"action":"finish","handoffId":"{run_id}"}}\n'
                    '{"action":"disconnect"}\n'
                )
                sink = io.StringIO()
                with patch("amplifai_phone.agent.BridgeServer", return_value=bridge):
                    self.assertEqual(run_connect(source, sink, fake_collector), 0)
                events = [json.loads(line) for line in sink.getvalue().splitlines()]
                self.assertIn({"kind": "error", "code": "handoff_unconfirmed"}, events)
                self.assertIn({"kind": "handoff", "state": "saved" if acknowledged else "received", "handoffId": run_id}, events)
                self.assertEqual(events[-1], {"kind": "state", "state": "completed" if acknowledged else "disconnected"})
                self.assertEqual(bridge.close.call_count, 1)

    def test_pairing_bind_failure_keeps_local_review_available(self) -> None:
        async def fake_collector(**_kwargs: object) -> IPhoneCapture:
            return synthetic_capture()

        source = io.StringIO(
            '{"action":"review","ids":[1]}\n'
            '{"action":"pair"}\n'
            '{"action":"disconnect"}\n'
        )
        sink = io.StringIO()
        with patch("amplifai_phone.agent.BridgeServer", side_effect=OSError("in use")):
            self.assertEqual(run_connect(source, sink, fake_collector), 0)
        events = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(events[-2], {"kind": "error", "code": "bridge_unavailable"})
        self.assertEqual(events[-1], {"kind": "state", "state": "disconnected"})

    def test_admin_residue_is_read_only_until_clear_command(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-agent-test-") as temporary:
            root = Path(temporary) / "sessions"
            workspace = SessionWorkspace(root)
            directory = workspace.__enter__()
            (directory / "fixture").write_bytes(b"synthetic")
            with patch("amplifai_phone.workspace._pid_alive", return_value=False):
                inspect = io.StringIO()
                self.assertEqual(run_admin("inspect", inspect, root), 0)
                self.assertTrue(directory.exists())
                self.assertEqual(
                    json.loads(inspect.getvalue())["sessions"][0]["name"],
                    directory.name,
                )
                clear = io.StringIO()
                self.assertEqual(run_admin("clear-residue", clear, root), 0)
                self.assertFalse(directory.exists())


if __name__ == "__main__":
    unittest.main()
