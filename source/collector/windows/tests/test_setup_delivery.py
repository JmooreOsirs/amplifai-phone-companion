"""Headless routing/security boundaries. Actual Win32 pipe I/O is a separate gate."""
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from native_setup import SetupLink, SetupProtocolError
from setup_delivery import (DeliveryError, PendingDelivery, PrimaryExists, SetupDelivery,
                            WindowsPipe, launch_uri, message_uri)
from test_native_setup import LINK_TEXT, ROOT_LINK_TEXT


class WinError(Exception):
    def __init__(self, code):
        self.winerror = code


class DeliveryTests(unittest.TestCase):
    def test_only_one_exact_setup_uri_or_plain_launch(self):
        self.assertIsNone(launch_uri([]))
        self.assertIsNone(message_uri(b"OPEN"))
        for uri in (LINK_TEXT, ROOT_LINK_TEXT):
            with self.subTest(root_slash="://setup/?" in uri):
                self.assertEqual(launch_uri([uri]), uri)
                self.assertEqual(message_uri(uri.encode()), uri)
            for arguments in ([uri, "extra"], ["--setup", uri], [uri + "&host=evil"]):
                with self.subTest(argument_count=len(arguments)), self.assertRaises(SetupProtocolError):
                    launch_uri(arguments)
            for invalid in (uri + "#fragment", uri + "&host=evil", uri.replace("://setup", "://setup/other", 1)):
                with self.subTest(invalid_shape=invalid[:30]), self.assertRaises(SetupProtocolError):
                    message_uri(invalid.encode())
        with self.assertRaises(SetupProtocolError):
            launch_uri(["--fixture"])
        for body in (b"", b"x" * 513, b"\xff", b"OPEN\n", b"https://example.test"):
            with self.assertRaises(SetupProtocolError):
                message_uri(body)

    def test_timed_out_queue_request_cannot_deliver_later(self):
        clock = [0]
        pending = PendingDelivery(LINK_TEXT, monotonic=lambda: clock[0])
        clock[0] = 4
        receiver = Mock(return_value="accepted")
        pending.dispatch(receiver)
        receiver.assert_not_called()
        self.assertTrue(pending.done.is_set())
        self.assertIsNone(pending.uri)
        self.assertEqual(pending.reply, "unavailable")

    def test_cancelled_delivery_does_not_reach_ui_and_uri_is_erased(self):
        pending = PendingDelivery(LINK_TEXT)
        pending.cancel()
        receiver = Mock()
        pending.dispatch(receiver)
        receiver.assert_not_called()
        self.assertIsNone(pending.uri)
        self.assertNotIn(LINK_TEXT, repr(pending))

    def test_ack_is_ui_admission_only_and_request_is_dispatched_once(self):
        for reply in ("accepted", "duplicate", "busy", "unavailable", "malformed"):
            pending = PendingDelivery(LINK_TEXT)
            receiver = Mock(return_value=reply)
            pending.dispatch(receiver)
            pending.dispatch(receiver)
            receiver.assert_called_once_with(LINK_TEXT)
            self.assertEqual(pending.reply, "unavailable" if reply == "malformed" else reply)
            self.assertIsNone(pending.uri)

    def test_queue_is_bounded_and_shutdown_cancels_undelivered_request(self):
        backend = Mock()
        delivery = SetupDelivery(backend)
        pending = PendingDelivery(LINK_TEXT)
        delivery.pending.put_nowait(pending)
        with self.assertRaises(queue.Full):
            delivery.pending.put_nowait(PendingDelivery(LINK_TEXT))
        delivery.stop()
        self.assertTrue(pending.done.is_set())
        self.assertIsNone(pending.uri)
        backend.stop.assert_called_once()

    def test_existing_primary_is_never_replaced_and_forward_revalidates(self):
        backend = Mock()
        backend.create.side_effect = PrimaryExists()
        delivery = SetupDelivery(backend)
        self.assertFalse(delivery.start())
        self.assertIsNone(delivery.thread)
        for uri in (LINK_TEXT, ROOT_LINK_TEXT):
            backend.forward.reset_mock()
            delivery.forward(uri)
            backend.forward.assert_called_once_with(uri)
        backend.forward.reset_mock()
        for invalid in ("https://elsewhere.test", ROOT_LINK_TEXT + "&extra=true"):
            with self.assertRaises(SetupProtocolError):
                delivery.forward(invalid)
        backend.forward.assert_not_called()

    def test_receiver_runs_only_when_ui_drains(self):
        delivery = SetupDelivery(Mock())
        receiver = Mock(return_value="accepted")
        delivery.pending.put_nowait(PendingDelivery(LINK_TEXT))
        receiver.assert_not_called()
        delivery.drain(receiver)
        receiver.assert_called_once_with(LINK_TEXT)
        delivery.drain(receiver)
        self.assertEqual(receiver.call_count, 1)


