"""App-private, short-lived index of sanitized phone metadata.

Only allowlisted contact and interaction fields reach this database. The owner
of the enclosing SessionWorkspace closes it when local review ends.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path

from .workspace import SessionWorkspace, WorkspaceError


def storage_failure(error: sqlite3.Error) -> WorkspaceError:
    code = getattr(error, "sqlite_errorcode", None)
    return WorkspaceError(
        "Private metadata storage could not complete a database operation",
        code="workspace_low_space" if code is not None and code & 0xff == sqlite3.SQLITE_FULL
        else "workspace_unavailable",
    )


class RecordStore:
    def __init__(self, path: Path, workspace: SessionWorkspace | None = None):
        self.workspace = workspace
        self.path = workspace.checked_path(path) if workspace is not None else path
        try:
            self.db = sqlite3.connect(self.path)
            self.db.execute("PRAGMA journal_mode=OFF")
            self.db.execute("PRAGMA synchronous=OFF")
            self.db.execute("PRAGMA cache_size=-2048")
            self.db.execute("CREATE TABLE record (category TEXT NOT NULL, position INTEGER NOT NULL, "
                            "source_id INTEGER NOT NULL, name TEXT, value TEXT NOT NULL, "
                            "PRIMARY KEY (category, position)) WITHOUT ROWID")
            self.db.execute("CREATE INDEX record_identity ON record (category, source_id)")
            self.db.execute("CREATE TABLE phone (category TEXT NOT NULL, position INTEGER NOT NULL, "
                            "number TEXT NOT NULL, PRIMARY KEY (category, position, number)) WITHOUT ROWID")
            self.db.execute("CREATE INDEX phone_number ON phone (number, category, position)")
            self.db.execute("CREATE TABLE selection (ordinal INTEGER PRIMARY KEY, source_id INTEGER NOT NULL UNIQUE, "
                            "contact_position INTEGER NOT NULL UNIQUE)")
        except sqlite3.Error as error:
            raise storage_failure(error) from error
        self.counts = {"contacts": 0, "calls": 0, "messages": 0}
        self.selection_review_id: str | None = None
        self.selection_total = 0
        self.selection_count = 0
        self.selection_hash = hashlib.sha256()

    def add(self, category: str, record: object) -> None:
        from .metadata import Contact, Interaction

        if category not in self.counts:
            raise ValueError("Invalid metadata category")
        position = self.counts[category]
        if self.workspace is not None and position % 128 == 0:
            self.workspace.check_bound()
        if isinstance(record, Contact) and category == "contacts":
            value = {"source_id": record.source_id, "name": record.name,
                     "phones": record.phones, "emails": record.emails}
            phones = record.phones
            name = record.name.casefold()
        elif isinstance(record, Interaction) and category in ("calls", "messages"):
            value = {"source_id": record.source_id, "kind": record.kind,
                     "transport": record.transport, "occurred_at": record.occurred_at,
                     "direction": record.direction, "participants": record.participants,
                     "duration_seconds": record.duration_seconds}
            phones = record.participants
            name = None
        else:
            raise TypeError("Invalid sanitized metadata record")
        try:
            self.db.execute("INSERT INTO record VALUES (?, ?, ?, ?, ?)",
                            (category, position, record.source_id, name,
                             json.dumps(value, separators=(",", ":"))))
            self.db.executemany("INSERT OR IGNORE INTO phone VALUES (?, ?, ?)",
                                ((category, position, phone) for phone in phones))
        except sqlite3.Error as error:
            raise storage_failure(error) from error
        self.counts[category] += 1

    def collection(self, category: str) -> RecordCollection:
        try:
            self.db.commit()
        except sqlite3.Error as error:
            raise storage_failure(error) from error
        if self.workspace is not None:
            self.workspace.check_bound()
        return RecordCollection(self, category)

    def discard_category(self, category: str) -> None:
        """Remove every partially parsed optional source before marking it missing."""
        if category not in ("calls", "messages"):
            raise ValueError("Only optional sources can be discarded")
        try:
            with self.db:
                self.db.execute("DELETE FROM phone WHERE category = ?", (category,))
                self.db.execute("DELETE FROM record WHERE category = ?", (category,))
        except sqlite3.Error as error:
            raise storage_failure(error) from error
        self.counts[category] = 0

    def append_selection(self, review_id: str, cursor: int, ids: list[int], total: int) -> SelectionSnapshot | None:
        """Accept one ordered page; only a complete, exact selection can be reviewed."""
        if cursor == 0:
            try:
                self.db.execute("DELETE FROM selection")
            except sqlite3.Error as error:
                raise storage_failure(error) from error
            self.selection_review_id = review_id
            self.selection_total = total
            self.selection_count = 0
            self.selection_hash = hashlib.sha256()
        if (review_id != self.selection_review_id or total != self.selection_total
                or cursor != self.selection_count or not ids or len(ids) > 1000
                or cursor + len(ids) > total or total > self.counts["contacts"]):
            raise ValueError("Invalid selection page")
        try:
            with self.db:
                for source_id in ids:
                    if not isinstance(source_id, int) or isinstance(source_id, bool):
                        raise TypeError("Invalid contact ID")
                    row = self.db.execute(
                        "SELECT position FROM record WHERE category = 'contacts' AND source_id = ?",
                        (source_id,),
                    ).fetchone()
                    if row is None:
                        raise ValueError("Unknown contact ID")
                    self.db.execute("INSERT INTO selection VALUES (?, ?, ?)",
                                    (self.selection_count, source_id, row[0]))
                    self.selection_hash.update(f"{source_id}\n".encode("ascii"))
                    self.selection_count += 1
        except sqlite3.Error as error:
            raise storage_failure(error) from error
        except (ValueError, TypeError) as error:
            try:
                self.db.execute("DELETE FROM selection")
                self.db.commit()
            except sqlite3.Error as storage_error:
                raise storage_failure(storage_error) from storage_error
            self.selection_review_id = None
            self.selection_count = 0
            raise ValueError("Invalid selection page") from error
        if self.selection_count == total:
            return SelectionSnapshot(self, review_id, total, self.selection_hash.hexdigest())
        return None

    def selected_collection(self, category: str) -> Iterator:
        collection = RecordCollection(self, category)
        with collection._readonly() as db:
            if category == "contacts":
                rows = db.execute(
                    "SELECT r.value FROM selection s JOIN record r ON r.category = 'contacts' "
                    "AND r.position = s.contact_position ORDER BY s.ordinal"
                )
            elif category in ("calls", "messages"):
                rows = db.execute(
                    "SELECT r.value FROM record r JOIN (SELECT DISTINCT ip.position FROM selection s "
                    "JOIN phone cp ON cp.category = 'contacts' AND cp.position = s.contact_position "
                    "JOIN phone ip ON ip.number = cp.number AND ip.category = ?) matched "
                    "ON r.category = ? AND r.position = matched.position ORDER BY r.position",
                    (category, category),
                )
            else:
                raise ValueError("Invalid selection source")
            for (value,) in rows:
                yield collection._decode(value)

    def close(self) -> None:
        self.db.close()


class RecordCollection(Sequence):
    def __init__(self, store: RecordStore, category: str):
        self.store = store
        self.category = category

    def __len__(self) -> int:
        return self.store.counts[self.category]

    def _decode(self, value: str):
        from .metadata import Contact, Interaction

        data = json.loads(value)
        if self.category == "contacts":
            return Contact(data["source_id"], data["name"], tuple(data["phones"]), tuple(data["emails"]))
        return Interaction(data["source_id"], data["kind"], data["transport"], data["occurred_at"],
                           data["direction"], tuple(data["participants"]), data["duration_seconds"])

    @contextmanager
    def _readonly(self) -> Iterator[sqlite3.Connection]:
        try:
            with closing(sqlite3.connect(self.store.path.as_uri() + "?mode=ro", uri=True)) as db:
                yield db
        except sqlite3.Error as error:
            raise storage_failure(error) from error

    def __iter__(self) -> Iterator:
        with self._readonly() as db:
            for (value,) in db.execute(
                "SELECT value FROM record WHERE category = ? ORDER BY position", (self.category,)
            ):
                yield self._decode(value)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[position] for position in range(*index.indices(len(self))))
        if not isinstance(index, int):
            raise TypeError("Record position must be an integer")
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        with self._readonly() as db:
            row = db.execute(
                "SELECT value FROM record WHERE category = ? AND position = ?",
                (self.category, index),
            ).fetchone()
        if row is None:
            raise IndexError(index)
        return self._decode(row[0])

    def selected_contacts(self, ids: set[int]) -> Iterator:
        with self._readonly() as db:
            for source_id in sorted(ids):
                row = db.execute(
                    "SELECT value FROM record WHERE category = 'contacts' AND source_id = ?",
                    (source_id,),
                ).fetchone()
                if row is not None:
                    yield self._decode(row[0])

    def matched_interactions(self, phones: set[str]) -> Iterator:
        if not phones:
            return
        with self._readonly() as db:
            db.execute("CREATE TEMP TABLE selected_phone (number TEXT PRIMARY KEY) WITHOUT ROWID")
            db.executemany("INSERT INTO selected_phone VALUES (?)", ((phone,) for phone in phones))
            for (value,) in db.execute(
                "SELECT record.value FROM record JOIN "
                "(SELECT DISTINCT phone.position FROM phone JOIN selected_phone "
                "ON selected_phone.number = phone.number WHERE phone.category = ?) AS matched "
                "ON record.category = ? AND record.position = matched.position "
                "ORDER BY record.position", (self.category, self.category),
            ):
                yield self._decode(value)

    def page(self, query: str, cursor: int, size: int):
        needle = query.strip().casefold()
        with self._readonly() as db:
            rows = db.execute(
                "SELECT position, value FROM record WHERE category = 'contacts' AND position >= ? "
                "AND (? = '' OR instr(name, ?) > 0) ORDER BY position LIMIT ?",
                (cursor, needle, needle, size + 1),
            ).fetchall()
        return [(position, self._decode(value)) for position, value in rows]


class SelectionSnapshot:
    def __init__(self, store: RecordStore, review_id: str, count: int, sha256: str):
        self.store = store
        self.review_id = review_id
        self.count = count
        self.sha256 = sha256

    def rows(self, category: str) -> Iterator:
        if self.store.selection_review_id != self.review_id or self.store.selection_count != self.count:
            raise ValueError("Reviewed selection changed")
        return self.store.selected_collection(category)
