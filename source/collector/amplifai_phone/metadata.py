"""Fail-closed, metadata-only readers for selected iPhone backup databases.

No query in this module reads message text, attributed bodies, attachments, or
contact notes. The source databases can nevertheless contain that material and
must remain in a private, short-lived directory.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

COCOA_EPOCH = 978307200
RETAINED_HISTORY_START = datetime(2001, 1, 1, tzinfo=UTC)
MAX_ROWS_PER_SOURCE = 1_000_000
MAX_SELECTED_CONTACTS = 25_000
ROW_CHECK_INTERVAL = 1024
PHONE_CHARS = re.compile(r"[^0-9+]")


class UnsupportedSchema(ValueError):
    """A device database shape is not one we can safely interpret."""


class SourceCapacityLimit(ValueError):
    """A bounded metadata/control frontier, not an unsupported phone schema."""


class SourceReadCapacityLimit(SourceCapacityLimit):
    """A per-source reader bound, distinct from backup/session safety bounds."""


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
    records: tuple[Contact | Interaction, ...]
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


def read_contacts(
    path: Path, check_callback: Callable[[], None] | None = None
) -> SourceResult:
    with _open_readonly(path) as db:
        _require_columns(db, "ABPerson", {"ROWID", "First", "Last"})
        _require_columns(db, "ABMultiValue", {"record_id", "property", "value"})
        rows = db.execute(
            "SELECT ROWID, First, Last FROM ABPerson ORDER BY ROWID LIMIT ?",
            (MAX_ROWS_PER_SOURCE + 1,),
        )
        values: dict[int, dict[str, set[str]]] = {}
        for index, row in enumerate(
            db.execute(
                "SELECT record_id, property, value FROM ABMultiValue WHERE property IN (3, 4) AND value IS NOT NULL"
            )
        ):
            if index >= MAX_ROWS_PER_SOURCE:
                raise SourceReadCapacityLimit("Contact value row limit exceeded")
            if check_callback is not None and index % ROW_CHECK_INTERVAL == 0:
                check_callback()
            owner = values.setdefault(
                row["record_id"], {"phones": set(), "emails": set()}
            )
            if row["property"] == 3:
                phone = normalize_phone(row["value"])
                if phone:
                    owner["phones"].add(phone)
            elif row["property"] == 4 and isinstance(row["value"], str):
                email = row["value"].strip().lower()
                if (
                    3 <= len(email) <= 254
                    and "@" in email
                    and not any(c.isspace() for c in email)
                ):
                    owner["emails"].add(email)
        contacts = []
        row_count = 0
        for row_count, row in enumerate(rows, 1):
            if row_count > MAX_ROWS_PER_SOURCE:
                raise SourceReadCapacityLimit("Contact row limit exceeded")
            if check_callback is not None and row_count % ROW_CHECK_INTERVAL == 0:
                check_callback()
            fields = values.get(row["ROWID"], {"phones": set(), "emails": set()})
            if len(fields["phones"]) > 20 or len(fields["emails"]) > 20:
                raise SourceReadCapacityLimit(
                    "Contact has more phone or email values than the handoff contract supports"
                )
            name = " ".join(
                part.strip()
                for part in (row["First"], row["Last"])
                if isinstance(part, str) and part.strip()
            )
            if name or fields["phones"] or fields["emails"]:
                contacts.append(
                    Contact(
                        row["ROWID"],
                        name[:240],
                        tuple(sorted(fields["phones"])),
                        tuple(sorted(fields["emails"])),
                    )
                )
        return SourceResult(row_count, tuple(contacts), row_count - len(contacts))


def read_calls(
    path: Path, since: datetime, check_callback: Callable[[], None] | None = None
) -> SourceResult:
    with _open_readonly(path) as db:
        _require_columns(
            db,
            "ZCALLRECORD",
            {"ZDATE", "ZDURATION", "ZADDRESS", "ZORIGINATED", "ZANSWERED"},
        )
        rows = db.execute(
            "SELECT ROWID, ZDATE, ZDURATION, ZADDRESS, ZORIGINATED, ZANSWERED "
            "FROM ZCALLRECORD ORDER BY ROWID LIMIT ?",
            (MAX_ROWS_PER_SOURCE + 1,),
        )
        calls = []
        row_count = 0
        for row_count, row in enumerate(rows, 1):
            if row_count > MAX_ROWS_PER_SOURCE:
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
            calls.append(
                Interaction(
                    row["ROWID"],
                    "call",
                    "call",
                    when.isoformat(),
                    direction,
                    (phone,),
                    duration_seconds,
                )
            )
        return SourceResult(row_count, tuple(calls), row_count - len(calls))


def read_messages(
    path: Path, since: datetime, check_callback: Callable[[], None] | None = None
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
            "ORDER BY message.ROWID LIMIT ?",
            (MAX_ROWS_PER_SOURCE + 1,),
        )
        chat_phones: dict[int, set[str]] = {}
        message_chats: dict[int, set[int]] = {}
        if has_chat:
            for index, row in enumerate(
                db.execute(
                    "SELECT chj.chat_id, handle.id FROM chat_handle_join AS chj "
                    "JOIN handle ON chj.handle_id = handle.ROWID"
                )
            ):
                if index >= MAX_ROWS_PER_SOURCE:
                    raise SourceReadCapacityLimit("Message participant row limit exceeded")
                if check_callback is not None and index % ROW_CHECK_INTERVAL == 0:
                    check_callback()
                phone = normalize_phone(row["id"])
                if phone:
                    phones = chat_phones.setdefault(row["chat_id"], set())
                    if len(phones) <= 50:
                        phones.add(phone)
            for index, row in enumerate(
                db.execute("SELECT message_id, chat_id FROM chat_message_join")
            ):
                if index >= MAX_ROWS_PER_SOURCE:
                    raise SourceReadCapacityLimit("Message chat row limit exceeded")
                if check_callback is not None and index % ROW_CHECK_INTERVAL == 0:
                    check_callback()
                if row["chat_id"] in chat_phones:
                    message_chats.setdefault(row["message_id"], set()).add(row["chat_id"])
        messages = []
        row_count = 0
        for row_count, row in enumerate(rows, 1):
            if row_count > MAX_ROWS_PER_SOURCE:
                raise SourceReadCapacityLimit("Message row limit exceeded")
            if check_callback is not None and row_count % ROW_CHECK_INTERVAL == 0:
                check_callback()
            when = _cocoa_datetime(row["date"])
            participants = {
                phone
                for chat_id in message_chats.get(row["source_id"], ())
                for phone in chat_phones.get(chat_id, ())
            }
            sender = normalize_phone(row["sender"])
            if sender:
                participants.add(sender)
            if (
                when is None
                or when < since
                or not participants
                or len(participants) > 50
            ):
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
            messages.append(
                Interaction(
                    row["source_id"],
                    "message",
                    transport,
                    when.isoformat(),
                    direction,
                    tuple(sorted(participants)),
                    None,
                )
            )
        return SourceResult(row_count, tuple(messages), row_count - len(messages))


def select_people(contacts: SourceResult, contact_ids: set[int]) -> tuple[Contact, ...]:
    """Return owner-selected contacts; interaction filtering is a separate explicit step."""
    chosen = tuple(
        item
        for item in contacts.records
        if isinstance(item, Contact) and item.source_id in contact_ids
    )
    if len(chosen) != len(contact_ids) or len(chosen) > MAX_SELECTED_CONTACTS:
        raise ValueError("Choose known contact IDs within the supported source bound")
    return chosen


def interactions_for_contacts(
    chosen: tuple[Contact, ...], sources: tuple[SourceResult, SourceResult]
) -> tuple[Interaction, ...]:
    phones = {phone for contact in chosen for phone in contact.phones}
    return tuple(
        item
        for source in sources
        for item in source.records
        if isinstance(item, Interaction) and any(p in phones for p in item.participants)
    )