class NativePipeConfigurationTests(unittest.TestCase):
    def modules(self):
        token = Mock()
        security = Mock(TokenGroups=2, SDDL_REVISION_1=1)
        security.OpenProcessToken.return_value = token
        security.GetTokenInformation.return_value = [("ordinary", 4), ("logon", 0xC0000000)]
        security.ConvertSidToStringSid.return_value = "S-1-5-5-111-222"
        modules = {"pywintypes": SimpleNamespace(error=WinError, SECURITY_ATTRIBUTES=SimpleNamespace),
                   "win32api": Mock(), "win32con": SimpleNamespace(TOKEN_QUERY=8),
                   "win32event": Mock(), "win32file": SimpleNamespace(FILE_FLAG_OVERLAPPED=0x40000000),
                   "win32pipe": Mock(PIPE_ACCESS_DUPLEX=3, PIPE_TYPE_MESSAGE=4, PIPE_READMODE_MESSAGE=2),
                   "win32security": security}
        return modules, token

    def test_primary_uses_logon_sid_protected_dacl_first_instance_and_remote_rejection(self):
        modules, token = self.modules()
        with patch.dict("sys.modules", modules):
            pipe = WindowsPipe()
            pipe.create()
        token.Close.assert_called_once()
        modules["win32security"].ConvertStringSecurityDescriptorToSecurityDescriptor.assert_called_once_with(
            "D:P(A;;GA;;;S-1-5-5-111-222)", 1)
        args = modules["win32pipe"].CreateNamedPipe.call_args.args
        self.assertEqual(args[0], r"\\.\pipe\AMPLIFaiPhone.Setup.v2.S-1-5-5-111-222")
        self.assertTrue(args[1] & 0x00080000)
        self.assertTrue(args[1] & 0x40000000)
        self.assertTrue(args[2] & 0x8)
        self.assertEqual(args[3:6], (1, 512, 512))
        self.assertFalse(args[-1].bInheritHandle)

    def test_missing_or_ambiguous_logon_sid_fails_closed(self):
        for groups in ([], [("a", 0xC0000000), ("b", 0xC0000000)]):
            modules, token = self.modules()
            modules["win32security"].GetTokenInformation.return_value = groups
            with patch.dict("sys.modules", modules), self.assertRaises(DeliveryError):
                WindowsPipe()
            token.Close.assert_called_once()
            modules["win32pipe"].CreateNamedPipe.assert_not_called()

    def test_pending_io_storage_survives_failed_bounded_cancellation(self):
        pipe = WindowsPipe.__new__(WindowsPipe)
        pipe.file = Mock()
        pipe.event = Mock(WAIT_OBJECT_0=0)
        pipe.event.WaitForSingleObject.return_value = 258
        pipe.failed = False
        pipe.quarantine = None
        overlap, buffer = Mock(), bytearray(b"synthetic")
        with self.assertRaises(DeliveryError):
            pipe._cancel("handle", overlap, buffer)
        pipe.file.CancelIo.assert_called_once_with("handle")
        pipe.event.WaitForSingleObject.assert_called_once_with(overlap.hEvent, 1000)
        self.assertIs(pipe.quarantine[0], overlap)
        self.assertIs(pipe.quarantine[1], buffer)
        self.assertTrue(pipe.failed)


