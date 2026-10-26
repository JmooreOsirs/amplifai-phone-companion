"""Memory-only setup and prepared-run authority; helper actions remain on the UI thread."""
from __future__ import annotations

import http.client
import queue
import socket
import ssl
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

from native_setup import (
    APPROVE_PATH, CLAIM_PATH, DISCLOSURE, ORIGIN, RUN_SECONDS, SETUP_SECONDS, ApprovalRequest, ApprovedRun,
    ClaimRequest, ClientIdentity, SetupClaim, SetupLink, SetupProtocolError, categories, digest,
    identifier, json_object, request_body, strict_object, timestamp,
)

STATUS_PATH = "/api/v1/phone-run-approvals/preparation/status"
START_PATH = "/api/v1/phone-run-approvals/preparation/start"
BIND_PATH = "/api/v1/phone-run-approvals/preparation/bind"
VERIFY_PATH = "/api/v1/phone-run-approvals/verify-intent"
ROUTES = {CLAIM_PATH: (8192, (200,)), APPROVE_PATH: (16384, (200, 201)),
          STATUS_PATH: (2048, (200,)), START_PATH: (2048, (200,)),
          BIND_PATH: (2048, (200,)), VERIFY_PATH: (2048, (200,))}
REQUEST_SECONDS = 20
REFRESH_SECONDS = 15
CLOCK_SKEW_SECONDS = 60
ACCOUNT_URL = ORIGIN + "/phone/account"


def account_continue_url(enrollment_id, run_id):
    return ACCOUNT_URL + "?" + urlencode({"continue": "reviewed", "enrollmentId": identifier(enrollment_id),
                                            "week": "phone", "phoneRunId": identifier(run_id)})


def account_control_url(run_id, enrollment_id):
    return ACCOUNT_URL + "?" + urlencode({"phoneRunControl": identifier(run_id),
                                           "enrollmentId": identifier(enrollment_id), "week": "phone"})


@dataclass(frozen=True)
class CompletionBinding:
    """Non-secret return binding; the account server still verifies a durable save."""
    enrollment_id: str
    handoff_id: str
    payload_sha256: str

    def __post_init__(self):
        identifier(self.enrollment_id)
        identifier(self.handoff_id)
        digest(self.payload_sha256)

    @property
    def url(self):
        return ACCOUNT_URL + "?" + urlencode({"enrollmentId": self.enrollment_id, "week": "phone",
            "completedHandoffId": self.handoff_id, "completedPayloadSha256": self.payload_sha256})


@dataclass(frozen=True, repr=False)
class Operation:
    action: str
    path: str
    body: bytes
    token: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True, repr=False)
class Outcome:
    state: str
    body: bytes = b""


class NativeTransport:
    """Fixed TLS POSTs: no proxy, cookies, redirects, credentials or response logging."""
    def post(self, operation: Operation) -> Outcome:
        if operation.path not in ROUTES or type(operation.body) is not bytes or not 0 < len(operation.body) <= 4096:
            return Outcome("uncertain")
        maximum, accepted = ROUTES[operation.path]
        try:
            connection = http.client.HTTPSConnection(urlsplit(ORIGIN).hostname, timeout=10,
                                                      context=ssl.create_default_context())
        except (OSError, ValueError):
            return Outcome("uncertain")
        expired = threading.Event()

        def interrupt():
            expired.set()
            sock = connection.sock
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # A disconnected socket is already unable to grant authority.
            connection.close()

        watchdog = threading.Timer(REQUEST_SECONDS, interrupt)
        watchdog.daemon = True
        try:
            watchdog.start()
        except RuntimeError:
            connection.close()
            return Outcome("uncertain")
        try:
            connection.request("POST", operation.path, operation.body,
                               {"Content-Type": "application/json", "Accept": "application/json", "Cache-Control": "no-store"})
            response = connection.getresponse()
            if response.status in (400, 401, 403, 404, 409, 410):
                return Outcome("refused")
            if response.status not in accepted:
                return Outcome("uncertain")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            length = response.getheader("Content-Length")
            if (content_type != "application/json" or response.getheader("Content-Encoding", "identity") != "identity"
                    or length is not None and (not length.isdecimal() or int(length) > maximum)):
                return Outcome("uncertain")
            body = response.read(maximum + 1)
            if (expired.is_set() or not 0 < len(body) <= maximum
                    or length is not None and len(body) != int(length)):
                return Outcome("uncertain")
            return Outcome("accepted", body)
        except (OSError, http.client.HTTPException, ValueError):
            return Outcome("uncertain")
        finally:
            watchdog.cancel()
            connection.close()


