"""Same-logon Windows URI routing. Delivery never grants collection authority."""
from __future__ import annotations

import queue
import threading
import time

from native_setup import SetupLink, SetupProtocolError

MAX_MESSAGE = 512
CLIENT_SECONDS = 8
REQUEST_SECONDS = 3
IO_SECONDS = 2
REPLIES = frozenset({"accepted", "duplicate", "busy", "unavailable"})


class DeliveryError(RuntimeError):
    def __init__(self):
        super().__init__("The running companion could not receive this setup. Close it after local cleanup, reopen it, and retry from the original account tab.")


class PrimaryExists(DeliveryError):
    pass


def launch_uri(arguments):
    if not arguments:
        return None
    if len(arguments) != 1:
        raise SetupProtocolError("Open one setup link from your account")
    SetupLink.parse(arguments[0])
    return arguments[0]


def message_uri(body):
    if body == b"OPEN":
        return None
    if type(body) is not bytes or not 0 < len(body) <= MAX_MESSAGE:
        raise SetupProtocolError("Invalid setup delivery")
    try:
        uri = body.decode("ascii")
    except UnicodeError:
        raise SetupProtocolError("Invalid setup delivery") from None
    SetupLink.parse(uri)
    return uri


class PendingDelivery:
    """One expiring UI admission, with no capability-bearing repr or persistence."""
    def __init__(self, uri, *, monotonic=time.monotonic):
        self.uri = uri
        self.clock = monotonic
        self.deadline = self.clock() + REQUEST_SECONDS
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.reply = "unavailable"

    def dispatch(self, receiver):
        with self.lock:
            if self.done.is_set() or self.clock() >= self.deadline:
                self.uri = None
                self.done.set()
                return
            self.reply = receiver(self.uri)
            if self.reply not in REPLIES:
                self.reply = "unavailable"
            self.uri = None
            self.done.set()

    def cancel(self):
        with self.lock:
            self.uri = None
            self.done.set()


