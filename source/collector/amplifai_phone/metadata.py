"""Fail-closed, metadata-only readers for selected iPhone backup databases.

No query in this module reads message text, attributed bodies, attachments, or
contact notes. The source databases can nevertheless contain that material and
must remain in a private, short-lived directory.
"""

from __future__ import annotations

import math
import re
import sqlite3
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .record_store import RecordCollection, RecordStore
from .workspace import WorkspaceError

COCOA_EPOCH = 978307200
RETAINED_HISTORY_START = datetime(2001, 1, 1, tzinfo=UTC)
MAX_ROWS_PER_SOURCE = 1_000_000
MAX_SELECTED_CONTACTS = 25_000
ROW_CHECK_INTERVAL = 1024
PHONE_CHARS = re.compile(r"[^0-9+]")


class UnsupportedSchema(ValueError):
    """A device database shape is not one we can safely interpret."""


class BackupControlInvalid(UnsupportedSchema):
    """Backup manifest or required control metadata is unusable."""


class ContactsSchemaUnsupported(UnsupportedSchema):
    """The required contact database cannot be interpreted safely."""


class SelectedPayloadMissing(UnsupportedSchema):
    """A manifest-listed selected file was not retained by DeviceLink."""


class SelectedPayloadIntegrityError(UnsupportedSchema):
    """A retained selected file cannot be trusted for parsing."""


class SourceCapacityLimit(ValueError):
    """A bounded metadata/control frontier, not an unsupported phone schema."""

    code = "source_capacity_limit"


class SourceReadCapacityLimit(SourceCapacityLimit):
    """A per-source reader bound, distinct from backup/session safety bounds."""

    code = "source_read_capacity_limit"


class ContactsCapacityLimit(SourceReadCapacityLimit):
    """The required contacts source exceeded its bounded in-memory reader."""

    code = "contacts_capacity_limit"


class BackupControlFrameLimit(SourceCapacityLimit):
    """A DeviceLink control frame exceeded its finite parsing bound."""

    code = "backup_control_frame_limit"


class BackupControlMetadataLimit(SourceCapacityLimit):
    """An on-disk backup control plist exceeded its finite parsing bound."""

    code = "backup_control_metadata_limit"


class BackupControlPathLimit(SourceCapacityLimit):
    """A DeviceLink path exceeded the supported path safety bound."""

    code = "backup_control_path_limit"


@dataclass(frozen=True)
class Contact:
    source_id: int
    name: str
    phones: tuple[str, ...]
    emails: tuple[str, ...]


@dataclass(frozen=True)
class Interaction:
    source_id: int
    kind: str
    transport: str
    occurred_at: str
    direction: str
    participants: tuple[str, ...]
    duration_seconds: int | None


@dataclass(frozen=True)
class SourceResult:
    rows_seen: int
    records: Sequence[Contact | Interaction]
    excluded: int