class AuthorityWorker:
    """At most one transport thread and one pending result, including DNS stalls."""
    def __init__(self):
        self.transport = NativeTransport()
        self.results = queue.Queue(maxsize=1)
        self.thread = None
        self.lock = threading.Lock()
        self.busy = False
        self.closed = threading.Event()

    @property
    def idle(self):
        with self.lock:
            return not self.busy and self.results.empty()

    def submit(self, operation):
        with self.lock:
            if self.busy or not self.results.empty() or self.closed.is_set():
                return False
            self.busy = True
        def work():
            result = self.transport.post(operation)
            with self.lock:
                if not self.closed.is_set():
                    self.results.put_nowait((operation, result))
                self.busy = False
        self.thread = threading.Thread(target=work, name="native-run-authority", daemon=True)
        try:
            self.thread.start()
        except RuntimeError:
            with self.lock:
                self.busy = False
            return False
        return True

    def close(self):
        self.closed.set()


def _destination(value, run):
    expected = {"approvalId": run.approval_id, "enrollmentId": run.scope.enrollment_id,
                "destinationEmail": run.scope.email, "destinationCourse": run.scope.course,
                "categories": list(run.scope.categories)}
    if any(value.get(key) != item for key, item in expected.items()):
        raise SetupProtocolError("Run destination changed")
    categories(value["categories"])


def preparation(body, run, operation_id):
    value = strict_object(json_object(body, 2048), ("protocol", "approvalId", "enrollmentId", "destinationEmail",
        "destinationCourse", "categories", "disclosureVersion", "expiresAt", "state", "operation"))
    _destination(value, run)
    if (value["protocol"] != "phone-start-v1" or value["disclosureVersion"] != DISCLOSURE["version"]
            or timestamp(value["expiresAt"]) != run.expires_at
            or value["state"] not in ("current", "replacement_required", "expired", "revoked")):
        raise SetupProtocolError("Run authority changed")
    if value["state"] != "current":
        return value["state"]
    if value["operation"] is not None:
        operation = strict_object(value["operation"], ("operationId", "admittedAt"))
        if identifier(operation["operationId"]) != operation_id or not run.approved_at <= timestamp(operation["admittedAt"]) < run.expires_at:
            raise SetupProtocolError("Collection operation changed")
    elif operation_id is not None:
        raise SetupProtocolError("Collection operation is missing")
    return value["state"]


def binding(body, run, operation_id):
    value = strict_object(json_object(body, 2048), ("protocol", "operationId", "approvalId", "enrollmentId",
                                                   "destinationEmail", "destinationCourse", "categories"))
    _destination(value, run)
    if value["protocol"] != "phone-start-v1" or value["operationId"] != operation_id:
        raise SetupProtocolError("Handoff operation changed")


def verified_destination(body, run):
    value = strict_object(json_object(body, 2048), ("approvalId", "enrollmentId", "destinationEmail", "destinationCourse", "categories"))
    _destination(value, run)


