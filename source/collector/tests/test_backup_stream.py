from __future__ import annotations

import asyncio
import errno
import io
import plistlib
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from amplifai_phone.backup_stream import DeviceBackupRejected, StreamedDeviceLink
from amplifai_phone.backup_watchdog import (
    BACKUP_IDLE_SECONDS,
    MAX_CONTROL_BYTES,
    MAX_CONTROL_ONLY_SECONDS,
    RECEIVE_CHUNK_BYTES,
    BackupNoFileProgress,
    BackupStalled,
    BackupWatchdog,
)
from amplifai_phone.metadata import SourceCapacityLimit, UnsupportedSchema
from amplifai_phone.workspace import MIN_FREE_BYTES, SessionWorkspace, WorkspaceError
from pymobiledevice3.exceptions import NotEnoughDiskSpaceError
from pymobiledevice3.services.device_link import CODE_ERROR_REMOTE


def prefixed(value: str) -> bytes:
    payload = value.encode()
    return struct.pack(">I", len(payload)) + payload


def file_transfer(name: str, payload: bytes) -> bytes:
    return (
        prefixed("HomeDomain-Library/SMS/sms.db")
        + prefixed(name)
        + struct.pack(">IB", len(payload) + 1, 0xC)
        + payload
        + struct.pack(">IB", 1, 0)
    )


class Wire:
    def __init__(self, payload: bytes, after_read=None):
        self.stream, self.after_read = io.BytesIO(payload), after_read
        self.requests = []
        self.send_plist = AsyncMock()
        self.sendall = AsyncMock()

    async def recvall(self, size: int) -> bytes:
        self.requests.append(size)
        payload = self.stream.read(size)
        if len(payload) != size:
            raise asyncio.IncompleteReadError(payload, size)
        if self.after_read:
            self.after_read(payload)
        return payload

    async def recv_plist(self):
        (size,) = struct.unpack(">I", await self.recvall(4))
        if size > MAX_CONTROL_BYTES:
            raise SourceCapacityLimit("Backup control frame exceeds safe memory bounds")
        return plistlib.loads(await self.recvall(size))


