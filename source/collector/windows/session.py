"""Windows presentation state for the engine-authorized paged protocol."""
from __future__ import annotations

import json
import hashlib
import math
import re
import uuid
from dataclasses import dataclass
from typing import TextIO

from amplifai_phone.source_diagnostics import (
    merge_source_diagnostics, validate_source_diagnostics, validate_source_diagnostic_history,
)

DISCLOSURE_VERSION = "native-local-collection-v1"
CONTACT_PAGE_SIZE = 200
REVIEW_PAGE_SIZE = 1_000
MAX_CONTACT_QUERY_LENGTH = 120
MAX_CONTACT_WORKSPACE_BYTES = 64 * 1024 * 1024
MAX_CONTACT_WORKSPACE_PREVIEWS = 100_000
MAX_CONTACT_WORKSPACE_MEMORY_BYTES = 256 * 1024 * 1024
CONTACT_WORKSPACE_MEMORY_RESERVE = 8 * 1024 * 1024
BACKUP_CHECK_MODE = "backup-encryption-status"
CONNECTION_FAILURE_HINTS = {
    "phone_connection": "Connect exactly one unlocked iPhone and check that it appears in Apple Devices on this PC.",
    "trust_required": "The iPhone's Trust connection could not be confirmed. Unlock it and review any Trust prompt yourself.",
    "connection_timeout": "The iPhone connection timed out. Check that it appears in Apple Devices on this PC and keep it unlocked.",
    "connection_lost": "The iPhone connection was interrupted. Check the existing cable or Wi-Fi connection and keep the iPhone unlocked.",
}
WEBSITE_ORIGIN = "https://amplifai.satoris.ai"
BRIDGE_PORT = 48751
OPTIONAL_SOURCES = frozenset({"calls", "messages"})
UNAVAILABLE_REASONS = frozenset({
    "absent", "selected_payload_missing", "selected_payload_invalid",
    "selected_payload_size", "selected_payload_identity", "selected_payload_file_type",
    "selected_payload_length", "selected_payload_crypto", "source_integrity",
    "unsupported_schema", "source_capacity", "source_invalid",
})


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class GroupMove:
    ids: frozenset[int]
    source: str
    destination: str
    generation: str
    query: str | None = None


@dataclass(frozen=True)
class GroupUndo:
    ids: frozenset[int]
    source: str
    destination: str
    generation: str
    selected_before: frozenset[int]


def count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 2**63 - 1:
        raise ProtocolError("Invalid helper count")
    return value


def unavailable_detail(source: str, reason: str | None, backup_encrypted: bool | None) -> str:
    if source == "calls" and reason == "absent" and backup_encrypted is False:
        return "absent from this unencrypted backup; a future owner-approved encrypted backup may include calls"
    details = {
        "absent": "absent from this capture",
        "selected_payload_missing": "listed in the backup but its file was not received",
        "selected_payload_size": "invalid backup-reported size",
        "selected_payload_identity": "invalid backup identity",
        "selected_payload_file_type": "not a regular backup file",
        "selected_payload_length": "received length differs from the backup manifest",
        "selected_payload_crypto": "encrypted source could not be validated",
        "selected_payload_invalid": "selected source failed integrity validation",
        "source_integrity": "selected source failed integrity validation",
        "unsupported_schema": "source format could not be read",
        "source_capacity": "local processing limit reached",
        "source_invalid": "not a readable database",
        "selected_contacts_integrity": "selected contacts failed integrity validation",
        "contacts_schema": "contacts format could not be read",
    }
    return details.get(reason, "reason not available from this helper")


def diagnostic_summary(item: dict) -> str:
    def amount(key: str) -> str:
        return "unknown" if item[key] is None else f"{item[key]:,}"
    return (f"{item['source'].title()} ({item['component']}): "
            f"{unavailable_detail(item['source'], item['reason'], item['encrypted'])}. "
            f"Bytes reported / received / copied: {amount('declaredBytes')} / {amount('rawBytes')} / {amount('copiedBytes')}.")


def diagnostic_context(item: dict) -> str:
    def flag(value: bool | None) -> str:
        return "unknown" if value is None else "yes" if value else "no"
    def code(key: str) -> str:
        return "unknown" if item[key] is None else item[key].replace("_", " ")
    return (diagnostic_summary(item) + f"\nResult: {code('result')}. Encrypted: {flag(item['encrypted'])}; entry key present: {flag(item['entryKeyPresent'])}. "
            f"Validation: {code('validationCode')} ({code('validationScope')}). "
            f"Producer digest: {item['producerDigest']}; copy digest: {item['copyDigest']}; sidecars: {item['sidecars']}.")