# These cases use the real local Windows kernel pipe, with synthetic links only.
# They neither import the product window nor instantiate any authority transport.
import sys
import time


@unittest.skipUnless(sys.platform == "win32", "Requires actual Windows named pipes")
class WindowsPipeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.delivery = SetupDelivery()
        if not self.delivery.start():
            self.delivery.stop()
            self.fail("A companion already owns the same-logon pipe; qualification will not replace it")

    def tearDown(self):
        self.delivery.stop()
        self.assertFalse(self.delivery.thread.is_alive(), "Pipe worker did not cancel within its bound")

    def exchange(self, uri, receiver=None):
        result = []
        def client():
            backend = WindowsPipe()
            try:
                result.append(backend.forward(uri))
            except DeliveryError:
                result.append("unavailable")
            finally:
                backend.close()
        thread = threading.Thread(target=client, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while thread.is_alive() and time.monotonic() < deadline:
            if receiver is not None:
                self.delivery.drain(receiver)
            thread.join(0.02)
        self.assertFalse(thread.is_alive(), "Client exceeded bounded delivery deadline")
        return result[0]

    def test_same_logon_acl_primary_exclusion_plain_open_and_repeated_delivery(self):
        backend = self.delivery.backend
        security = backend.security
        descriptor = security.GetSecurityInfo(backend.handle, security.SE_KERNEL_OBJECT,
                                               security.DACL_SECURITY_INFORMATION)
        self.assertTrue(descriptor.GetSecurityDescriptorControl()[0] & 0x1000)  # protected DACL
        acl = descriptor.GetSecurityDescriptorDacl()
        self.assertEqual(acl.GetAceCount(), 1)
        self.assertEqual(security.ConvertSidToStringSid(acl.GetAce(0)[2]), backend.logon_sid)
        second = SetupDelivery()
        try:
            self.assertFalse(second.start())
        finally:
            second.stop()
        seen = []
        def receive(uri):
            if uri in seen:
                return "duplicate"
            seen.append(uri)
            return "accepted"
        self.assertEqual(self.exchange(None, receive), "accepted")
        self.assertEqual(self.exchange(LINK_TEXT, receive), "accepted")
        self.assertEqual(self.exchange(LINK_TEXT, receive), "duplicate")
        self.assertEqual(seen, [None, LINK_TEXT])

    def test_busy_reply_preserves_primary_for_next_delivery(self):
        self.assertEqual(self.exchange(LINK_TEXT, lambda _uri: "busy"), "unavailable")
        self.assertEqual(self.exchange(LINK_TEXT, lambda _uri: "accepted"), "accepted")

    def test_single_root_slash_delivers_the_same_authority_as_empty_path(self):
        seen = []
        def receive(uri):
            link = SetupLink.parse(uri)
            if link in seen:
                return "duplicate"
            seen.append(link)
            return "accepted"
        self.assertEqual(self.exchange(LINK_TEXT, receive), "accepted")
        self.assertEqual(self.exchange(ROOT_LINK_TEXT, receive), "duplicate")
        self.assertEqual(seen, [SetupLink.parse(LINK_TEXT)])

    def test_ui_timeout_never_delivers_later(self):
        self.assertEqual(self.exchange(LINK_TEXT), "unavailable")
        receiver = Mock(return_value="accepted")
        self.delivery.drain(receiver)
        receiver.assert_not_called()
        self.assertEqual(self.exchange(LINK_TEXT, receiver), "accepted")

    def test_shutdown_cancels_idle_connect_and_releases_primary(self):
        self.delivery.stop()
        self.assertFalse(self.delivery.thread.is_alive())
        replacement = SetupDelivery()
        try:
            self.assertTrue(replacement.start())
        finally:
            replacement.stop()


if __name__ == "__main__":
    unittest.main()