class NativeRun:
    def __init__(self, client: ClientIdentity, *, now=None, monotonic=None):
        self.client = client
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic or time.monotonic
        self.phase = "none"
        self.message = ""
        self.authority = "paused"
        self.claim_request = self.claim = self.approval_request = self.run = None
        self.pending = None
        self.deadline = 0.0
        self.next_refresh = 0.0
        self.claim_started = None
        self.approval_started = None
        self.claim_started_monotonic = self.approval_started_monotonic = 0.0
        self.claim_deadline = self.run_deadline = 0.0
        self.accepted = False
        self.operation_id = None
        self.start_body = self.bind_body = None
        self.started = False
        self.continued_selection = None
        self.handoff_id = self.payload_sha256 = self.intent_id = ""
        self.completion = None
        self.stop_requested = False
        self.backup_setting = "not_checked"
        self.call_history_preparation = None

    @property
    def active(self):
        return self.phase != "none"

    @property
    def current(self):
        return (self.run is not None and self.run.expires_at > self.now() and self.monotonic() < self.run_deadline
                and self.phase not in ("refused", "cancelled", "failed", "complete", "handoff_expired"))

    @property
    def claim_current(self):
        return (self.claim is not None and self.claim.expires_at > self.now()
                and self.monotonic() < self.claim_deadline)

    @property
    def account_url(self):
        if self.completion is not None:
            return self.completion.url
        if self.run is not None:
            if self.phase == "handoff_expired" and self.handoff_id:
                return CompletionBinding(self.run.scope.enrollment_id, self.handoff_id, self.payload_sha256).url
            return account_control_url(self.run.run_id, self.run.scope.enrollment_id)
        return ACCOUNT_URL

    @property
    def continue_url(self):
        if self.current and self.phase == "bound" and self.handoff_id:
            return account_continue_url(self.run.scope.enrollment_id, self.run.run_id)
        return None

    def record_saved(self, handoff_id):
        """Called only after Session accepts the helper's matching saved ACK."""
        if self.completion is not None and handoff_id == self.completion.handoff_id:
            return
        if self.run is None or handoff_id != self.handoff_id or not self.bind_body:
            raise SetupProtocolError("Saved receipt does not match the reviewed binding")
        self.completion = CompletionBinding(self.run.scope.enrollment_id, handoff_id, self.payload_sha256)
        self.claim_request = self.claim = self.approval_request = self.run = None
        self.pending = self.start_body = self.bind_body = self.operation_id = None
        self.continued_selection = None
        self.handoff_id = self.payload_sha256 = self.intent_id = ""
        self.accepted = self.started = self.stop_requested = False
        self.authority, self.phase, self.message = "paused", "complete", ""

    def _send(self, action, path, body):
        if self.pending is not None:
            raise SetupProtocolError("An authority operation is pending")
        self.pending = Operation(action, path, body)
        self.deadline = self.monotonic() + REQUEST_SECONDS
        return self.pending

    def claim_link(self, url):
        if self.active:
            raise SetupProtocolError("Finish or cancel the current setup first")
        self.claim_request = ClaimRequest.create(SetupLink.parse(url), self.client)
        self.claim_started = self.now()
        self.claim_started_monotonic = self.monotonic()
        self.phase = "claiming"
        return self._send("claim", CLAIM_PATH, self.claim_request.body)

    def agree(self, accepted):
        self.accepted = bool(type(accepted) is bool and accepted and self.phase == "pending_approval"
                             and self.claim_current)

    def approve(self):
        if not self.accepted or self.phase != "pending_approval" or not self.claim_current:
            raise SetupProtocolError("A current native agreement is required")
        self.approval_request = ApprovalRequest.create(self.claim, self.claim_request)
        self.approval_started = self.now()
        self.approval_started_monotonic = self.monotonic()
        self.phase = "approving"
        return self._send("approve", APPROVE_PATH, self.approval_request.body)

    def check_backup_setting(self):
        if not self.current or self.started or self.phase != "prepared" or self.pending is not None:
            raise SetupProtocolError("A current unstarted prepared run is required")
        self.backup_setting = "checking"
        self.call_history_preparation = None
        self.message = "Checking the connected iPhone's backup encryption setting. No backup has started."

    def checked_backup_setting(self, encrypted):
        if not self.current or self.phase != "prepared" or self.backup_setting != "checking":
            return
        self.backup_setting = "on" if encrypted is True else "off" if encrypted is False else "unknown"
        label = "Off" if encrypted is False else "Unknown"
        self.message = ("Backup encryption is On. Call history will be checked during collection; complete history is not guaranteed."
            if encrypted is True else
            f"Backup encryption is {label}. Follow the Apple Devices guide and check again, or continue with available data without confirmed call history.")

    def start(self, *, available_data=False):
        if not self.current or self.started or self.phase != "prepared":
            raise SetupProtocolError("A current unstarted prepared run is required")
        if available_data is False and self.backup_setting == "on":
            self.call_history_preparation = "confirmedEncryptedBackup"
        elif available_data is True and self.backup_setting in ("off", "unknown"):
            self.call_history_preparation = "continueWithoutConfirmedCoverage"
        else:
            raise SetupProtocolError("Check this iPhone's backup setting before starting collection")
        self.operation_id = str(uuid.uuid4())
        self.start_body = request_body({**self._run_fields(), "expectedApprovalId": self.run.approval_id,
                                       "operationId": self.operation_id})
        self.phase = "starting"
        self.message = ""
        return self._send("start", START_PATH, self.start_body)

    def _run_fields(self):
        return {"runId": self.run.run_id, "capability": self.run.capability}

    def refresh(self, purpose="status"):
        if not self.current:
            self._refused()
            return None
        self.authority = "refreshing" if self.authority == "current" else "paused"
        return self._send(purpose, STATUS_PATH, request_body(self._run_fields()))

    def continue_review(self, selection):
        if (not self.current or not self.started or self.handoff_id or self.phase not in ("collecting", "continue_uncertain")
                or type(selection) is not tuple or not selection or len(set(selection)) != len(selection)
                or any(type(value) is not int or value < 0 for value in selection)):
            raise SetupProtocolError("Review the current contact selection before continuing")
        if self.continued_selection is not None and self.continued_selection != selection:
            raise SetupProtocolError("The continued selection changed")
        self.continued_selection = selection
        return self.refresh("status_continue")

    def pair_received(self, handoff_id, payload_sha256):
        if self.phase != "awaiting_pair" or not self.current or not self.started:
            raise SetupProtocolError("No reviewed Continue is pending")
        self.handoff_id, self.payload_sha256 = identifier(handoff_id), digest(payload_sha256)
        self.bind_body = request_body({**self._run_fields(), "expectedApprovalId": self.run.approval_id,
            "operationId": self.operation_id, "handoffId": self.handoff_id, "payloadSha256": self.payload_sha256})
        self.authority = "paused"
        self.phase = "binding"
        return self.refresh("status_bind")

    def verify(self, intent_id, email, course):
        if not self.current or self.phase != "bound" or not self.handoff_id:
            raise SetupProtocolError("The reviewed handoff is not bound")
        if email != self.run.scope.email or course != self.run.scope.course:
            self.authority = "paused"
            self.message = "Return to the account and course approved for this collection."
            return None
        self.intent_id = identifier(intent_id)
        return self.refresh("status_verify")

    def supersede_intent(self):
        # A newer authenticated intent invalidates every outstanding destination response.
        if self.pending is not None and self.pending.action in ("status_verify", "verify"):
            self.pending = None
        self.intent_id = ""
        self.authority = "paused"

    def retry(self):
        if self.phase == "claim_uncertain":
            self.phase = "claiming"
            return self._send("claim", CLAIM_PATH, self.claim_request.body)
        if self.phase == "approval_uncertain":
            self.phase = "approving"
            return self._send("approve", APPROVE_PATH, self.approval_request.body)
        if self.phase == "start_uncertain" and self.current and not self.started:
            self.phase = "starting"
            return self._send("start", START_PATH, self.start_body)
        if self.phase == "bind_uncertain" and self.current:
            self.phase = "binding"
            return self.refresh("status_bind")
        if self.phase == "continue_uncertain" and self.current:
            return self.continue_review(self.continued_selection)
        if self.phase == "bound" and self.intent_id and self.current:
            return self.refresh("status_verify")
        if self.current:
            return self.refresh()
        raise SetupProtocolError("No recoverable authority request")

    def cancel(self):
        # Browser-session revoke is unavailable here. Stop locally and return to that account tab.
        self.pending = None
        self.authority = "paused"
        self.phase = "cancelled"
        self.stop_requested = self.started
        self.message = "Stop requested locally. Account Save is not confirmed."

    def fail(self):
        if self.phase in ("cancelled", "failed", "refused", "handoff_expired", "complete"):
            return
        self.pending = None
        self.authority = "paused"
        self.phase = "failed"
        self.stop_requested = self.started
        self.message = "This collection could not finish. Account Save is not confirmed. Source diagnostic details remain available here."

    def handoff_expired(self):
        self.pending = None
        self.authority = "paused"
        self.phase = "handoff_expired"
        self.message = "This reviewed handoff expired. Recover any completed save in your account, or cancel locally and start a new setup. This selection cannot be rebound."

    def _refused(self):
        self.pending = None
        self.authority = "paused"
        self.phase = "refused"
        self.stop_requested = self.started
        self.message = "This setup or collection is no longer current. Return to your account for a new setup."

    def _uncertain(self, action):
        self.authority = "paused"
        self.phase = {"claim": "claim_uncertain", "approve": "approval_uncertain", "start": "start_uncertain",
                      "bind": "bind_uncertain", "status_bind": "bind_uncertain",
                      "status_continue": "continue_uncertain"}.get(action, self.phase)
        self.message = "Confirmation is unavailable. Check your connection and PC clock, then retry. No new collection or transfer is authorized until confirmation."

    def timeout(self):
        if self.run is not None and not self.current and self.phase not in ("cancelled", "failed", "refused", "complete", "handoff_expired"):
            self._refused()
            return True
        if self.pending is not None and self.monotonic() >= self.deadline:
            action = self.pending.action
            self.pending = None
            self._uncertain(action)
            return True
        return False

    def complete(self, operation: Operation, outcome: Outcome):
        """Return one UI action or the next bounded request; stale callbacks do nothing."""
        if operation != self.pending:
            return None
        if self.monotonic() >= self.deadline:
            self.timeout()
            return None
        self.pending = None
        if outcome.state == "refused":
            self._refused()
            return None
        if outcome.state != "accepted":
            self._uncertain(operation.action)
            return None
        try:
            action = operation.action
            if action == "claim":
                claim = SetupClaim.parse(outcome.body, self.claim_request)
                if not self.now() < claim.expires_at <= self.claim_started + timedelta(seconds=SETUP_SECONDS + CLOCK_SKEW_SECONDS):
                    raise SetupProtocolError("Setup has expired or changed its lifetime")
                self.claim_deadline = min(self.claim_started_monotonic + SETUP_SECONDS,
                    self.monotonic() + (claim.expires_at - self.now()).total_seconds())
                if self.monotonic() >= self.claim_deadline:
                    raise SetupProtocolError("Setup has expired")
                self.claim, self.phase, self.accepted = claim, "pending_approval", False
            elif action == "approve":
                run = ApprovedRun.parse(outcome.body, self.approval_request)
                if (run.expires_at <= self.now()
                        or run.approved_at < self.approval_started - timedelta(seconds=CLOCK_SKEW_SECONDS)
                        or run.approved_at > self.now() + timedelta(seconds=CLOCK_SKEW_SECONDS)):
                    raise SetupProtocolError("Approval cannot be confirmed")
                # Request timestamps allow bounded skew. Neither wall-clock rollback nor
                # replay can renew the maximum lifetime anchored to the original approval.
                lifetime = min(RUN_SECONDS, (run.expires_at - run.approved_at).total_seconds())
                self.run_deadline = min(self.approval_started_monotonic + lifetime,
                    self.monotonic() + (run.expires_at - self.now()).total_seconds())
                if self.monotonic() >= self.run_deadline:
                    raise SetupProtocolError("Prepared run has expired")
                self.run, self.phase = run, "prepared"
            elif action == "start":
                if not self.current or preparation(outcome.body, self.run, self.operation_id) != "current":
                    self._refused()
                    return None
                self.started, self.phase, self.authority = True, "collecting", "current"
                self.next_refresh = self.monotonic() + REFRESH_SECONDS
                self.message = ""
                return "start"
            elif action.startswith("status"):
                if not self.current or preparation(outcome.body, self.run, self.operation_id if self.started else None) != "current":
                    self._refused()
                    return None
                self.authority = "current"
                self.next_refresh = self.monotonic() + REFRESH_SECONDS
                self.message = ""
                if action == "status_continue":
                    self.phase = "awaiting_pair"
                    return "pair"
                if action == "status_bind":
                    return self._send("bind", BIND_PATH, self.bind_body)
                if action == "status_verify":
                    return self._send("verify", VERIFY_PATH, request_body({**self._run_fields(),
                        "handoffId": self.handoff_id, "payloadSha256": self.payload_sha256,
                        "intentId": self.intent_id, "expectedApprovalId": self.run.approval_id}))
            elif action == "bind":
                if not self.current:
                    self._refused()
                    return None
                binding(outcome.body, self.run, self.operation_id)
                self.phase, self.authority = "bound", "current"
                self.message = ""
                return "bound"
            elif action == "verify":
                if not self.current or self.phase != "bound":
                    self._refused()
                    return None
                verified_destination(outcome.body, self.run)
                self.authority = "current"
                self.message = ""
                return "destination_verified"
            else:
                raise SetupProtocolError("Unexpected authority operation")
            self.message = ""
        except (SetupProtocolError, TypeError, KeyError):
            self._uncertain(operation.action)
        return None