class StreamedBackupTest(unittest.IsolatedAsyncioTestCase):
    async def test_large_legitimate_control_frame_streams_in_bounded_chunks(self) -> None:
        content = "x" * (MAX_CONTROL_BYTES + 1)
        payload = plistlib.dumps(
            ["DLMessageProcessMessage", {"ErrorCode": 0, "Content": content}]
        )
        self.assertGreater(len(payload), MAX_CONTROL_BYTES)
        with tempfile.TemporaryDirectory(prefix="amplifai-large-control-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                wire = Wire(struct.pack(">I", len(payload)) + payload)
                link = StreamedDeviceLink(
                    wire, directory, workspace=workspace, watchdog=BackupWatchdog()
                )
                message = await link.receive_message()
                self.assertEqual(len(message[1]["Content"]), len(content))
                self.assertLessEqual(max(wire.requests), RECEIVE_CHUNK_BYTES)

    async def test_extreme_control_frame_still_fails_before_allocating_or_reading_it(self) -> None:
        from amplifai_phone.backup_stream import MAX_CONTROL_MESSAGE_BYTES

        with tempfile.TemporaryDirectory(prefix="amplifai-large-control-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                wire = Wire(struct.pack(">I", MAX_CONTROL_MESSAGE_BYTES + 1))
                link = StreamedDeviceLink(
                    wire, directory, workspace=workspace, watchdog=BackupWatchdog()
                )
                with self.assertRaises(SourceCapacityLimit):
                    await link.receive_message()
                self.assertEqual(wire.requests, [4])
                self.assertEqual(tuple(directory.iterdir()), (directory / ".amplifai-owned-session.json",))

    async def test_large_private_error_for_discarded_file_is_drained_not_retained(self) -> None:
        private_error = b"x" * 8192
        failed_file = (
            prefixed("synthetic-device-entry")
            + prefixed("phone/discarded")
            + struct.pack(">IB", len(private_error) + 1, CODE_ERROR_REMOTE)
            + private_error
        )
        with tempfile.TemporaryDirectory(prefix="amplifai-error-drain-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                wire = Wire(failed_file + file_transfer("phone/selected", b"ok") + prefixed(""))
                link = StreamedDeviceLink(
                    wire, directory, workspace=workspace, watchdog=BackupWatchdog(),
                    preserve_file=lambda name, _: name.endswith("selected"),
                )
                await link.upload_files([])
                self.assertEqual((directory / "phone/selected").read_bytes(), b"ok")
                self.assertLessEqual(max(wire.requests), RECEIVE_CHUNK_BYTES)

    async def test_iphone_space_query_uses_stream_capacity_not_selected_file_budget(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-space-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                wire = Wire(b"")
                link = StreamedDeviceLink(
                    wire, directory, workspace=workspace, watchdog=BackupWatchdog()
                )
                with patch(
                    "amplifai_phone.workspace.shutil.disk_usage",
                    return_value=SimpleNamespace(free=MIN_FREE_BYTES + 100),
                ):
                    await link.get_free_disk_space([])
                self.assertGreaterEqual(wire.send_plist.await_args.args[0][3], 8 * 1024**4)

    async def test_large_announced_backup_streams_without_claiming_that_disk_can_store_it(self) -> None:
        announced_full_backup = 275 * 1024**3
        selected_headroom = 128 * 1024**2
        with tempfile.TemporaryDirectory(prefix="amplifai-large-preflight-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                wire = Wire(
                    file_transfer("phone/selected", b"selected")
                    + file_transfer("phone/discarded", b"unrelated")
                    + prefixed("")
                )
                link = StreamedDeviceLink(
                    wire, directory, workspace=workspace,
                    watchdog=BackupWatchdog(),
                    preserve_file=lambda name, _: name.endswith("selected"),
                )
                with patch(
                    "amplifai_phone.workspace.shutil.disk_usage",
                    return_value=SimpleNamespace(free=MIN_FREE_BYTES + selected_headroom),
                ):
                    self.assertEqual(workspace.available_transfer_bytes(), selected_headroom)
                    await link.get_free_disk_space(["DLMessageGetFreeDiskSpace", announced_full_backup])
                    self.assertGreater(wire.send_plist.await_args.args[0][3], announced_full_backup)
                    await link.upload_files([])
                    self.assertEqual((directory / "phone/selected").read_bytes(), b"selected")
                    self.assertEqual((directory / "phone/discarded").stat().st_size, 0)
                    with self.assertRaises(WorkspaceError) as error:
                        workspace.check_bound(selected_headroom + 1)
                    self.assertEqual(error.exception.code, "workspace_low_space")

    async def test_purge_request_remains_a_specific_host_space_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-purge-preflight-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                link = StreamedDeviceLink(
                    Wire(b""), directory, workspace=workspace, watchdog=BackupWatchdog()
                )
                with self.assertRaises(NotEnoughDiskSpaceError):
                    await link.purge_disk_space(["DLMessagePurgeDiskSpace"])

    async def test_no_zero_byte_backup_progress_when_protocol_fails_before_file_frame(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-preflight-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                events = []
                link = StreamedDeviceLink(
                    Wire(b""), directory, workspace=workspace,
                    watchdog=BackupWatchdog(), transfer_callback=events.append,
                )
                link.publish_progress(final=True)
                self.assertEqual(events, [])

    async def test_filtered_file_readback_returns_empty_success_until_removed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-readback-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                wire = Wire(file_transfer("phone/discarded", b"private") + prefixed(""))
                link = StreamedDeviceLink(
                    wire,
                    directory,
                    workspace=workspace,
                    watchdog=BackupWatchdog(),
                    preserve_file=lambda *_: False,
                )
                await link.upload_files([])
                await link.download_files(
                    ["DLMessageDownloadFiles", ["phone/discarded"]]
                )
                self.assertEqual((directory / "phone/discarded").read_bytes(), b"")
                self.assertEqual(wire.send_plist.await_args.args[0][1], 0)
                self.assertIn(
                    struct.pack(">IB", 1, 0),
                    [call.args[0] for call in wire.sendall.await_args_list],
                )
                self.assertEqual(link._discarded_files, set())
                await link.remove_items(
                    ["DLMessageRemoveItems", ["phone/discarded"]]
                )
                await link.download_files(
                    ["DLMessageDownloadFiles", ["phone/discarded"]]
                )
                self.assertNotEqual(wire.send_plist.await_args.args[0][1], 0)

    async def test_filtered_entry_creation_reports_exhausted_disk_or_inodes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-entry-space-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                link = StreamedDeviceLink(
                    Wire(file_transfer("phone/discarded", b"private") + prefixed("")),
                    directory,
                    workspace=workspace,
                    watchdog=BackupWatchdog(),
                    preserve_file=lambda *_: False,
                )
                with patch(
                    "amplifai_phone.workspace.os.open",
                    side_effect=OSError(errno.ENOSPC, "synthetic private path"),
                ):
                    with self.assertRaises(WorkspaceError) as failure:
                        await link.upload_files([])
                self.assertEqual(failure.exception.code, "workspace_low_space")
                self.assertNotIn("private path", str(failure.exception))

    async def test_terminal_device_status_is_required_for_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                status = ["DLMessageProcessMessage", {"ErrorCode": 0}]
                payload = plistlib.dumps(status)
                wire = Wire(struct.pack(">I", len(payload)) + payload)
                link = StreamedDeviceLink(
                    wire, directory, workspace=workspace, watchdog=BackupWatchdog()
                )
                self.assertIsNone(await link.dl_loop())
                self.assertEqual(wire.requests, [4, len(payload)])

    async def test_device_backup_status_keeps_numeric_code_without_private_reason(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                status = [
                    "DLMessageProcessMessage",
                    {"ErrorCode": 205, "PrivateReason": "phone owner secret"},
                ]
                payload = plistlib.dumps(status)
                wire = Wire(struct.pack(">I", len(payload)) + payload)
                link = StreamedDeviceLink(
                    wire, directory, workspace=workspace, watchdog=BackupWatchdog()
                )
                with self.assertRaises(DeviceBackupRejected) as error:
                    await link.dl_loop()
                self.assertEqual(error.exception.device_code, 205)
                self.assertNotIn("phone owner secret", str(error.exception))

    async def test_chunked_selected_and_discarded_frames_emit_private_numeric_progress(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            clock = [0.0]

            def read(_payload):
                clock[0] += 0.2

            payload = b"synthetic-only" * 40_000
            wire = Wire(
                file_transfer("phone/selected", payload)
                + file_transfer("phone/discarded", payload)
                + prefixed(""),
                read,
            )
            watchdog = BackupWatchdog(clock=lambda: clock[0])
            watchdog.start()
            events = []
            with workspace as directory:
                link = StreamedDeviceLink(
                    wire,
                    directory,
                    workspace=workspace,
                    watchdog=watchdog,
                    preserve_file=lambda name, _: name.endswith("selected"),
                    transfer_callback=events.append,
                    clock=lambda: clock[0],
                )
                await link.upload_files([])
                self.assertEqual((directory / "phone/selected").read_bytes(), payload)
                self.assertEqual((directory / "phone/discarded").stat().st_size, 0)
                self.assertEqual(link._discarded_files, set())
                self.assertLessEqual(max(wire.requests), 128 * 1024)
                self.assertEqual(events[-1]["receivedBytes"], 2 * len(payload))
                self.assertEqual(events[-1]["retainedBytes"], len(payload))
                self.assertEqual(events[-1]["discardedBytes"], len(payload))
                self.assertGreater(len(events), 2)
                self.assertTrue(
                    all(
                        "phone" not in repr(event)
                        and "synthetic-only" not in repr(event)
                        for event in events
                    )
                )
                link.cleanup_discarded_files()
                self.assertEqual((directory / "phone/discarded").stat().st_size, 0)
            self.assertFalse(directory.exists())

    async def test_low_space_is_checked_before_selected_bytes_are_written(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            watchdog = BackupWatchdog()
            watchdog.start()
            with workspace as directory:
                wire = Wire(file_transfer("phone/selected", b"12345678") + prefixed(""))
                link = StreamedDeviceLink(
                    wire, directory, workspace=workspace, watchdog=watchdog
                )
                with patch(
                    "amplifai_phone.workspace.shutil.disk_usage",
                    return_value=SimpleNamespace(free=MIN_FREE_BYTES + 15),
                ):
                    with self.assertRaises(WorkspaceError) as error:
                        await link.upload_files([])
                    self.assertEqual(error.exception.code, "workspace_low_space")
                    self.assertEqual((directory / "phone/selected").stat().st_size, 0)

    async def test_cancelled_receive_cannot_write_late_payload(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            watchdog = BackupWatchdog()
            watchdog.start()
            payload = b"synthetic-late-file-data"
            wire = Wire(
                file_transfer("phone/selected", payload),
                lambda data: watchdog.abort() if data == payload else None,
            )
            with workspace as directory:
                link = StreamedDeviceLink(
                    wire, directory, workspace=workspace, watchdog=watchdog
                )
                with self.assertRaises(asyncio.CancelledError):
                    await link.upload_files([])
                self.assertEqual((directory / "phone/selected").stat().st_size, 0)

    async def test_untrusted_destination_does_not_touch_owner_file(self) -> None:
        for name in (
            "../owner",
            "/owner",
            "phone/../owner",
            ".amplifai-owned-session.json",
            "pairing/owner",
        ):
            with (
                self.subTest(name=name),
                tempfile.TemporaryDirectory(
                    prefix="amplifai-stream-test-"
                ) as temporary,
            ):
                workspace = SessionWorkspace(Path(temporary) / "sessions")
                owner = Path(temporary) / "owner"
                owner.write_bytes(b"owner-data")
                with workspace as directory:
                    link = StreamedDeviceLink(
                        Wire(file_transfer(name, b"synthetic")),
                        directory,
                        workspace=workspace,
                        watchdog=BackupWatchdog(),
                    )
                    with self.assertRaises(WorkspaceError):
                        await link.upload_files([])
                self.assertEqual(owner.read_bytes(), b"owner-data")

    async def test_control_length_limit_is_not_reported_as_schema_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                link = StreamedDeviceLink(
                    Wire(struct.pack(">I", 4097)),
                    directory,
                    workspace=workspace,
                    watchdog=BackupWatchdog(),
                )
                with self.assertRaises(SourceCapacityLimit):
                    await link.upload_files([])

    async def test_malformed_frame_remains_a_format_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                wire = Wire(
                    prefixed("source")
                    + prefixed("phone/selected")
                    + struct.pack(">I", 0)
                )
                link = StreamedDeviceLink(
                    wire, directory, workspace=workspace, watchdog=BackupWatchdog()
                )
                with self.assertRaises(UnsupportedSchema):
                    await link.upload_files([])

    async def test_self_copy_rejects_before_truncating_the_source(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                (directory / "source").write_bytes(b"selected-source")
                link = StreamedDeviceLink(
                    Wire(b""), directory, workspace=workspace, watchdog=BackupWatchdog()
                )
                with self.assertRaises(WorkspaceError):
                    await link.copy_item(["DLMessageCopyItem", "source", "source"])
                self.assertEqual(
                    (directory / "source").read_bytes(), b"selected-source"
                )

    async def test_copy_and_move_overwrite_release_only_replaced_copy_budget(
        self,
    ) -> None:
        for operation in ("copy", "move"):
            with (
                self.subTest(operation=operation),
                tempfile.TemporaryDirectory(
                    prefix="amplifai-stream-test-"
                ) as temporary,
            ):
                workspace = SessionWorkspace(Path(temporary) / "sessions")
                with workspace as directory:
                    link = StreamedDeviceLink(
                        Wire(b""),
                        directory,
                        workspace=workspace,
                        watchdog=BackupWatchdog(),
                    )
                    for name, payload in (("a", b"12345678"), ("b", b"12345")):
                        with workspace.open_private(directory / name) as stream:
                            workspace.write_chunk(stream, payload, reserve_copy=True)
                        link._reserved_paths[directory / name] = len(payload)
                    if operation == "copy":
                        await link.copy_item(["DLMessageCopyItem", "a", "b"])
                        self.assertEqual(workspace._pending_copy_bytes, 16)
                    else:
                        await link.move_items(["DLMessageMoveItems", {"a": "b"}])
                        self.assertEqual(workspace._pending_copy_bytes, 8)
                    self.assertEqual((directory / "b").read_bytes(), b"12345678")

    async def test_moving_empty_filtered_entry_releases_replaced_copy_budget(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-move-budget-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                link = StreamedDeviceLink(
                    Wire(b""), directory, workspace=workspace, watchdog=BackupWatchdog()
                )
                source = directory / "empty"
                target = directory / "selected"
                with workspace.open_private(source):
                    pass
                with workspace.open_private(target) as stream:
                    workspace.write_chunk(stream, b"selected", reserve_copy=True)
                link._reserved_paths[target] = len(b"selected")
                await link.move_items(["DLMessageMoveItems", {"empty": "selected"}])
                self.assertEqual(target.read_bytes(), b"")
                self.assertEqual(workspace._pending_copy_bytes, 0)
                self.assertEqual(link._reserved_paths, {})

    async def test_discarded_payloads_keep_empty_entries_without_path_set(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                wire = Wire(
                    file_transfer("phone/discarded", b"synthetic") + prefixed("")
                )
                link = StreamedDeviceLink(
                    wire,
                    directory,
                    workspace=workspace,
                    watchdog=BackupWatchdog(),
                    preserve_file=lambda *_: False,
                )
                await link.upload_files([])
                await link.move_items(
                    ["DLMessageMoveItems", {"phone/discarded": "phone/moved"}]
                )
                await link.copy_item(
                    ["DLMessageCopyItem", "phone/moved", "phone/copied"]
                )
                await link.remove_items(
                    ["DLMessageRemoveItems", ["phone/moved", "phone/copied"]]
                )
                await link.contents_of_directory(
                    ["DLMessageContentsOfDirectory", "missing"]
                )
                self.assertEqual(link._discarded_files, set())
                self.assertFalse(
                    any(path.is_file() for path in (directory / "phone").rglob("*"))
                )

    async def test_file_copy_and_move_to_existing_directory_match_pinned_semantics(
        self,
    ) -> None:
        for operation in ("copy", "move"):
            for target_exists in (False, True):
                with (
                    self.subTest(operation=operation, target_exists=target_exists),
                    tempfile.TemporaryDirectory(
                        prefix="amplifai-stream-test-"
                    ) as temporary,
                ):
                    workspace = SessionWorkspace(Path(temporary) / "sessions")
                    with workspace as directory:
                        link = StreamedDeviceLink(
                            Wire(b""),
                            directory,
                            workspace=workspace,
                            watchdog=BackupWatchdog(),
                        )
                        (directory / "target").mkdir()
                        originals = [(directory / "source", b"source-data")]
                        if target_exists:
                            originals.append((directory / "target/source", b"old"))
                        for path, payload in originals:
                            with workspace.open_private(path) as stream:
                                workspace.write_chunk(
                                    stream, payload, reserve_copy=True
                                )
                            link._reserved_paths[path] = len(payload)
                        if operation == "move":
                            if target_exists:
                                with self.assertRaises(shutil.Error):
                                    await link.move_items(
                                        ["DLMessageMoveItems", {"source": "target"}]
                                    )
                                self.assertEqual(
                                    (directory / "source").read_bytes(), b"source-data"
                                )
                                self.assertEqual(
                                    (directory / "target/source").read_bytes(), b"old"
                                )
                                self.assertEqual(workspace._pending_copy_bytes, 14)
                                continue
                            await link.move_items(
                                ["DLMessageMoveItems", {"source": "target"}]
                            )
                            expected_budget = 11
                        else:
                            await link.copy_item(
                                ["DLMessageCopyItem", "source", "target"]
                            )
                            expected_budget = 22
                        self.assertEqual(
                            (directory / "target/source").read_bytes(), b"source-data"
                        )
                        self.assertEqual(workspace._pending_copy_bytes, expected_budget)

    async def test_large_outbound_file_has_exact_data_and_bounded_frames(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                payload = b"synthetic" * 30_000
                (directory / "selected").write_bytes(payload)
                wire = Wire(b"")
                link = StreamedDeviceLink(
                    wire, directory, workspace=workspace, watchdog=BackupWatchdog()
                )
                await link.download_files(["DLMessageDownloadFiles", ["selected"]])
                frames = [call.args[0] for call in wire.sendall.await_args_list]
                self.assertEqual(frames[0], prefixed("selected"))
                data_frames = frames[1:-2]
                self.assertEqual(b"".join(frame[5:] for frame in data_frames), payload)
                self.assertTrue(
                    all(
                        frame[4] == 0xC and len(frame) <= 128 * 1024 + 5
                        for frame in data_frames
                    )
                )
                self.assertEqual(frames[-2:], [struct.pack(">IB", 1, 0), b"\x00" * 4])

    async def test_copy_has_a_cancellable_checkpoint_before_the_next_write(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                (directory / "source").write_bytes(b"s" * (3 * 128 * 1024))
                watchdog = BackupWatchdog()
                link = StreamedDeviceLink(
                    Wire(b""), directory, workspace=workspace, watchdog=watchdog
                )
                asyncio.get_running_loop().call_soon(watchdog.abort)
                with self.assertRaises(asyncio.CancelledError):
                    await link.copy_item(["DLMessageCopyItem", "source", "copy"])
                self.assertEqual((directory / "copy").stat().st_size, 128 * 1024)
                self.assertEqual((directory / "source").stat().st_size, 3 * 128 * 1024)

    async def test_empty_directory_copy_has_no_late_creation_after_abort(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amplifai-stream-test-") as temporary:
            workspace = SessionWorkspace(Path(temporary) / "sessions")
            with workspace as directory:
                (directory / "source/one/two/three").mkdir(parents=True)
                watchdog = BackupWatchdog()
                link = StreamedDeviceLink(
                    Wire(b""), directory, workspace=workspace, watchdog=watchdog
                )
                asyncio.get_running_loop().call_soon(watchdog.abort)
                with self.assertRaises(asyncio.CancelledError):
                    await link.copy_item(["DLMessageCopyItem", "source", "copy"])
                self.assertTrue((directory / "copy").is_dir())
                self.assertEqual(list((directory / "copy").iterdir()), [])
            self.assertFalse(directory.exists())


class TransferDeadlineTest(unittest.TestCase):
    def test_real_payload_progress_extends_four_hour_budget(self) -> None:
        clock = [0.0]
        watchdog = BackupWatchdog(clock=lambda: clock[0])
        watchdog.start()
        clock[0] = 4 * 60 * 60 + 1
        watchdog.payload_received(1024**3)
        self.assertIsNone(watchdog._expired())
        clock[0] += BACKUP_IDLE_SECONDS
        self.assertIsInstance(watchdog._expired(), BackupStalled)

    def test_control_chatter_cannot_hide_a_file_payload_stall(self) -> None:
        clock = [0.0]
        watchdog = BackupWatchdog(clock=lambda: clock[0])
        watchdog.start()
        watchdog.payload_received(128 * 1024)
        watchdog.file_transfer_started()
        clock[0] = BACKUP_IDLE_SECONDS
        watchdog.received(4)
        self.assertIsInstance(watchdog._expired(), BackupStalled)

    def test_completed_file_allows_bounded_live_control_finalization(self) -> None:
        clock = [0.0]
        watchdog = BackupWatchdog(clock=lambda: clock[0])
        watchdog.start()
        watchdog.file_transfer_started()
        watchdog.payload_received(128 * 1024)
        watchdog.file_transfer_completed()
        for minute in range(1, 31):
            clock[0] = minute * 60
            watchdog.received(4)
            self.assertIsNone(watchdog._expired())
        clock[0] = MAX_CONTROL_ONLY_SECONDS
        watchdog.received(4)
        self.assertIsInstance(watchdog._expired(), BackupNoFileProgress)
