"""Windows presentation state for the engine-authorized paged protocol."""
from __future__ import annotations

import json
import hashlib
import re
import uuid
from typing import TextIO

DISCLOSURE_VERSION = "native-local-collection-v1"
CONTACT_PAGE_SIZE = 200
REVIEW_PAGE_SIZE = 1_000
MAX_CONTACT_QUERY_LENGTH = 120
WEBSITE_ORIGIN = "https://amplifai-database-engine.vercel.app"
BRIDGE_PORT = 48751


class ProtocolError(ValueError):
    pass


def count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ProtocolError("Invalid helper count")
    return value


class Session:
    def __init__(self) -> None:
        self.phase = "idle"
        self.status = "Check temporary data before starting. Nothing is collected automatically."
        self.mode = ""
        self.running = False
        self.inspected = False
        self.needs_inspection = True
        self.residue: list[dict] = []
        self.contacts: list[dict] = []
        self.total_contacts = 0
        self.contact_query = ""
        self.contact_cursor = 0
        self.next_contact_cursor: int | None = None
        self.previous_contact_cursors: list[int] = []
        self.pending_contact: tuple[str, int] | None = None
        self.selected_ids: set[int] = set()
        self.selection: tuple[int, ...] = ()
        self.reviewed = False
        self.review_counts = (0, 0, 0)
        self.missing: list[str] = []
        self.progress: float | None = None
        self.handoff_id = ""
        self.pair_code = ""
        self.saved_acknowledged = False
        self.approval: str | None = None
        self._polling = self._pairing = self._finishing = False
        self._pending_review: tuple[int, ...] | None = None
        self._pending_review_id = ""
        self._pending_review_sha = ""
        self._review_next_cursor = 0
        self._review_expected_ack = 0
        self._retired_review_ids: set[str] = set()
        self._pairing_invalidated = False
        self._retired_handoff_id = ""
        self._capture_received = False
        self.selecting_all = False
        self.select_all_visited = 0
        self._select_all_ids: set[int] = set()
        self._select_all_cursor = 0
        self._select_all_scan_id = ""
        self._select_all_cancel = False
        self.destination_intent_id = ""
        self.destination_email = ""
        self.destination_course = ""
        self.destination_requested_approval: bool | None = None
        self.destination_approved = False
        self._failure_status = ""
        self._completed = self._saved_exit = self._stopping = self._failed = False

    @property
    def can_connect(self) -> bool:
        return bool(self.approval and self.inspected and not self.needs_inspection and not self.residue and not self.running)

    @property
    def can_select(self) -> bool:
        return self.running and self.mode == "connect" and self.phase in {"selecting", "reviewing"} and not self._stopping and not self._finishing

    @property
    def can_select_all(self) -> bool:
        return (self.can_select and not self.selecting_all and self.pending_contact is None
                and self._pending_review is None and not self._pairing and not self._polling
                and not self.handoff_id and not self._retired_handoff_id and self.total_contacts > 0)

    @property
    def can_review(self) -> bool:
        return (self.can_select and not self.selecting_all and self._pending_review is None and self.pending_contact is None
                and not self._pairing and not self._polling and not self.handoff_id
                and not self._retired_handoff_id)

    @property
    def can_pair(self) -> bool:
        return (self.running and self.mode == "connect" and self.phase == "reviewing"
                and self.reviewed and bool(self.selection) and not self.saved_acknowledged
                and not self._stopping and not self._finishing and not self._pairing
                and not self._polling and self._pending_review is None
                and self.pending_contact is None and not self.selecting_all and not self._retired_handoff_id)

    def approve(self, checked: bool) -> None:
        self.approval = str(uuid.uuid4()) if checked and self.inspected and not self.needs_inspection and not self.residue and not self.running else None

    def begin(self, mode: str) -> None:
        if self.running or mode not in {"inspect", "clear-residue", "connect"}:
            raise ProtocolError("An operation is already running or unavailable")
        if mode == "connect" and not self.can_connect:
            raise ProtocolError("Fresh collection agreement and clean inspection required")
        if mode == "connect":
            self.contacts = []
            self.total_contacts = 0
            self.contact_query = ""
            self.contact_cursor = 0
            self.next_contact_cursor = None
            self.previous_contact_cursors = []
            self.pending_contact = None
            self.selected_ids.clear()
            self._clear_select_all()
            self.selection = ()
            self.reviewed = self.saved_acknowledged = False
            self.handoff_id = self.pair_code = ""
            self._clear_destination()
            self._saved_exit = self._capture_received = False
        self.approval = None
        self._completed = self._stopping = self._failed = False
        self._polling = self._pairing = self._finishing = self._pairing_invalidated = False
        self._pending_review = None
        self._pending_review_id = self._pending_review_sha = ""
        self._review_next_cursor = self._review_expected_ack = 0
        self._retired_review_ids.clear()
        self._retired_handoff_id = self._failure_status = ""
        self.mode = mode
        self.running = True
        self.inspected = False
        self.needs_inspection = True
        self.progress = None
        self.phase = "connecting" if mode == "connect" else "checking"
        self.status = "Connecting to the iPhone. Handle device Trust prompts yourself." if mode == "connect" else "Checking owned temporary data…"

    def review(self, ids: list[int]) -> dict:
        if (not self.can_review or not isinstance(ids, list) or not 0 < len(ids) <= self.total_contacts
                or len(set(ids)) != len(ids) or any(type(item) is not int for item in ids)
                or set(ids) != self.selected_ids):
            raise ProtocolError("Select available contacts before reviewing")
        self.selection = tuple(sorted(ids))
        self._pending_review = self.selection
        self._pending_review_id = str(uuid.uuid4())
        self._pending_review_sha = hashlib.sha256("".join(f"{item}\n" for item in self.selection).encode("ascii")).hexdigest()
        self._review_next_cursor = self._review_expected_ack = 0
        self.reviewed = self.saved_acknowledged = False
        self.handoff_id = self.pair_code = ""
        self._clear_destination()
        self.phase = "selecting"
        self.status = "Reviewing only the selected contacts and matching metadata…"
        return self._next_review_page()

    def _next_review_page(self) -> dict:
        if self._pending_review is None or self._review_next_cursor >= len(self._pending_review):
            raise ProtocolError("No selection page is pending")
        cursor = self._review_next_cursor
        end = min(cursor + REVIEW_PAGE_SIZE, len(self._pending_review))
        self._review_next_cursor = self._review_expected_ack = end
        return {"action": "review-page", "reviewId": self._pending_review_id,
                "cursor": cursor, "total": len(self._pending_review),
                "ids": list(self._pending_review[cursor:end])}

    def update_visible_selection(self, ids: list[int]) -> dict | None:
        visible = {item["id"] for item in self.contacts}
        if (not self.can_select or self.selecting_all or self.pending_contact is not None or not isinstance(ids, list)
                or any(type(item) is not int for item in ids) or len(set(ids)) != len(ids)
                or not set(ids) <= visible):
            raise ProtocolError("Invalid contact-page selection")
        updated = self.selected_ids - visible | set(ids)
        if updated == self.selected_ids:
            return None
        self.selected_ids = updated
        return self.invalidate_selection(clear_selected=False)

    def request_contacts(self, query: str, cursor: int = 0) -> dict:
        if (not self.can_select or self.selecting_all or self._pending_review is not None or self._pairing
                or not isinstance(query, str) or len(query) > MAX_CONTACT_QUERY_LENGTH
                or type(cursor) is not int or not 0 <= cursor <= self.total_contacts):
            raise ProtocolError("Invalid contact page request")
        self.pending_contact = (query, cursor)
        return {"action": "contacts", "query": query, "cursor": cursor}

    def search_contacts(self, query: str) -> dict:
        command = self.request_contacts(query, 0)
        if query != self.contact_query:
            self.previous_contact_cursors = []
        return command

    def next_contacts(self) -> dict:
        if self.pending_contact is not None or self.next_contact_cursor is None:
            raise ProtocolError("No next contact page")
        self.previous_contact_cursors.append(self.contact_cursor)
        return self.request_contacts(self.contact_query, self.next_contact_cursor)

    def previous_contacts(self) -> dict:
        if self.pending_contact is not None or not self.previous_contact_cursors:
            raise ProtocolError("No previous contact page")
        cursor = self.previous_contact_cursors.pop()
        return self.request_contacts(self.contact_query, cursor)

    def select_all_contacts(self) -> dict:
        if not self.can_select_all:
            raise ProtocolError("Wait for the current review before selecting all contacts")
        self.invalidate_selection(clear_selected=False)
        self.selecting_all = True
        self.select_all_visited = 0
        self._select_all_ids = set()
        self._select_all_cursor = 0
        self._select_all_scan_id = str(uuid.uuid4())
        self._select_all_cancel = False
        self.pending_contact = ("", 0)
        self.status = f"Checking all {self.total_contacts} contacts before changing the selection…"
        return self._next_select_all_page()

    def _next_select_all_page(self) -> dict:
        return {"action": "contacts", "query": "", "cursor": self._select_all_cursor,
                "scanId": self._select_all_scan_id}

    def cancel_select_all(self) -> None:
        if self.selecting_all:
            self._select_all_cancel = True
            self.status = "Stopping the all-contact check. The previous selection stays unchanged."

    def _clear_select_all(self) -> None:
        self.selecting_all = False
        self.select_all_visited = 0
        self._select_all_ids = set()
        self._select_all_cursor = 0
        self._select_all_scan_id = ""
        self._select_all_cancel = False

    def clear_all_contacts(self) -> dict | None:
        if not self.can_select or self.selecting_all or self.pending_contact is not None or self._pending_review is not None:
            raise ProtocolError("Wait for the current contact operation before clearing selection")
        if not self.selected_ids:
            return None
        return self.invalidate_selection()

    def _accept_select_all_page(self, event: dict) -> dict | None:
        if event.get("scanId") != self._select_all_scan_id or event.get("cursor") != self._select_all_cursor:
            raise ProtocolError("Contact scan does not match this review")
        if self._select_all_cancel:
            self.pending_contact = None
            self._clear_select_all()
            self.status = "All-contact check stopped. The previous selection is unchanged; review it again before connecting."
            return None
        old_page = (self.contacts, self.total_contacts, self.contact_query, self.contact_cursor,
                    self.next_contact_cursor, self.pending_contact)
        try:
            self._accept_contact_page(event, first=False)
            page = self.contacts
            next_cursor = self.next_contact_cursor
            expected_size = min(CONTACT_PAGE_SIZE, self.total_contacts - self._select_all_cursor)
            if (len(page) != expected_size or
                    next_cursor != (self._select_all_cursor + expected_size if
                                    self._select_all_cursor + expected_size < self.total_contacts else None)):
                raise ProtocolError("Incomplete all-contact page")
            page_ids = {item["id"] for item in page}
            if page_ids & self._select_all_ids:
                raise ProtocolError("Duplicate contact across pages")
        finally:
            (self.contacts, self.total_contacts, self.contact_query, self.contact_cursor,
             self.next_contact_cursor, self.pending_contact) = old_page
        self._select_all_ids.update(page_ids)
        self.select_all_visited += len(page)
        if next_cursor is not None:
            self._select_all_cursor = next_cursor
            self.pending_contact = ("", next_cursor)
            self.status = f"Checked {self.select_all_visited} of {self.total_contacts} contacts…"
            return self._next_select_all_page()
        if self.select_all_visited != self.total_contacts or len(self._select_all_ids) != self.total_contacts:
            raise ProtocolError("All-contact coverage is incomplete")
        self.selected_ids = self._select_all_ids
        selected = len(self.selected_ids)
        self.pending_contact = None
        self._clear_select_all()
        self.status = f"All {selected} contacts selected across the complete review. Review before connecting."
        return None

    def _clear_destination(self) -> None:
        self.destination_intent_id = ""
        self.destination_email = ""
        self.destination_course = ""
        self.destination_requested_approval = None
        self.destination_approved = False

    @property
    def can_decide_destination(self) -> bool:
        return (self.can_select and self.reviewed and self.handoff_id != ""
                and self.destination_intent_id != "" and self.destination_requested_approval is None
                and not self.destination_approved and set(self.selection) == self.selected_ids)

    def decide_destination(self, approved: bool) -> dict:
        if type(approved) is not bool or not self.can_decide_destination:
            raise ProtocolError("Account destination approval is unavailable")
        self.destination_requested_approval = approved
        return {"action": "destination-decision", "handoffId": self.handoff_id,
                "intentId": self.destination_intent_id, "approved": approved}

    def invalidate_selection(self, *, clear_selected: bool = True) -> dict | None:
        if not self.can_select:
            return None
        revoke = bool(self.handoff_id) or (self._pairing and not self._pairing_invalidated)
        if self._polling:
            self._retired_handoff_id = self.handoff_id
        if self._pairing:
            self._pairing_invalidated = True
        if clear_selected:
            self.selected_ids.clear()
        if self._pending_review_id:
            self._retired_review_ids.add(self._pending_review_id)
        self.selection = ()
        self._pending_review = None
        self._pending_review_id = self._pending_review_sha = ""
        self._review_next_cursor = self._review_expected_ack = 0
        self.reviewed = self.saved_acknowledged = False
        self.handoff_id = self.pair_code = ""
        self._clear_destination()
        self._polling = False
        self.phase = "selecting"
        self.status = "Selection changed. Pending responses cannot approve it; review the current selection again."
        return {"action": "revoke"} if revoke else None

    def pair(self) -> dict:
        if not self.can_pair:
            raise ProtocolError("Review the current selection before pairing")
        self._pairing = True
        self._pairing_invalidated = False
        self.handoff_id = self.pair_code = ""
        self._clear_destination()
        self.saved_acknowledged = False
        self.status = "Preparing an exact-origin, five-minute initial browser pairing…"
        return {"action": "pair"}

    def poll(self) -> dict | None:
        if not self.running or self.mode != "connect" or self.phase != "reviewing" or not self.reviewed or self._finishing or not self.handoff_id or self._stopping or self._polling or self._pairing or self._pending_review is not None:
            return None
        self._polling = True
        return {"action": "handoff-status", "handoffId": self.handoff_id}

    def finish(self) -> dict:
        if (not self.running or self.mode != "connect" or self.phase != "reviewing"
                or not self.reviewed or not self.saved_acknowledged or not self.handoff_id
                or self._stopping or self._finishing or self._pairing or self._polling
                or self._pending_review is not None):
            raise ProtocolError("A matching verified account-save acknowledgment is required")
        self._finishing = True
        self.phase = "finishing"
        return {"action": "finish", "handoffId": self.handoff_id}

    def write_password(self, password: str, sink: TextIO) -> None:
        if not self.running or self.mode != "connect" or self._stopping or self.phase != "password_required" or not isinstance(password, str) or not 0 < len(password) <= 512:
            raise ProtocolError("Enter the requested backup password")
        sink.write(json.dumps({"action": "password", "value": password}, ensure_ascii=False) + "\n")
        sink.flush()
        self.phase = "processing"
        self.status = "Processing locally. Password was sent only through the private helper pipe."

    def request_stop(self) -> dict | None:
        self._stopping = True
        self.approval = None
        self.handoff_id = self.pair_code = ""
        self._clear_destination()
        self._clear_select_all()
        self.inspected = False
        self.needs_inspection = True
        self.phase = "cancelling"
        self.status = "Stop requested. Capture may continue until the helper checks stdin. Cleanup is not yet confirmed."
        return {"action": "disconnect"} if self.mode == "connect" and self._capture_received else None

    def cooperative_failure(self) -> None:
        self.request_stop()
        self._failed = True
        if not self._failure_status:
            self._failure_status = "Helper communication failed. Stop requested; capture may continue until stdin is checked. Cleanup is unconfirmed. Inspect after the helper exits."
        self.status = self._failure_status

    def force_stopped(self) -> None:
        self.request_stop()
        self._failed = True
        self._failure_status = "Windows force stop requested; cleanup is unconfirmed. Check temporary data after the owned helper exits."
        self.status = self._failure_status

    def _accept_contact_page(self, event: dict, *, first: bool) -> None:
        contacts = event.get("contacts")
        total = count(event.get("totalContacts"))
        cursor = event.get("cursor")
        query = event.get("query")
        next_cursor = event.get("nextCursor")
        if (not isinstance(contacts, list) or len(contacts) > CONTACT_PAGE_SIZE
                or type(cursor) is not int or cursor < 0 or cursor > total
                or not isinstance(query, str) or len(query) > MAX_CONTACT_QUERY_LENGTH
                or next_cursor is not None and (type(next_cursor) is not int
                                                or not cursor < next_cursor < total)
                or first and (cursor != 0 or query != "" or total < len(contacts)
                              or len(contacts) < min(CONTACT_PAGE_SIZE, total)
                              or (next_cursor is None) != (total <= CONTACT_PAGE_SIZE))
                or not first and (self.pending_contact != (query, cursor)
                                  or total != self.total_contacts)):
            raise ProtocolError("Invalid contact page")
        seen: set[int] = set()
        for item in contacts:
            if (not isinstance(item, dict) or type(item.get("id")) is not int
                    or item["id"] < 0 or item["id"] in seen
                    or not isinstance(item.get("name"), str) or len(item["name"]) > 240):
                raise ProtocolError("Invalid contact preview")
            phone_count = count(item.get("phoneCount"))
            ends = item.get("phoneEnds")
            if (phone_count > 20 or not isinstance(ends, list) or len(ends) != phone_count
                    or any(not isinstance(end, str) or not re.fullmatch(r"[0-9]{4}", end)
                           for end in ends)):
                raise ProtocolError("Invalid contact preview")
            seen.add(item["id"])
        self.contacts = contacts
        self.total_contacts = total
        self.contact_query = query
        self.contact_cursor = cursor
        self.next_contact_cursor = next_cursor
        self.pending_contact = None

    def handle(self, event: dict) -> dict | None:
        if not self.running or not isinstance(event, dict):
            raise ProtocolError("Unexpected helper event")
        kind = event.get("kind")
        if not isinstance(kind, str):
            raise ProtocolError("Invalid helper event kind")
        if self._stopping and kind != "error":
            return None
        if kind == "progress":
            value = event.get("value")
            if type(value) not in {float, int} or not 0 <= value <= 100:
                raise ProtocolError("Invalid progress")
            self.progress = float(value)
        elif kind == "state":
            state = event.get("state")
            if self.mode != "connect" or not isinstance(state, str):
                raise ProtocolError("Unexpected helper state")
            if state == "completed":
                if not self._finishing or not self.saved_acknowledged or self._stopping:
                    raise ProtocolError("Unacknowledged completion")
                self._completed = True
            elif state in {"connecting", "processing", "password_required"}:
                self.phase = state
                self.status = {"connecting": "Unlock your iPhone and review its Trust prompt.", "processing": "Reading selected relationship context locally; cleanup is still in progress.", "password_required": "Enter the encrypted backup password. It is not logged or persisted."}[state]
            elif state not in {"disconnected", "cancelled", "pairing_revoked"}:
                raise ProtocolError("Unknown helper state")
        elif kind == "capture":
            if self.mode != "connect" or self._capture_received:
                raise ProtocolError("Invalid contact preview")
            self._accept_contact_page(event, first=True)
            count(event.get("availableCalls"))
            count(event.get("availableMessages"))
            missing = event.get("missing")
            if not isinstance(missing, list) or any(item not in {"contacts", "calls", "messages"} for item in missing):
                raise ProtocolError("Invalid source availability")
            self._capture_received = True
            self.phase = "selecting"
            self.status = "Select contacts, then review matching call and message context. No account data has been saved."
        elif kind == "contacts":
            if self.mode != "connect" or not self._capture_received:
                raise ProtocolError("Unexpected contact page")
            if event.get("scanId") is not None:
                if not self.selecting_all:
                    return None
                return self._accept_select_all_page(event)
            if self.selecting_all:
                raise ProtocolError("Ordinary contact page arrived during all-contact check")
            # A later search may have superseded an in-flight response; never
            # let it move the visible cursor or alter reviewed selection.
            if self.pending_contact != (event.get("query"), event.get("cursor")):
                return None
            self._accept_contact_page(event, first=False)
        elif kind == "review-page":
            if isinstance(event.get("reviewId"), str) and event["reviewId"] in self._retired_review_ids:
                return None
            if (self._pending_review is None or event.get("reviewId") != self._pending_review_id
                    or type(event.get("nextCursor")) is not int
                    or event["nextCursor"] != self._review_expected_ack):
                raise ProtocolError("Selection page acknowledgment does not match")
            if self._review_expected_ack < len(self._pending_review):
                return self._next_review_page()
        elif kind == "review":
            if isinstance(event.get("reviewId"), str) and event["reviewId"] in self._retired_review_ids:
                return None
            counts = tuple(count(event.get(field)) for field in ("selected_contacts", "matched_calls", "matched_messages"))
            pending = self._pending_review
            if (pending is None or counts[0] != len(pending)
                    or self._review_expected_ack != len(pending)
                    or event.get("reviewId") != self._pending_review_id
                    or event.get("selection_sha256") != self._pending_review_sha):
                raise ProtocolError("Review does not match selection")
            missing = event.get("missing_sources")
            if not isinstance(missing, list) or any(not isinstance(item, str) or item not in {"contacts", "calls", "messages"} for item in missing):
                raise ProtocolError("Invalid source availability")
            self._pending_review = None
            self.review_counts = counts
            self.missing = missing
            self.reviewed = True
            self.phase = "reviewing"
            self.status = "Local review ready. Creating a pairing does not approve account saving."
        elif kind == "pairing":
            code, digest = event.get("pairCode"), event.get("payloadSha256")
            if not self._pairing or not isinstance(code, str) or not re.fullmatch(r"[0-9]{10}", code) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ProtocolError("Invalid pairing")
            try:
                binding = str(uuid.UUID(event["handoffId"]))
            except (KeyError, ValueError, TypeError, AttributeError) as exc:
                raise ProtocolError("Invalid handoff binding") from exc
            if count(event.get("port")) != BRIDGE_PORT or type(event.get("expiresInSeconds")) is not int or event["expiresInSeconds"] != 300:
                raise ProtocolError("Invalid pairing endpoint")
            self._pairing = False
            if self._pairing_invalidated:
                self._pairing_invalidated = False
                return None  # Revoke was queued behind this obsolete pair command.
            if not self.reviewed:
                raise ProtocolError("Pairing does not match reviewed selection")
            self.handoff_id, self.pair_code = binding, code
            self.status = "Use Connect in your signed-in account. The temporary code remains a manual recovery option; received is not saved."
        elif kind == "destination":
            if (event.get("handoffId") != self.handoff_id or not self.handoff_id
                    or not self.reviewed or set(self.selection) != self.selected_ids):
                raise ProtocolError("Account destination does not match this reviewed handoff")
            intent_id = event.get("intentId")
            try:
                valid_id = isinstance(intent_id, str) and str(uuid.UUID(intent_id)) == intent_id
            except (ValueError, AttributeError):
                valid_id = False
            if not valid_id:
                raise ProtocolError("Invalid account destination intent")
            if event.get("state") == "pending":
                email, course = event.get("email"), event.get("course")
                if (not isinstance(email, str) or not 0 < len(email) <= 254 or "@" not in email
                        or not isinstance(course, str) or not 0 < len(course) <= 160
                        or any(ord(char) < 32 for char in email + course)
                        or self.destination_intent_id not in ("", intent_id)
                        or self.destination_intent_id == intent_id and
                        (self.destination_email != email or self.destination_course != course)):
                    raise ProtocolError("Invalid server-confirmed account destination")
                self.destination_intent_id = intent_id
                self.destination_email = email
                self.destination_course = course
                if self.destination_requested_approval is None:
                    self.status = "Confirm the server-verified signed-in destination in this companion. Connecting does not save sources."
            elif event.get("state") in {"approved", "denied"}:
                if (intent_id != self.destination_intent_id or self.destination_requested_approval is None
                        or (event["state"] == "approved") != self.destination_requested_approval):
                    raise ProtocolError("Account destination decision does not match")
                if event["state"] == "approved":
                    self.destination_approved = True
                    self.status = "Destination approved here. Waiting for the account browser; each source still requires Save approval."
                else:
                    self._clear_destination()
                    self.handoff_id = self.pair_code = ""
                    self.reviewed = False
                    self.status = "Destination denied. This pairing was revoked; review and create a new connection if wanted."
                    return {"action": "revoke"}
            else:
                raise ProtocolError("Invalid account destination state")
        elif kind == "handoff":
            state = event.get("state")
            if not isinstance(state, str) or state not in {"saved", "saved_pending_browser_receipt", "received", "expired", "waiting"}:
                raise ProtocolError("Invalid handoff state")
            if self._retired_handoff_id and event.get("handoffId") == self._retired_handoff_id:
                self._retired_handoff_id = ""
                return None  # Expected stale acknowledgment cannot finish this selection.
            if not self._polling or event.get("handoffId") != self.handoff_id:
                raise ProtocolError("Handoff acknowledgment does not match")
            self._polling = False
            if state == "saved":
                self.saved_acknowledged = True
                self.status = "Matching account save acknowledged. Waiting for safe helper completion."
            elif state == "received":
                self.status = "Browser received the preview, not a saved account receipt. Keep this review open."
            elif state == "saved_pending_browser_receipt":
                self.status = "Account parts are saved; waiting for the browser's verified final receipt. Keep this review open."
            elif state == "expired":
                self.handoff_id = self.pair_code = ""
                self._clear_destination()
                self.status = "Pairing expired. Your local review remains; create a fresh pairing."
        elif kind == "residue":
            items = event.get("sessions")
            if self.mode != "inspect" or not isinstance(items, list):
                raise ProtocolError("Invalid cleanup inspection")
            for item in items:
                if not isinstance(item, dict) or not re.fullmatch(r"session-[0-9a-f]{32}", str(item.get("name", ""))):
                    raise ProtocolError("Invalid cleanup target")
                count(item.get("bytes"))
            self.residue = items
            self.inspected = True
        elif kind == "cleared":
            if self.mode != "clear-residue":
                raise ProtocolError("Unexpected cleanup result")
            self.status = f"Removed {count(event.get('count'))} marked abandoned sessions. Inspect again to confirm."
        elif kind == "connection":
            if event.get("transport") not in {"usb", "wifi"}:
                raise ProtocolError("Invalid connection transport")
        elif kind == "error":
            self._failed = True
            self.needs_inspection = True
            self.phase = "error"
            code = event.get("code")
            messages = {
                "workspace_marker": "A private empty or marker-only root may remain. No phone data was written by this attempt. Collection is paused; this unverified root is not automatically removed.",
                "workspace_unsafe": "Private storage could not be verified. Unverified directories are not repaired or automatically removed. Collection is stopped; cleanup is unconfirmed.",
                "workspace_unavailable": "Private storage is unavailable. Collection completion is not assumed; check the package/runtime and inspect after the helper stops.",
            }
            self._failure_status = messages.get(code, "Collection or handoff failed. Completion and cleanup are unconfirmed. Check device permissions and inspect temporary data after the helper stops.") if isinstance(code, str) else "Helper communication failed; completion and cleanup are unconfirmed."
            self.status = self._failure_status
        else:
            raise ProtocolError("Unknown helper event")
        return None

    def exited(self, code: int) -> None:
        if type(code) is not int:
            raise ProtocolError("Invalid helper exit result")
        self.running = False
        self.approval = None
        if self.mode == "inspect" and code == 0 and self.inspected and not self._failed:
            self.needs_inspection = False
            self.phase = "completed" if self._saved_exit and not self.residue else "idle"
            self.status = "Account save acknowledged; helper exited and no abandoned temporary sessions were found." if self.phase == "completed" else "Temporary data checked. Review collection agreement to connect." if not self.residue else "Marked temporary phone data remains. Explicitly remove it before another collection."
        else:
            self._saved_exit = self.mode == "connect" and code == 0 and self._completed and self.saved_acknowledged and not self._failed and not self._stopping
            self.needs_inspection = True
            self.inspected = False
            self.phase = "checking" if self._saved_exit else "error"
            self.status = "Account save acknowledged; check temporary data before completion." if self._saved_exit else self._failure_status or "Helper stopped; cleanup is unconfirmed. Check temporary data before collecting again."
