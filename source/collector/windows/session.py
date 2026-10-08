"""In-memory Windows presentation for the existing, engine-authorized protocol."""
from __future__ import annotations

import json
import re
import uuid
from typing import TextIO

DISCLOSURE_VERSION = "native-local-collection-v1"
MAX_CONTACTS = 25_000
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
        self._pairing_invalidated = False
        self._retired_handoff_id = ""
        self._capture_received = False
        self._failure_status = ""
        self._completed = self._saved_exit = self._stopping = self._failed = False

    @property
    def can_connect(self) -> bool:
        return bool(self.approval and self.inspected and not self.needs_inspection and not self.residue and not self.running)

    @property
    def can_select(self) -> bool:
        return self.running and self.mode == "connect" and self.phase in {"selecting", "reviewing"} and not self._stopping and not self._finishing

    @property
    def can_review(self) -> bool:
        return self.can_select and self._pending_review is None and not self._pairing and not self._polling and not self._retired_handoff_id

    @property
    def can_pair(self) -> bool:
        return self.can_review and self.reviewed

    def approve(self, checked: bool) -> None:
        self.approval = str(uuid.uuid4()) if checked and self.inspected and not self.needs_inspection and not self.residue and not self.running else None

    def begin(self, mode: str) -> None:
        if self.running or mode not in {"inspect", "clear-residue", "connect"}:
            raise ProtocolError("An operation is already running or unavailable")
        if mode == "connect" and not self.can_connect:
            raise ProtocolError("Fresh collection agreement and clean inspection required")
        if mode == "connect":
            self.contacts = []
            self.selection = ()
            self.reviewed = self.saved_acknowledged = False
            self.handoff_id = self.pair_code = ""
            self._saved_exit = self._capture_received = False
        self.approval = None
        self._completed = self._stopping = self._failed = False
        self._polling = self._pairing = self._finishing = self._pairing_invalidated = False
        self._pending_review = None
        self._retired_handoff_id = self._failure_status = ""
        self.mode = mode
        self.running = True
        self.inspected = False
        self.needs_inspection = True
        self.progress = None
        self.phase = "connecting" if mode == "connect" else "checking"
        self.status = "Connecting to the iPhone. Handle device Trust prompts yourself." if mode == "connect" else "Checking owned temporary data…"

    def review(self, ids: list[int]) -> dict:
        available = {item["id"] for item in self.contacts}
        if not self.can_review or not isinstance(ids, list) or not 0 < len(ids) <= MAX_CONTACTS or any(type(item) is not int or item not in available for item in ids) or len(set(ids)) != len(ids):
            raise ProtocolError("Select available contacts before reviewing")
        self.selection = tuple(ids)
        self._pending_review = self.selection
        self.reviewed = self.saved_acknowledged = False
        self.handoff_id = self.pair_code = ""
        self.phase = "selecting"
        self.status = "Reviewing only the selected contacts and matching metadata…"
        return {"action": "review", "ids": ids}

    def invalidate_selection(self) -> dict | None:
        if not self.can_select:
            return None
        revoke = bool(self.handoff_id) or (self._pairing and not self._pairing_invalidated)
        if self._polling:
            self._retired_handoff_id = self.handoff_id
        if self._pairing:
            self._pairing_invalidated = True
        self.selection = ()
        self.reviewed = self.saved_acknowledged = False
        self.handoff_id = self.pair_code = ""
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
        self.saved_acknowledged = False
        self.status = "Preparing an exact-origin, five-minute browser handoff…"
        return {"action": "pair"}

    def poll(self) -> dict | None:
        if not self.running or self.mode != "connect" or self.phase != "reviewing" or not self.reviewed or self._finishing or not self.handoff_id or self._stopping or self._polling or self._pairing or self._pending_review is not None:
            return None
        self._polling = True
        return {"action": "handoff-status", "handoffId": self.handoff_id}

    def finish(self) -> dict:
        if not self.saved_acknowledged or not self.can_pair or not self.handoff_id:
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
        self.inspected = False
        self.needs_inspection = True
        self.phase = "cancelling"
        self.status = "Stop requested. Capture may continue until the helper checks stdin. Cleanup is not yet confirmed."
        return {"action": "disconnect"} if self.mode == "connect" and self.contacts else None

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

    def handle(self, event: dict) -> None:
        if not self.running or not isinstance(event, dict):
            raise ProtocolError("Unexpected helper event")
        kind = event.get("kind")
        if not isinstance(kind, str):
            raise ProtocolError("Invalid helper event kind")
        if self._stopping and kind != "error":
            return
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
            contacts = event.get("contacts")
            if self.mode != "connect" or self._capture_received or not isinstance(contacts, list) or len(contacts) > MAX_CONTACTS:
                raise ProtocolError("Invalid contact preview")
            seen = set()
            for item in contacts:
                if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] in seen or not isinstance(item.get("name"), str) or len(item["name"]) > 1024:
                    raise ProtocolError("Invalid contact preview")
                count(item.get("phoneCount"))
                ends = item.get("phoneEnds")
                if not isinstance(ends, list) or any(not isinstance(end, str) or not re.fullmatch(r"[0-9]{1,4}", end) for end in ends):
                    raise ProtocolError("Invalid contact preview")
                seen.add(item["id"])
            self.contacts = contacts
            self._capture_received = True
            self.phase = "selecting"
            self.status = "Select contacts, then review matching call and message context. No account data has been saved."
        elif kind == "review":
            counts = tuple(count(event.get(field)) for field in ("selected_contacts", "matched_calls", "matched_messages"))
            pending = self._pending_review
            if pending is None or counts[0] != len(pending):
                raise ProtocolError("Review does not match selection")
            missing = event.get("missing_sources")
            if not isinstance(missing, list) or any(not isinstance(item, str) or item not in {"contacts", "calls", "messages"} for item in missing):
                raise ProtocolError("Invalid source availability")
            self._pending_review = None
            if pending != self.selection:
                return  # Expected late response, never a current selection grant.
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
                return  # Revoke was queued behind this obsolete pair command.
            if not self.reviewed:
                raise ProtocolError("Pairing does not match reviewed selection")
            self.handoff_id, self.pair_code = binding, code
            self.status = "Enter this one-use code on the approved website. Received is not saved."
        elif kind == "handoff":
            state = event.get("state")
            if not isinstance(state, str) or state not in {"saved", "received", "expired", "waiting"}:
                raise ProtocolError("Invalid handoff state")
            if self._retired_handoff_id and event.get("handoffId") == self._retired_handoff_id:
                self._retired_handoff_id = ""
                return  # Expected stale acknowledgment cannot finish this selection.
            if not self._polling or event.get("handoffId") != self.handoff_id:
                raise ProtocolError("Handoff acknowledgment does not match")
            self._polling = False
            if state == "saved":
                self.saved_acknowledged = True
                self.status = "Matching account save acknowledged. Waiting for safe helper completion."
            elif state == "received":
                self.status = "Browser received the preview, not a saved account receipt. Keep this review open."
            elif state == "expired":
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