def contact_source_details(item: dict) -> str:
    names = {
        "first_and_last": "Source name: first and last fields filled.",
        "first": "Source name: first field filled. One name can be valid.",
        "last": "Source name: last field filled. One name can be valid.",
        "missing": "Source first and last fields are empty.",
    }
    name = names.get(item.get("nameFields"), "Name-field details are unavailable for this display name.")
    organization = item.get("organizationField")
    hint = ("Organization field supplied. This is a hint, not verified business identity." if organization is True else
            "Organization field is empty." if organization is False else "Organization-field details are unavailable.")
    return f"{name}\n{hint}\nThese local details do not change the contact, your selection, or account Save approval."


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
        self.unavailable_reasons: dict[str, str] = {}
        self.source_diagnostics: list[dict] = []
        self.source_diagnostic_history: list[dict] = []
        self.diagnostic_storage_status = ""
        self._current_diagnostic_attempt_id: str | None = None
        self.backup_encrypted: bool | None = None
        self.checked_backup_setting: bool | None = None
        self.backup_check_status = "Backup setting: Unknown — no read-only check completed."
        self._backup_check_received = False
        self._pending_backup_setting: bool | None = None
        self._backup_check_failure_hint = ""
        self.source_counts = (0, 0)
        self.transfer_status = "No phone data has been received."
        self.transfer_stage = ""
        self.transfer_bytes = 0
        self.transfer_elapsed = 0.0
        self.transfer_rate = 0.0
        self.progress: float | None = None
        self.handoff_id = ""
        self.pair_code = ""
        self.saved_acknowledged = False
        self.save_receipt_pending = False
        self.approval: str | None = None
        self._polling = self._pairing = self._finishing = False
        self._pending_review: tuple[int, ...] | None = None
        self._pending_review_id = ""
        self._pending_review_sha = ""
        self._review_next_cursor = 0
        self._review_expected_ack = 0
        self._retired_review_ids: set[str] = set()
        self._pairing_invalidated = False
        self._selection_change_pending = False
        self._retired_handoff_id = ""
        self._capture_received = False
        self.review_discarded = False
        self.selecting_all = False
        self.select_all_visited = 0
        self._select_all_ids: set[int] = set()
        self._select_all_cursor = 0
        self._select_all_scan_id = ""
        self._select_all_cancel = False
        self._scan_for_workspace = False
        self._workspace_previews: list[dict] = []
        self._workspace_preview_bytes = 0
        self._workspace_preview_memory_bytes = CONTACT_WORKSPACE_MEMORY_RESERVE
        self.contact_workspace = None
        self.contact_generation = str(uuid.uuid4())
        self._fresh_workspace_capture = False
        self._workspace_default_applied = False
        self._workspace_selection_edited = False
        self.marked_group_ids: set[int] = set()
        self.pending_group_move: GroupMove | None = None
        self.group_undo: GroupUndo | None = None
        self.destination_intent_id = ""
        self.destination_email = ""
        self.destination_course = ""
        self.destination_requested_approval: bool | None = None
        self.destination_approved = False
        self._retired_destination_ids: set[str] = set()
        self._browser_received = False
        self._failure_status = ""
        self._completed = self._saved_exit = self._stopping = self._failed = False

    @property
    def can_update(self) -> bool:
        return bool(self.inspected and not self.needs_inspection and not self.running and not self.residue
                    and not self.save_receipt_pending and not self._pairing and not self._polling
                    and not self._finishing and not self._selection_change_pending
                    and (not self._capture_received or self.saved_acknowledged or self.review_discarded))

    @property
    def can_connect(self) -> bool:
        return bool(self.approval and self.inspected and not self.needs_inspection and not self.residue and not self.running)

    @property
    def can_check_backup_setting(self) -> bool:
        # Never replace the owned helper while a capture, review, Save or cleanup is running.
        return not self.running and self.inspected and not self.needs_inspection and not self.residue

    @property
    def activity_stage(self) -> str:
        if self.mode == BACKUP_CHECK_MODE and self.running:
            return "Checking the existing backup setting — no backup is started"
        if self._saved_exit and not self.running and not self.needs_inspection:
            return "Account save acknowledged · temporary data checked"
        if self._capture_received and self.running:
            return "4 of 4 · Review & save — account Save is a separate decision"
        if self.mode == "connect" and self.running:
            if self.transfer_stage == "processing" or self.phase == "processing":
                return "3 of 4 · Read selected sources locally"
            if self.transfer_stage == "backup":
                return "2 of 4 · Receive backup bytes locally"
            return "1 of 4 · Connect iPhone — waiting for the first received byte"
        return "Connect → Receive → Read → Review & save"

    @property
    def progress_caption(self) -> str:
        if self.mode != "connect" or not self.running or self._capture_received:
            return "Backup progress, browser receipt and account Save are separate."
        if self.transfer_stage == "processing" or self.phase == "processing":
            return "Reading measured local bytes. Total reading time is unknown."
        if self.progress is not None:
            return f"{self.progress:.0f}% reported by iPhone · backup transfer only"
        return "No percentage reported. Received bytes appear when measured."

    @property
    def activity_measurement(self) -> str:
        if self.transfer_stage != "backup":
            return ""
        return (f"Measured receive time: {self.transfer_elapsed:.0f}s · "
                f"Observed rate: {self.transfer_rate:,.0f} bytes/s. No completion-time estimate.")

    @property
    def coverage_status(self) -> str:
        if not self._capture_received:
            coverage = "Call and message coverage has not been read."
        else:
            labels = (("calls", "Calls", self.source_counts[0]), ("messages", "Messages", self.source_counts[1]))
            coverage = " · ".join(
                f"{label} unavailable: {unavailable_detail(source, self.unavailable_reasons.get(source), self.backup_encrypted)}"
                if source in self.missing else f"{label}: {observed} readable phone entries"
                for source, label, observed in labels
            )
        summaries = [coverage]
        if self.source_diagnostics:
            summaries.append("Current collection source details: " + "\n".join(map(diagnostic_summary, self.source_diagnostics)))
        previous = self.previous_diagnostics
        if previous:
            latest = previous[0]["sourceDiagnostics"]
            attempts = "attempt" if len(previous) == 1 else "attempts"
            summaries.append(f"Previous run diagnostics ({len(previous)} retained {attempts}): " +
                             "; ".join(diagnostic_summary(item) for item in latest) + " Not part of this collection or account Save.")
        if self.diagnostic_storage_status:
            summaries.append(self.diagnostic_storage_status)
        return "\n".join(summaries)

    @property
    def previous_diagnostics(self) -> list[dict]:
        return [item for item in self.source_diagnostic_history if item["attemptId"] != self._current_diagnostic_attempt_id]

    @property
    def diagnostic_details(self) -> str:
        sections = []
        if self.source_diagnostics:
            sections.append("Current collection source details\n" + "\n\n".join(map(diagnostic_context, self.source_diagnostics)))
        for number, attempt in enumerate(self.previous_diagnostics, 1):
            sections.append(f"Previous run {number} — local attempt {attempt['attemptId']}\n"
                            "Historical evidence only; not part of this collection or account Save.\n" +
                            "\n\n".join(map(diagnostic_context, attempt["sourceDiagnostics"])))
        if self.diagnostic_storage_status:
            sections.append(self.diagnostic_storage_status)
        return "\n\n".join(sections)

    @property
    def can_select(self) -> bool:
        return (self.running and self.mode == "connect" and self.phase in {"selecting", "reviewing"}
                and not self._stopping and not self._finishing and not self.handoff_id
                and not self._selection_change_pending and not self.save_receipt_pending
                and not self.saved_acknowledged)

    @property
    def can_change_selection(self) -> bool:
        return (self.running and self.mode == "connect" and self.phase == "reviewing"
                and self.reviewed and bool(self.handoff_id) and not self._selection_change_pending
                and not self._pairing and not self._finishing and not self._stopping
                and not self.save_receipt_pending and not self.saved_acknowledged)

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
                and not self.save_receipt_pending and not self._selection_change_pending
                and self._pending_review is None and self.pending_contact is None
                and not self.selecting_all and not self._retired_handoff_id)

    def approve(self, checked: bool) -> None:
        self.approval = str(uuid.uuid4()) if checked and self.inspected and not self.needs_inspection and not self.residue and not self.running else None

    def begin(self, mode: str) -> None:
        if self.running or mode not in {"inspect", "clear-residue", "connect", BACKUP_CHECK_MODE}:
            raise ProtocolError("An operation is already running or unavailable")
        if mode == "connect" and not self.can_connect:
            raise ProtocolError("Fresh collection agreement and clean inspection required")
        if mode == BACKUP_CHECK_MODE and not self.can_check_backup_setting:
            raise ProtocolError("Inspect temporary data before checking the backup setting")
        if mode == BACKUP_CHECK_MODE:
            self.checked_backup_setting = None
            self._pending_backup_setting = None
            self._backup_check_received = False
            self._backup_check_failure_hint = ""
            self.backup_check_status = "Backup setting: checking the currently connected trusted iPhone…"
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
            self.contact_workspace = None
            self.contact_generation = str(uuid.uuid4())
            self._fresh_workspace_capture = False
            self._workspace_default_applied = False
            self._workspace_selection_edited = False
            self.marked_group_ids.clear()
            self.pending_group_move = self.group_undo = None
            self.selection = ()
            self.reviewed = self.saved_acknowledged = self.save_receipt_pending = False
            self.missing = []
            self.unavailable_reasons = {}
            self.source_diagnostics = []
            self.diagnostic_storage_status = ""
            self._current_diagnostic_attempt_id = None
            self.backup_encrypted = None
            self.source_counts = (0, 0)
            self.transfer_status = "Waiting for the iPhone. No phone data has been received."
            self.transfer_stage = ""
            self.transfer_bytes = 0
            self.transfer_elapsed = self.transfer_rate = 0.0
            self.handoff_id = self.pair_code = ""
            self._clear_destination()
            self._saved_exit = self._capture_received = False
            self.review_discarded = False
        self.approval = None
        self._completed = self._stopping = self._failed = False
        self._polling = self._pairing = self._finishing = self._pairing_invalidated = False
        self._selection_change_pending = False
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
        if mode == BACKUP_CHECK_MODE:
            self.status = "Reading the existing iPhone backup setting. No collection, backup or encryption change is requested."

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
        self._workspace_selection_edited = True
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

    def search_contacts(self, query: str) -> dict | None:
        if self.contact_workspace is not None:
            self._require_workspace_edit()
            if not isinstance(query, str) or len(query) > MAX_CONTACT_QUERY_LENGTH:
                raise ProtocolError("Invalid contact search")
            self.pending_group_move = None
            self.contact_workspace.search(query)
            self._sync_workspace_page()
            return None
        command = self.request_contacts(query, 0)
        if query != self.contact_query:
            self.previous_contact_cursors = []
        return command

    def next_contacts(self) -> dict | None:
        if self.contact_workspace is not None:
            self._require_workspace_edit()
            self.contact_workspace.next_page()
            self._sync_workspace_page()
            return None
        if self.pending_contact is not None or self.next_contact_cursor is None:
            raise ProtocolError("No next contact page")
        self.previous_contact_cursors.append(self.contact_cursor)
        return self.request_contacts(self.contact_query, self.next_contact_cursor)

    def previous_contacts(self) -> dict | None:
        if self.contact_workspace is not None:
            self._require_workspace_edit()
            self.contact_workspace.previous_page()
            self._sync_workspace_page()
            return None
        if self.pending_contact is not None or not self.previous_contact_cursors:
            raise ProtocolError("No previous contact page")
        cursor = self.previous_contact_cursors.pop()
        return self.request_contacts(self.contact_query, cursor)

    def select_all_contacts(self) -> dict | None:
        if self.contact_workspace is not None:
            return self.select_workspace_contacts(True, scoped=False)
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
        self._scan_for_workspace = False
        self._workspace_previews = []
        self._workspace_preview_bytes = 0
        self._workspace_preview_memory_bytes = CONTACT_WORKSPACE_MEMORY_RESERVE

    def _workspace_unavailable(self) -> None:
        self.pending_contact = None
        self._clear_select_all()
        self.status = "Grouping is unavailable for this collection. Your selection is unchanged; continue with the paged All contacts review."

    def clear_all_contacts(self) -> dict | None:
        if not self.can_select or self.selecting_all or self.pending_contact is not None or self._pending_review is not None:
            raise ProtocolError("Wait for the current contact operation before clearing selection")
        self._workspace_selection_edited = True
        if not self.selected_ids:
            return None
        return self.invalidate_selection()

    def _accept_select_all_page(self, event: dict) -> dict | None:
        try:
            return self._read_select_all_page(event)
        except MemoryError:
            if not self._scan_for_workspace:
                raise
            self._workspace_unavailable()
            return None
        except ProtocolError:
            if self._scan_for_workspace:
                self.pending_contact = None
                self._clear_select_all()
            raise

    def _read_select_all_page(self, event: dict) -> dict | None:
        if event.get("scanId") != self._select_all_scan_id or event.get("cursor") != self._select_all_cursor:
            raise ProtocolError("Contact scan does not match this review")
        if self._select_all_cancel:
            workspace_scan = self._scan_for_workspace
            self.pending_contact = None
            self._clear_select_all()
            self.status = ("Contact grouping check stopped. Your previous selection and review are unchanged."
                           if workspace_scan else "All-contact check stopped. The previous selection is unchanged; review it again before connecting.")
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
        if self._scan_for_workspace:
            from contact_workspace import preview_peak_bytes
            self._workspace_preview_bytes += len(json.dumps(page, ensure_ascii=False).encode("utf-8"))
            self._workspace_preview_memory_bytes += sum(preview_peak_bytes(item) for item in page)
            if (self._workspace_preview_bytes > MAX_CONTACT_WORKSPACE_BYTES
                    or self._workspace_preview_memory_bytes > MAX_CONTACT_WORKSPACE_MEMORY_BYTES):
                self._workspace_unavailable()
                return None
            self._workspace_previews.extend(page)
        if next_cursor is not None:
            self._select_all_cursor = next_cursor
            self.pending_contact = ("", next_cursor)
            self.status = f"Checked {self.select_all_visited} of {self.total_contacts} contacts…"
            return self._next_select_all_page()
        if self.select_all_visited != self.total_contacts or len(self._select_all_ids) != self.total_contacts:
            raise ProtocolError("All-contact coverage is incomplete")
        if self._scan_for_workspace:
            previews = self._workspace_previews
            self.pending_contact = None
            self._clear_select_all()
            self._publish_contact_workspace(previews)
            return None
        self.selected_ids = self._select_all_ids
        selected = len(self.selected_ids)
        self.pending_contact = None
        self._clear_select_all()
        self.status = f"All {selected} contacts selected across the complete review. Review before connecting."
        return None

    def prepare_contact_workspace(self) -> dict | None:
        """Verify a complete local snapshot before showing groups or defaults."""
        if self.contact_workspace is not None:
            return None
        if (not self.can_review or not self._capture_received or self.reviewed
                or self.selection or self.handoff_id):
            return None
        if self.total_contacts > MAX_CONTACT_WORKSPACE_PREVIEWS:
            self._workspace_unavailable()
            return None
        if self.total_contacts == len(self.contacts) and not self.contact_query and self.contact_cursor == 0:
            self._publish_contact_workspace(self.contacts)
            return None
        if not self.can_select_all:
            return None
        self.selecting_all = True
        self.select_all_visited = 0
        self._select_all_ids = set()
        self._select_all_cursor = 0
        self._select_all_scan_id = str(uuid.uuid4())
        self._select_all_cancel = False
        self._scan_for_workspace = True
        self._workspace_previews = []
        self._workspace_preview_bytes = 0
        self._workspace_preview_memory_bytes = CONTACT_WORKSPACE_MEMORY_RESERVE
        self.pending_contact = ("", 0)
        self.status = f"Checking all {self.total_contacts:,} contacts for Main and Review…"
        return self._next_select_all_page()

    def _publish_contact_workspace(self, previews: list[dict]) -> None:
        from contact_workspace import ContactWorkspace, preview_peak_bytes
        try:
            peak = CONTACT_WORKSPACE_MEMORY_RESERVE + sum(preview_peak_bytes(item) for item in previews)
            if (peak > MAX_CONTACT_WORKSPACE_MEMORY_BYTES
                    or len(previews) <= CONTACT_PAGE_SIZE
                    and len(json.dumps(previews, ensure_ascii=False).encode("utf-8")) > MAX_CONTACT_WORKSPACE_BYTES):
                self._workspace_unavailable()
                return
            workspace = ContactWorkspace(previews, self.total_contacts)
        except MemoryError:
            self._workspace_unavailable()
            return
        apply_default = (self._fresh_workspace_capture and not self._workspace_default_applied
                and not self._workspace_selection_edited and not self.selected_ids
                and not self.reviewed and not self.selection and not self.handoff_id
                and self.phase == "selecting")
        if apply_default:
            self.selected_ids = set(workspace.main_ids)
        self._workspace_default_applied = True
        self.contact_workspace = workspace
        self._sync_workspace_page()
        self.status = ("Main contacts start selected; Review contacts wait for you. Review your selection before continuing."
                       if apply_default else "Main and Review are ready. Your existing selection is unchanged.")

    def _sync_workspace_page(self) -> None:
        workspace = self.contact_workspace
        self.contacts = list(workspace.visible)
        self.contact_query = workspace.query
        self.contact_cursor = workspace.page_start
        self.next_contact_cursor = workspace.page_start + len(self.contacts) if workspace.has_next_page else None
        self.previous_contact_cursors = [max(0, workspace.page_start - CONTACT_PAGE_SIZE)] if workspace.has_previous_page else []
        self.pending_contact = None

    @property
    def can_edit_workspace(self) -> bool:
        return self.contact_workspace is not None and self.can_review

    def _require_workspace_edit(self) -> None:
        if not self.can_edit_workspace:
            raise ProtocolError("Finish the current review or account operation before organizing contacts")

    def show_contact_group(self, group: str) -> None:
        self._require_workspace_edit()
        if group not in ("All", "Main", "Review"):
            raise ProtocolError("Unknown contact group")
        self.pending_group_move = None
        if self.contact_workspace.group != group:
            self.marked_group_ids.clear()
        self.contact_workspace.show(group)
        self._sync_workspace_page()

    def select_workspace_contacts(self, selected: bool, *, scoped=True) -> dict | None:
        self._require_workspace_edit()
        if type(selected) is not bool:
            raise ProtocolError("Invalid contact selection")
        self._workspace_selection_edited = True
        ids = self.contact_workspace.filtered_ids if scoped else self.contact_workspace.known_ids
        self.selected_ids = self.selected_ids | set(ids) if selected else self.selected_ids - set(ids)
        self.group_undo = None
        return self.invalidate_selection(clear_selected=False)

    def mark_contact_for_move(self, contact_id: int) -> None:
        self._require_workspace_edit()
        workspace = self.contact_workspace
        if workspace.group == "All" or contact_id not in {item["id"] for item in workspace.visible}:
            raise ProtocolError("Choose a visible contact in Main or Review to mark")
        self.pending_group_move = None
        self.marked_group_ids.symmetric_difference_update({contact_id})

    def mark_all_for_move(self) -> None:
        self._require_workspace_edit()
        if self.contact_workspace.group == "All":
            raise ProtocolError("Choose Main or Review to mark contacts")
        self.pending_group_move = None
        self.marked_group_ids.update(self.contact_workspace.filtered_ids)

    def clear_move_marks(self) -> None:
        self._require_workspace_edit()
        self.pending_group_move = None
        self.marked_group_ids.clear()

    def begin_group_move(self, *, marked=False) -> GroupMove:
        self._require_workspace_edit()
        workspace = self.contact_workspace
        if workspace.group == "All":
            raise ProtocolError("Choose Main or Review to move contacts")
        ids = frozenset(self.marked_group_ids if marked else workspace.filtered_ids)
        source_ids = workspace.main_ids if workspace.group == "Main" else workspace.review_ids
        if not ids or not ids <= source_ids:
            raise ProtocolError("Group move does not match the current contacts")
        move = GroupMove(ids, workspace.group, "Review" if workspace.group == "Main" else "Main",
                         self.contact_generation, None if marked else workspace.query)
        self.pending_group_move = move
        return move

    def cancel_group_move(self) -> None:
        self.pending_group_move = None

    def confirm_group_move(self, expected: GroupMove) -> dict | None:
        self._require_workspace_edit()
        move, self.pending_group_move = self.pending_group_move, None
        workspace = self.contact_workspace
        if (move is None or move != expected or move.generation != self.contact_generation
                or workspace.group != move.source
                or (move.query is None and self.marked_group_ids != move.ids)
                or (move.query is not None and (workspace.query != move.query or workspace.filtered_ids != move.ids))):
            raise ProtocolError("The contacts changed while the group move was awaiting confirmation")
        return self._apply_group_move(move)

    def move_contact(self, contact_id: int, destination: str) -> dict | None:
        self._require_workspace_edit()
        workspace = self.contact_workspace
        if not workspace.contains(contact_id):
            raise ProtocolError("Contact is not in this review")
        source = "Review" if workspace.is_in_review(contact_id) else "Main"
        if destination not in ("Main", "Review") or source == destination:
            raise ProtocolError("Invalid contact group move")
        return self._apply_group_move(GroupMove(frozenset({contact_id}), source, destination, self.contact_generation))

    def _apply_group_move(self, move: GroupMove) -> dict | None:
        selected_before = frozenset(self.selected_ids & move.ids)
        if not self.contact_workspace.move(move.ids, move.source, move.destination):
            raise ProtocolError("Contact group move no longer matches the review")
        command = self.invalidate_selection(clear_selected=False)
        if move.destination == "Review":
            self.selected_ids.difference_update(move.ids)
        self.marked_group_ids.difference_update(move.ids)
        self.group_undo = GroupUndo(move.ids, move.source, move.destination, move.generation, selected_before)
        self._sync_workspace_page()
        return command

    def undo_group_move(self) -> dict | None:
        self._require_workspace_edit()
        undo = self.group_undo
        if (undo is None or undo.generation != self.contact_generation
                or not self.contact_workspace.move(undo.ids, undo.destination, undo.source)):
            raise ProtocolError("No current group move can be undone")
        command = self.invalidate_selection(clear_selected=False)
        self.selected_ids.difference_update(undo.ids)
        self.selected_ids.update(undo.selected_before)
        self.group_undo = None
        self._sync_workspace_page()
        return command

    def _clear_destination(self) -> None:
        self.destination_intent_id = ""
        self.destination_email = ""
        self.destination_course = ""
        self.destination_requested_approval = None
        self.destination_approved = False
        self._retired_destination_ids.clear()
        self._browser_received = False

    @property
    def can_decide_destination(self) -> bool:
        return (self.running and self.mode == "connect" and self.phase == "reviewing"
                and self.reviewed and self.handoff_id != "" and not self._pairing
                and not self._selection_change_pending and not self.save_receipt_pending
                and not self.saved_acknowledged and not self._stopping and not self._finishing
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
        self._workspace_selection_edited = True
        self.group_undo = None
        self.pending_group_move = None
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

    def request_selection_change(self) -> dict:
        if not self.can_change_selection:
            raise ProtocolError("Finish the current account save before changing contacts")
        self._selection_change_pending = True
        self.status = "Checking that changing contacts will not interrupt an account save…"
        return {"action": "revoke"}

    def pair(self) -> dict:
        if not self.can_pair:
            raise ProtocolError("Review the current selection before pairing")
        self._pairing = True
        self._pairing_invalidated = False
        self.status = ("Preparing a new browser pairing. The current review stays bound until the helper accepts it…"
                       if self.handoff_id else "Preparing an exact-origin, five-minute initial browser pairing…")
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
        self.pending_contact = None
        self.pending_group_move = None
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
            if ((item.get("nameFields") is not None and not isinstance(item["nameFields"], str)) or
                    (item.get("organizationField") is not None and type(item["organizationField"]) is not bool)):
                raise ProtocolError("Invalid contact source details")
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
        if self._stopping and kind not in {"error", "selected-payload-diagnostic", "diagnostic-storage"}:
            return None
        if kind == BACKUP_CHECK_MODE:
            if (self.mode != BACKUP_CHECK_MODE or self._backup_check_received or
                    set(event) != {"kind", "backupEncrypted"} or type(event.get("backupEncrypted")) is not bool):
                raise ProtocolError("Invalid backup setting result")
            self._pending_backup_setting = event["backupEncrypted"]
            self._backup_check_received = True
        elif kind == "progress":
            value = event.get("value")
            if type(value) not in {float, int} or not 0 <= value <= 100 or not math.isfinite(value):
                raise ProtocolError("Invalid progress")
            self.progress = float(value)
        elif kind == "transfer":
            stage = event.get("stage")
            if (self.mode != "connect" or self._capture_received or not isinstance(stage, str)
                    or stage not in {"backup", "processing"}):
                raise ProtocolError("Invalid transfer stage")
            if stage == "backup":
                fields = {"kind", "stage", "receivedBytes", "retainedBytes", "discardedBytes",
                          "filesReceived", "elapsedSeconds", "bytesPerSecond"}
                if set(event) != fields or self.transfer_stage == "processing":
                    raise ProtocolError("Invalid backup progress")
                received = count(event["receivedBytes"])
                retained = count(event["retainedBytes"])
                discarded = count(event["discardedBytes"])
                files = count(event["filesReceived"])
                elapsed, rate = event["elapsedSeconds"], event["bytesPerSecond"]
                if (retained + discarded != received or received < self.transfer_bytes
                        or type(elapsed) not in {float, int} or not 0 < elapsed <= 2**63 - 1
                        or not math.isfinite(elapsed) or type(rate) not in {float, int}
                        or not 0 <= rate <= 2**63 - 1 or not math.isfinite(rate)):
                    raise ProtocolError("Invalid backup counters")
                self.transfer_status = (f"Receiving backup: {received:,} bytes checked across {files:,} files; "
                                        f"{retained:,} retained locally, {discarded:,} discarded.")
                self.transfer_elapsed, self.transfer_rate = float(elapsed), float(rate)
            else:
                if set(event) != {"kind", "stage", "processedBytes"}:
                    raise ProtocolError("Invalid processing progress")
                processed = count(event["processedBytes"])
                if self.transfer_stage == "processing" and processed < self.transfer_bytes:
                    raise ProtocolError("Processing progress went backward")
                self.transfer_status = f"Reading selected local sources: {processed:,} bytes processed."
                received = processed
            self.transfer_stage, self.transfer_bytes = stage, received
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
            elif state == "pairing_revoked" and self._selection_change_pending:
                self._selection_change_pending = False
                self._retired_handoff_id = self.handoff_id if self._polling else ""
                self._polling = False
                self.handoff_id = self.pair_code = ""
                self._clear_destination()
                self.selection = ()
                self.reviewed = False
                self.phase = "selecting"
                self.status = "Browser pairing closed. Your contact choices remain; change them and review again."
        elif kind == "selected-payload-diagnostic":
            if self.mode != "connect" or self._capture_received:
                raise ProtocolError("Unexpected source diagnostic")
            try:
                facts = validate_source_diagnostics([{key: value for key, value in event.items() if key != "kind"}])
                self.source_diagnostics = merge_source_diagnostics(self.source_diagnostics, facts)
            except ValueError as error:
                raise ProtocolError("Invalid source diagnostic") from error
        elif kind == "diagnostic-storage":
            state = event.get("state")
            if state == "retained" and set(event) == {"kind", "state", "attemptId"}:
                attempt = event.get("attemptId")
                try:
                    parsed = uuid.UUID(attempt) if type(attempt) is str else None
                except ValueError:
                    parsed = None
                if parsed is None or str(parsed) != attempt or parsed.version != 4 or parsed.variant != uuid.RFC_4122:
                    raise ProtocolError("Invalid retained diagnostic identity")
                if self.mode == "connect":
                    self._current_diagnostic_attempt_id = attempt
                self.diagnostic_storage_status = "Source diagnostics retained locally for this attempt."
            elif state == "failed" and set(event) == {"kind", "state", "code"} and event.get("code") == "diagnostics_unavailable":
                self.diagnostic_storage_status = "Source diagnostics could not be retained locally. Current details remain visible until a new collection starts."
            else:
                raise ProtocolError("Invalid diagnostic storage result")
        elif kind == "capture":
            if self.mode != "connect" or self._capture_received:
                raise ProtocolError("Invalid contact preview")
            calls = count(event.get("availableCalls"))
            messages = count(event.get("availableMessages"))
            missing = event.get("missing")
            if (not isinstance(missing, list) or any(not isinstance(item, str) or item not in OPTIONAL_SOURCES
                    for item in missing) or len(missing) != len(set(missing))):
                raise ProtocolError("Invalid source availability")
            backup_encrypted = event.get("backupEncrypted")
            if backup_encrypted is not None and type(backup_encrypted) is not bool:
                raise ProtocolError("Invalid backup encryption state")
            reasons = event.get("unavailableReasons", {})
            if (not isinstance(reasons, dict) or any(
                    source not in OPTIONAL_SOURCES or source not in missing
                    or not isinstance(reason, str) or reason not in UNAVAILABLE_REASONS
                    for source, reason in reasons.items())):
                raise ProtocolError("Invalid source reason")
            try:
                facts = validate_source_diagnostics(event.get("sourceDiagnostics", []))
                if any(item["source"] not in missing or reasons.get(item["source"]) != item["reason"] for item in facts):
                    raise ValueError("Source diagnostic does not match capture availability")
                pending = [item for item in self.source_diagnostics
                           if item["source"] in missing and reasons.get(item["source"]) == item["reason"]]
                facts = merge_source_diagnostics(pending, facts)
            except ValueError as error:
                raise ProtocolError("Invalid capture source diagnostics") from error
            self._accept_contact_page(event, first=True)
            self.source_counts = (calls, messages)
            self.missing = missing
            self.unavailable_reasons = dict(reasons)
            self.source_diagnostics = facts
            self.backup_encrypted = backup_encrypted
            self._capture_received = True
            self._fresh_workspace_capture = True
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
            if (not isinstance(missing, list) or any(not isinstance(item, str) for item in missing)
                    or len(missing) != len(set(missing)) or set(missing) != set(self.missing)):
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
            self._retired_handoff_id = self.handoff_id if self._polling else ""
            self._polling = False
            self.handoff_id, self.pair_code = binding, code
            self._clear_destination()
            self.save_receipt_pending = self.saved_acknowledged = False
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
            if not isinstance(event.get("state"), str) or event["state"] not in {"pending", "approved", "denied"}:
                raise ProtocolError("Invalid account destination state")
            if intent_id in self._retired_destination_ids:
                return None
            if event.get("state") == "pending":
                email, course = event.get("email"), event.get("course")
                if (not isinstance(email, str) or not 0 < len(email) <= 254 or "@" not in email
                        or not isinstance(course, str) or not 0 < len(course) <= 160
                        or any(ord(char) < 32 for char in email + course)
                        or self.destination_intent_id == intent_id and
                        (self.destination_email != email or self.destination_course != course)):
                    raise ProtocolError("Invalid server-confirmed account destination")
                if intent_id != self.destination_intent_id:
                    if self._browser_received or self.save_receipt_pending or self.saved_acknowledged:
                        raise ProtocolError("Account destination cannot replace a received handoff")
                    # A new pending intent is emitted only after the shared bridge
                    # verifies its account proof. It must never inherit an old decision.
                    if self.destination_intent_id:
                        self._retired_destination_ids.add(self.destination_intent_id)
                    self.destination_requested_approval = None
                    self.destination_approved = False
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
        elif kind == "handoff":
            state = event.get("state")
            if not isinstance(state, str) or state not in {"saved", "saved_pending_browser_receipt", "received", "expired", "waiting"}:
                raise ProtocolError("Invalid handoff state")
            if self._retired_handoff_id and event.get("handoffId") == self._retired_handoff_id:
                self._retired_handoff_id = ""
                return None  # Expected stale acknowledgment cannot finish this selection.
            receipted = state in {"saved", "saved_pending_browser_receipt"}
            if (event.get("handoffId") != self.handoff_id or not self.handoff_id
                    or not (self._polling or receipted and (
                        self._pairing or self._selection_change_pending
                        or self.save_receipt_pending or self.saved_acknowledged))):
                raise ProtocolError("Handoff acknowledgment does not match")
            self._polling = False
            if receipted:
                self._pairing = self._selection_change_pending = False
            if state == "saved":
                self.save_receipt_pending = False
                self.saved_acknowledged = True
                self.status = "Matching account save acknowledged. Waiting for safe helper completion."
            elif state == "received":
                self._browser_received = True
                self.status = "Browser received the preview, not a saved account receipt. Keep this review open."
            elif state == "saved_pending_browser_receipt":
                self.save_receipt_pending = True
                self.status = "Saving is being finalized. Finish in the browser before changing contacts."
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
            try:
                history = validate_source_diagnostic_history(event.get("sourceDiagnosticHistory", []))
            except ValueError as error:
                raise ProtocolError("Invalid source diagnostic history") from error
            self.residue = items
            self.source_diagnostic_history = history
            self.inspected = True
        elif kind == "cleared":
            if self.mode != "clear-residue":
                raise ProtocolError("Unexpected cleanup result")
            self.status = f"Removed {count(event.get('count'))} marked abandoned sessions. Inspect again to confirm."
        elif kind == "connection":
            if event.get("transport") not in {"usb", "wifi"}:
                raise ProtocolError("Invalid connection transport")
        elif kind == "error":
            if (event == {"kind": "error", "code": "handoff_unconfirmed"}
                    and self.can_pair and not self.destination_approved
                    and self.destination_requested_approval is not None):
                self.destination_requested_approval = None
                self.status = "Destination confirmation was not accepted. Checking the current account destination; your local review remains open."
                return None
            if event.get("code") == "bridge_unavailable" and self._pairing:
                self._retired_handoff_id = self.handoff_id if self._polling else ""
                self._pairing = self._polling = False
                self.handoff_id = self.pair_code = ""
                self._clear_destination()
                self.status = "New pairing could not start. Your reviewed contacts remain; retry Connect without importing again."
                return None
            self._failed = True
            self.needs_inspection = True
            self.phase = "error"
            code = event.get("code")
            connection_hint = CONNECTION_FAILURE_HINTS.get(code, "") if isinstance(code, str) else ""
            if self.mode == BACKUP_CHECK_MODE:
                self._backup_check_failure_hint = connection_hint
            messages = {
                "workspace_marker": "A private empty or marker-only root may remain. No phone data was written by this attempt. Collection is paused; this unverified root is not automatically removed.",
                "workspace_unsafe": "Private storage could not be verified. Unverified directories are not repaired or automatically removed. Collection is stopped; cleanup is unconfirmed.",
                "workspace_unavailable": "Private storage is unavailable. Collection completion is not assumed; check the package/runtime and inspect after the helper stops.",
            }
            if connection_hint:
                self._failure_status = connection_hint + " Completion and cleanup are unconfirmed. Inspect temporary data after the helper stops."
            else:
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
        self._clear_select_all()
        self.pending_contact = None
        self.pending_group_move = None
        if self.mode == BACKUP_CHECK_MODE:
            valid = code == 0 and self._backup_check_received and not self._failed and not self._stopping
            self.checked_backup_setting = self._pending_backup_setting if valid else None
            if self.checked_backup_setting is True:
                self.backup_check_status = "Last backup-setting check: On. This is not captured-source coverage; Calls are not guaranteed."
            elif self.checked_backup_setting is False:
                self.backup_check_status = "Last backup-setting check: Off. Use the guide if you choose to enable encryption yourself; unavailable Calls remain unavailable."
            elif self._backup_check_failure_hint:
                self.backup_check_status = f"Backup setting: Unknown. {self._backup_check_failure_hint} No setting was changed."
            else:
                self.backup_check_status = "Backup setting: Unknown. Connect one unlocked, already trusted iPhone and recheck; no setting was changed."
            self._pending_backup_setting = None
            self._backup_check_received = False
            self.needs_inspection, self.inspected = True, False
            self.phase = "checking"
            self.status = "Read-only setting check ended. Inspect temporary data before another operation."
        elif self.mode == "inspect" and code == 0 and self.inspected and not self._failed:
            self.needs_inspection = False
            self.phase = "completed" if self._saved_exit and not self.residue else "idle"
            self.status = "Account save acknowledged; helper exited and no abandoned temporary sessions were found." if self.phase == "completed" else "Temporary data checked. Review collection agreement to connect." if not self.residue else "Marked temporary phone data remains. Explicitly remove it before another collection."
        else:
            self._saved_exit = self.mode == "connect" and code == 0 and self._completed and self.saved_acknowledged and not self._failed and not self._stopping
            self.needs_inspection = True
            self.inspected = False
            self.phase = "checking" if self._saved_exit else "error"
            self.status = "Account save acknowledged; check temporary data before completion." if self._saved_exit else self._failure_status or "Helper stopped; cleanup is unconfirmed. Check temporary data before collecting again."