class WindowsPipe:
    """One first-instance message pipe, protected by the current logon SID."""
    def __init__(self):
        import pywintypes
        import win32api
        import win32con
        import win32event
        import win32file
        import win32pipe
        import win32security
        self.types, self.api, self.con = pywintypes, win32api, win32con
        self.event, self.file, self.pipe, self.security = win32event, win32file, win32pipe, win32security
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            logons = [sid for sid, flags in win32security.GetTokenInformation(token, win32security.TokenGroups)
                      if flags & 0xC0000000 == 0xC0000000]  # SE_GROUP_LOGON_ID
            if len(logons) != 1:
                raise DeliveryError()
            self.logon_sid = win32security.ConvertSidToStringSid(logons[0])
        finally:
            token.Close()
        self.name = r"\\.\pipe\AMPLIFaiPhone.Setup.v2." + self.logon_sid
        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
            f"D:P(A;;GA;;;{self.logon_sid})", win32security.SDDL_REVISION_1)
        attributes.bInheritHandle = False
        self.attributes = attributes
        self.stop_event = win32event.CreateEvent(None, True, False, None)
        self.handle = None
        self.quarantine = None
        self.failed = False
        self.closed = False

    def create(self):
        try:
            self.handle = self.pipe.CreateNamedPipe(self.name,
                self.pipe.PIPE_ACCESS_DUPLEX | self.file.FILE_FLAG_OVERLAPPED | 0x00080000,  # FIRST_PIPE_INSTANCE
                self.pipe.PIPE_TYPE_MESSAGE | self.pipe.PIPE_READMODE_MESSAGE | 0x8,  # REJECT_REMOTE_CLIENTS
                1, MAX_MESSAGE, MAX_MESSAGE, int(IO_SECONDS * 1000), self.attributes)
        except self.types.error as error:
            if error.winerror in (5, 231):
                raise PrimaryExists() from None
            raise DeliveryError() from None

    def stop(self):
        self.event.SetEvent(self.stop_event)

    def _cancel(self, handle, overlapped, buffer):
        # All I/O for this handle is issued on this thread. CancelIo cannot cancel
        # another thread's operation; shutdown signals this thread instead.
        self.file.CancelIo(handle)
        if self.event.WaitForSingleObject(overlapped.hEvent, 1000) != self.event.WAIT_OBJECT_0:
            # Keep pending native storage alive until process exit. A failed pipe
            # stays exclusive and cannot route another request or start a collector.
            self.quarantine = (overlapped, buffer)
            self.failed = True
            raise DeliveryError()

    def _io(self, handle, action, body=None, *, seconds=IO_SECONDS):
        if self.failed:
            raise DeliveryError()
        overlapped = self.types.OVERLAPPED()
        overlapped.hEvent = self.event.CreateEvent(None, True, False, None)
        buffer = None
        pending = False
        try:
            if action == "connect":
                result = self.pipe.ConnectNamedPipe(handle, overlapped)
                if result == 535:  # ERROR_PIPE_CONNECTED: connected before ConnectNamedPipe.
                    return None
            elif action == "read":
                buffer = self.file.AllocateReadBuffer(MAX_MESSAGE + 1)
                result, _ = self.file.ReadFile(handle, buffer, overlapped)
            elif action == "write":
                buffer = body
                result, _ = self.file.WriteFile(handle, buffer, overlapped)
            else:
                raise DeliveryError()
            if result == 997:  # ERROR_IO_PENDING
                pending = True
                ready = self.event.WaitForMultipleObjects([self.stop_event, overlapped.hEvent], False,
                                                          max(1, int(seconds * 1000)))
                if ready != self.event.WAIT_OBJECT_0 + 1:
                    self._cancel(handle, overlapped, buffer)
                    pending = False
                    raise DeliveryError()
            elif result != 0:
                raise DeliveryError()
            size = self.file.GetOverlappedResult(handle, overlapped, False)
            pending = False
            if action == "read":
                if not 0 < size <= MAX_MESSAGE:
                    raise DeliveryError()
                return bytes(buffer[:size])
            if action == "write" and size != len(body):
                raise DeliveryError()
        except self.types.error:
            raise DeliveryError() from None
        finally:
            if pending and not self.quarantine:
                try:
                    self._cancel(handle, overlapped, buffer)
                except (self.types.error, DeliveryError):
                    self.quarantine = (overlapped, buffer)
                    self.failed = True
            if not self.quarantine or self.quarantine[0] is not overlapped:
                overlapped.hEvent.Close()

    def connect(self):
        self._io(self.handle, "connect", seconds=30)

    def read(self):
        return self._io(self.handle, "read")

    def reply(self, reply):
        self._io(self.handle, "write", reply.encode("ascii"))
        # Wait boundedly for client EOF so DisconnectNamedPipe cannot discard an
        # unread ACK. Never FlushFileBuffers: that can block behind a stalled client.
        try:
            self._io(self.handle, "read", seconds=1)
        except DeliveryError:
            pass  # EOF/timeout after the single reply grants no further action.

    def disconnect(self):
        if not self.failed:
            try:
                self.pipe.DisconnectNamedPipe(self.handle)
            except self.types.error as error:
                if error.winerror not in (109, 233):
                    self.failed = True

    def forward(self, uri):
        body = b"OPEN" if uri is None else uri.encode("ascii")
        message_uri(body)
        deadline = time.monotonic() + CLIENT_SECONDS
        handle = None
        try:
            while time.monotonic() < deadline:
                try:
                    # Identification SQOS prevents an impersonating pipe server
                    # from receiving a stronger client impersonation token.
                    handle = self.file.CreateFile(self.name, self.con.GENERIC_READ | self.con.GENERIC_WRITE,
                        0, None, self.con.OPEN_EXISTING,
                        self.file.FILE_FLAG_OVERLAPPED | 0x00100000 | 0x00010000, None)
                    break
                except self.types.error as error:
                    if error.winerror != 231:
                        raise DeliveryError() from None
                    try:
                        self.pipe.WaitNamedPipe(self.name, 250)
                    except self.types.error as waiting:
                        if waiting.winerror not in (121, 231):
                            raise DeliveryError() from None
            if handle is None:
                raise DeliveryError()
            self.pipe.SetNamedPipeHandleState(handle, self.pipe.PIPE_READMODE_MESSAGE, None, None)
            self._io(handle, "write", body, seconds=min(IO_SECONDS, max(0, deadline - time.monotonic())))
            reply = self._io(handle, "read", seconds=max(0, deadline - time.monotonic())).decode("ascii")
            if reply not in ("accepted", "duplicate"):
                raise DeliveryError()
            return reply
        except (self.types.error, UnicodeError):
            raise DeliveryError() from None
        finally:
            if handle is not None:
                handle.Close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.handle is not None:
            self.handle.Close()
            self.handle = None
        # An exceptional incomplete cancellation retains native storage through
        # process shutdown; do not free an OVERLAPPED still owned by the kernel.
        if self.quarantine is None:
            self.stop_event.Close()


class SetupDelivery:
    def __init__(self, backend=None):
        self.backend = backend if backend is not None else WindowsPipe()
        self.pending = queue.Queue(maxsize=1)
        self.closed = threading.Event()
        self.thread = None
        self.failed = False

    def start(self):
        try:
            self.backend.create()
        except PrimaryExists:
            return False
        self.thread = threading.Thread(target=self._serve, name="native-setup-delivery", daemon=True)
        self.thread.start()
        return True

    def forward(self, uri):
        launch_uri([] if uri is None else [uri])
        return self.backend.forward(uri)

    def _serve(self):
        try:
            while not self.closed.is_set():
                try:
                    self.backend.connect()
                    uri = message_uri(self.backend.read())
                    pending = PendingDelivery(uri)
                    try:
                        self.pending.put_nowait(pending)
                    except queue.Full:
                        self.backend.reply("busy")
                        continue
                    while not pending.done.wait(0.05) and not self.closed.is_set() and time.monotonic() < pending.deadline:
                        pass
                    if not pending.done.is_set():
                        pending.cancel()
                    self.backend.reply(pending.reply)
                except (DeliveryError, SetupProtocolError):
                    if self.backend.failed:
                        self.failed = True
                        return
                finally:
                    self.backend.disconnect()
        finally:
            if self.closed.is_set():
                self.backend.close()

    def drain(self, receiver):
        try:
            pending = self.pending.get_nowait()
        except queue.Empty:
            return
        pending.dispatch(receiver)

    def stop(self):
        if self.closed.is_set():
            return
        self.closed.set()
        self.backend.stop()
        try:
            self.pending.get_nowait().cancel()
        except queue.Empty:
            pass
        if self.thread is not None:
            self.thread.join(timeout=1.5)
        if self.thread is None or not self.thread.is_alive():
            self.backend.close()