def normalize_phone(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    candidate = raw.strip().removeprefix("tel:")
    if "@" in candidate or any(character.isalpha() for character in candidate):
        return None
    value = PHONE_CHARS.sub("", candidate)
    if value.startswith("+") and value[1:].isdigit() and 6 <= len(value[1:]) <= 15:
        return value
    if value.isdigit() and len(value) == 10:
        return "+1" + value
    if value.isdigit() and len(value) == 11 and value.startswith("1"):
        return "+" + value
    return None


@contextmanager
def _open_readonly(path: Path) -> Iterator[sqlite3.Connection]:
    if not path.is_file():
        raise UnsupportedSchema(f"Missing required database: {path.name}")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA mmap_size=0")
        connection.execute("PRAGMA cache_size=-2048")
        yield connection
    finally:
        connection.close()


def _require_columns(
    connection: sqlite3.Connection, table: str, names: set[str]
) -> None:
    actual = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    missing = names - actual - {"ROWID"}
    if missing:
        raise UnsupportedSchema(
            f"Unsupported {table} schema: missing {', '.join(sorted(missing))}"
        )


def _cocoa_datetime(value: object) -> datetime | None:
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    seconds = value / 1_000_000_000 if value > 1_000_000_000_000_000 else value
    try:
        return datetime.fromtimestamp(seconds + COCOA_EPOCH, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _scratch_execute(
    connection: sqlite3.Connection, statement: str, parameters: tuple = ()
) -> sqlite3.Cursor:
    try:
        return connection.execute(statement, parameters)
    except sqlite3.Error as error:
        raise WorkspaceError(
            "Private metadata scratch is unavailable",
            code="workspace_unavailable",
        ) from error


@contextmanager
def _private_index(path: Path, prefix: str) -> Iterator[sqlite3.Connection]:
    try:
        scratch = tempfile.TemporaryDirectory(prefix=prefix, dir=path.parent)
    except OSError as error:
        raise WorkspaceError(
            "Private metadata scratch is unavailable", code="workspace_unavailable"
        ) from error
    with scratch as index_dir:
        try:
            index_connection = sqlite3.connect(Path(index_dir) / "values.sqlite3")
        except sqlite3.Error as error:
            raise WorkspaceError(
                "Private metadata scratch is unavailable", code="workspace_unavailable"
            ) from error
        with closing(index_connection) as index_db:
            _scratch_execute(index_db, "PRAGMA journal_mode=OFF")
            _scratch_execute(index_db, "PRAGMA synchronous=OFF")
            _scratch_execute(index_db, "PRAGMA cache_size=-2048")
            yield index_db


def read_contacts(
    path: Path, check_callback: Callable[[], None] | None = None,
    store: RecordStore | None = None,
) -> SourceResult:
    with _open_readonly(path) as db:
        _require_columns(db, "ABPerson", {"ROWID", "First", "Last"})
        _require_columns(db, "ABMultiValue", {"record_id", "property", "value"})
        # The selected database is already inside the app-owned private parsing
        # directory. Keep only normalized metadata in a short-lived disk index;
        # a large ABMultiValue table must not become an unbounded Python map.
        with _private_index(path, "amplifai-contact-index-") as index_db:
            _scratch_execute(
                index_db,
                "CREATE TABLE value (record_id INTEGER NOT NULL, property INTEGER NOT NULL, "
                "normalized TEXT NOT NULL, PRIMARY KEY (record_id, property, normalized)) WITHOUT ROWID",
            )
            for index, row in enumerate(
                db.execute(
                    "SELECT record_id, property, value FROM ABMultiValue "
                    "WHERE property IN (3, 4) AND value IS NOT NULL"
                )
            ):
                if store is None and index >= MAX_ROWS_PER_SOURCE:
                    raise ContactsCapacityLimit("Contact value row limit exceeded")
                if check_callback is not None and index % ROW_CHECK_INTERVAL == 0:
                    check_callback()
                owner = row["record_id"]
                if (
                    isinstance(owner, float)
                    and math.isfinite(owner)
                    and owner.is_integer()
                ):
                    owner = int(owner)
                if not isinstance(owner, int):
                    continue
                normalized = None
                if row["property"] == 3:
                    normalized = normalize_phone(row["value"])
                elif row["property"] == 4 and isinstance(row["value"], str):
                    email = row["value"].strip().lower()
                    if (
                        3 <= len(email) <= 254
                        and "@" in email
                        and not any(c.isspace() for c in email)
                    ):
                        normalized = email
                if normalized is not None:
                    _scratch_execute(
                        index_db,
                        "INSERT OR IGNORE INTO value VALUES (?, ?, ?)",
                        (owner, row["property"], normalized),
                    )
            contacts = []
            row_count = 0
            rows = db.execute("SELECT ROWID, First, Last FROM ABPerson ORDER BY ROWID")
            for row_count, row in enumerate(rows, 1):
                if store is None and row_count > MAX_ROWS_PER_SOURCE:
                    raise ContactsCapacityLimit("Contact row limit exceeded")
                if check_callback is not None and row_count % ROW_CHECK_INTERVAL == 0:
                    check_callback()
                phones = []
                emails = []
                try:
                    for property_id, normalized in _scratch_execute(
                        index_db,
                        "SELECT property, normalized FROM value WHERE record_id = ? "
                        "ORDER BY property, normalized",
                        (row["ROWID"],),
                    ):
                        (phones if property_id == 3 else emails).append(normalized)
                except sqlite3.Error as error:
                    raise WorkspaceError(
                        "Private contact metadata scratch is unavailable",
                        code="workspace_unavailable",
                    ) from error
                name = " ".join(
                    part.strip()
                    for part in (row["First"], row["Last"])
                    if isinstance(part, str) and part.strip()
                )
                if name or phones or emails:
                    contact = Contact(row["ROWID"], name, tuple(phones), tuple(emails))
                    if store is None:
                        contacts.append(contact)
                    else:
                        store.add("contacts", contact)
            records = tuple(contacts) if store is None else store.collection("contacts")
            return SourceResult(row_count, records, row_count - len(records))


def read_calls(
    path: Path, since: datetime, check_callback: Callable[[], None] | None = None,
    store: RecordStore | None = None,
) -> SourceResult:
    with _open_readonly(path) as db:
        _require_columns(
            db,
            "ZCALLRECORD",
            {"ZDATE", "ZDURATION", "ZADDRESS", "ZORIGINATED", "ZANSWERED"},
        )
        rows = db.execute(
            "SELECT ROWID, ZDATE, ZDURATION, ZADDRESS, ZORIGINATED, ZANSWERED "
            "FROM ZCALLRECORD ORDER BY ROWID"
        )
        calls = []
        row_count = 0
        for row_count, row in enumerate(rows, 1):
            if store is None and row_count > MAX_ROWS_PER_SOURCE:
                raise SourceReadCapacityLimit("Call row limit exceeded")
            if check_callback is not None and row_count % ROW_CHECK_INTERVAL == 0:
                check_callback()
            when = _cocoa_datetime(row["ZDATE"])
            phone = normalize_phone(row["ZADDRESS"])
            if when is None or when < since or phone is None:
                continue
            outgoing = row["ZORIGINATED"] == 1
            direction = (
                "outgoing"
                if outgoing
                else "incoming"
                if row["ZANSWERED"] == 1
                else "missed"
            )
            duration = row["ZDURATION"]
            duration_seconds = (
                max(0, min(86400, int(duration)))
                if isinstance(duration, (int, float)) and math.isfinite(duration)
                else None
            )
            call = Interaction(
                    row["ROWID"],
                    "call",
                    "call",
                    when.isoformat(),
                    direction,
                    (phone,),
                    duration_seconds,
                )
            if store is None:
                calls.append(call)
            else:
                store.add("calls", call)
        records = tuple(calls) if store is None else store.collection("calls")
        return SourceResult(row_count, records, row_count - len(records))


def read_messages(
    path: Path, since: datetime, check_callback: Callable[[], None] | None = None,
    store: RecordStore | None = None,
) -> SourceResult:
    with _open_readonly(path) as db:
        _require_columns(db, "message", {"date", "service", "is_from_me", "handle_id"})
        _require_columns(db, "handle", {"ROWID", "id"})
        has_chat = all(
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            for table in ("chat_message_join", "chat_handle_join")
        )
        if has_chat:
            _require_columns(db, "chat_message_join", {"message_id", "chat_id"})
            _require_columns(db, "chat_handle_join", {"chat_id", "handle_id"})
        rows = db.execute(
            "SELECT message.ROWID AS source_id, message.date, message.service, message.is_from_me, "
            "handle.id AS sender FROM message LEFT JOIN handle ON message.handle_id = handle.ROWID "
            "ORDER BY message.ROWID",
        )
        # Preserve the independent source-row bounds before any join expansion.
        # Only normalized phone metadata and opaque source IDs enter this short-lived
        # private index; message bodies and attachments never enter it.
        index_context = (
            _private_index(path, "amplifai-message-index-")
            if has_chat
            else nullcontext(None)
        )
        with index_context as index_db:
            if index_db is not None:
                _scratch_execute(
                    index_db,
                    "CREATE TABLE chat_phone (chat_id BLOB, phone TEXT NOT NULL)",
                )
                _scratch_execute(
                    index_db,
                    "CREATE UNIQUE INDEX chat_phone_key ON chat_phone (chat_id, phone)",
                )
                _scratch_execute(
                    index_db,
                    "CREATE TABLE message_chat (message_id BLOB, chat_id BLOB)",
                )
                _scratch_execute(
                    index_db,
                    "CREATE UNIQUE INDEX message_chat_key ON message_chat (message_id, chat_id)",
                )
                for index, row in enumerate(
                    db.execute(
                        "SELECT chj.chat_id, handle.id FROM chat_handle_join AS chj "
                        "JOIN handle ON chj.handle_id = handle.ROWID"
                    )
                ):
                    if store is None and index >= MAX_ROWS_PER_SOURCE:
                        raise SourceReadCapacityLimit(
                            "Message participant row limit exceeded"
                        )
                    if check_callback is not None and index % ROW_CHECK_INTERVAL == 0:
                        check_callback()
                    phone = normalize_phone(row["id"])
                    if phone:
                        _scratch_execute(
                            index_db,
                            "INSERT OR IGNORE INTO chat_phone VALUES (?, ?)",
                            (row["chat_id"], phone),
                        )
                for index, row in enumerate(
                    db.execute("SELECT message_id, chat_id FROM chat_message_join")
                ):
                    if store is None and index >= MAX_ROWS_PER_SOURCE:
                        raise SourceReadCapacityLimit("Message chat row limit exceeded")
                    if check_callback is not None and index % ROW_CHECK_INTERVAL == 0:
                        check_callback()
                    _scratch_execute(
                        index_db,
                        "INSERT OR IGNORE INTO message_chat VALUES (?, ?)",
                        (row["message_id"], row["chat_id"]),
                    )
            messages = []
            row_count = 0
            for row_count, row in enumerate(rows, 1):
                if store is None and row_count > MAX_ROWS_PER_SOURCE:
                    raise SourceReadCapacityLimit("Message row limit exceeded")
                if check_callback is not None and row_count % ROW_CHECK_INTERVAL == 0:
                    check_callback()
                when = _cocoa_datetime(row["date"])
                if when is None or when < since:
                    continue
                sender = normalize_phone(row["sender"])
                participants = {sender} if sender else set()
                if index_db is not None:
                    try:
                        for (phone,) in _scratch_execute(
                            index_db,
                            "SELECT phone.phone FROM message_chat AS link "
                            "JOIN chat_phone AS phone ON phone.chat_id IS link.chat_id "
                            "WHERE link.message_id IS ?",
                            (row["source_id"],),
                        ):
                            participants.add(phone)
                    except sqlite3.Error as error:
                        raise WorkspaceError(
                            "Private metadata scratch is unavailable",
                            code="workspace_unavailable",
                        ) from error
                if not participants:
                    continue
                service = str(row["service"] or "").lower()
                transport = (
                    "imessage"
                    if "imessage" in service
                    else "sms"
                    if service == "sms"
                    else "mms"
                    if service == "mms"
                    else "unknown"
                )
                direction = (
                    "outgoing"
                    if row["is_from_me"] == 1
                    else "incoming"
                    if row["is_from_me"] == 0
                    else "unknown"
                )
                message = Interaction(
                        row["source_id"],
                        "message",
                        transport,
                        when.isoformat(),
                        direction,
                        tuple(sorted(participants)),
                        None,
                    )
                if store is None:
                    messages.append(message)
                else:
                    store.add("messages", message)
            records = tuple(messages) if store is None else store.collection("messages")
            return SourceResult(row_count, records, row_count - len(records))


def select_people(contacts: SourceResult, contact_ids: set[int]) -> tuple[Contact, ...]:
    """Return owner-selected contacts; interaction filtering is a separate explicit step."""
    records = contacts.records.selected_contacts(contact_ids) if isinstance(contacts.records, RecordCollection) else contacts.records
    chosen = tuple(
        item
        for item in records
        if isinstance(item, Contact) and item.source_id in contact_ids
    )
    if len(chosen) != len(contact_ids) or len(chosen) > MAX_SELECTED_CONTACTS:
        raise ValueError("Choose known contact IDs within the supported source bound")
    return chosen


def interactions_for_contacts(
    chosen: tuple[Contact, ...], sources: tuple[SourceResult, SourceResult]
) -> tuple[Interaction, ...]:
    return tuple(iter_interactions_for_contacts(chosen, sources))


def iter_interactions_for_contacts(
    chosen: tuple[Contact, ...], sources: tuple[SourceResult, SourceResult]
) -> Iterator[Interaction]:
    phones = {phone for contact in chosen for phone in contact.phones}
    for source in sources:
        if isinstance(source.records, RecordCollection):
            yield from source.records.matched_interactions(phones)
        else:
            yield from (
                item for item in source.records
                if isinstance(item, Interaction) and any(p in phones for p in item.participants)
            )
