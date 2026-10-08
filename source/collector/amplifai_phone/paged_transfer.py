"""Immutable, bounded pages for a reviewed iPhone handoff.

The manifest identifies exact sanitized rows and their chained page hashes.
No backup database, body, attachment, email, or password enters these pages.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

from .ios_backup import IPhoneCapture
from .metadata import (
    Contact,
    Interaction,
    iter_interactions_for_contacts,
    select_people,
)
from .record_store import SelectionSnapshot, storage_failure

PAGE_MAX_BYTES = 128 * 1024
PAGE_MAX_ROWS = 100
EMPTY_CHAIN = "0" * 64


def _record(item: Contact | Interaction) -> dict[str, object]:
    if isinstance(item, Contact):
        return {"sourceId": item.source_id, "name": item.name, "phones": list(item.phones)}
    return {"sourceId": item.source_id, "kind": item.kind, "transport": item.transport,
            "occurredAt": item.occurred_at, "direction": item.direction,
            "participants": list(item.participants), "durationSeconds": item.duration_seconds}


def _encode(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


class PagedTransfer:
    def __init__(self, capture: IPhoneCapture, ids: set[int] | SelectionSnapshot, path: Path):
        if (not ids if isinstance(ids, set) else ids.count == 0) or capture.review_workspace is None or capture.record_store is None:
            raise ValueError("A private, reviewed iPhone capture is required")
        self.path = path
        self.workspace = capture.review_workspace
        self.workspace.checked_path(path)
        try:
            self.db = sqlite3.connect(path)
        except sqlite3.Error as error:
            raise storage_failure(error) from error
        try:
            self.db.execute("PRAGMA journal_mode=OFF")
            self.db.execute("PRAGMA synchronous=OFF")
            self.db.execute("CREATE TABLE page (category TEXT NOT NULL, cursor INTEGER NOT NULL, "
                            "chunk TEXT NOT NULL, sha256 TEXT NOT NULL, "
                            "PRIMARY KEY (category, cursor)) WITHOUT ROWID")
            chosen = select_people(capture.contacts, ids) if isinstance(ids, set) else None
            sources: dict[str, dict[str, object]] = {}
            contact_rows = iter(chosen) if chosen is not None else ids.rows("contacts")
            samples: list[str] = []
            def sampled_contacts() -> Iterator[Contact]:
                for item in contact_rows:
                    if len(samples) < 3:
                        samples.append(item.name or "Unnamed contact")
                    yield item
            for category, rows, seen in (
                ("contacts", sampled_contacts(), capture.contacts.rows_seen),
                ("calls", self._matched(chosen, capture, "call") if chosen is not None else ids.rows("calls"), capture.calls.rows_seen),
                ("messages", self._matched(chosen, capture, "message") if chosen is not None else ids.rows("messages"), capture.messages.rows_seen),
            ):
                if category in capture.missing_sources:
                    rows = iter(())
                count, pages, chain = self._write_source(category, rows)
                if seen < count:
                    raise ValueError("Source coverage counts are inconsistent")
                sources[category] = {"rowsSeen": seen, "rowsIncluded": count,
                                     "pageCount": pages, "chainSha256": chain}
            self.manifest = {
                "schema": 2, "platform": "iphone", "since": capture.since,
                "collectedAt": capture.collected_at,
                "missingSources": list(capture.missing_sources),
                "sampleContactNames": samples, "sources": sources,
            }
            self.sha256 = hashlib.sha256(_encode(self.manifest)).hexdigest()
            self.db.commit()
        except BaseException as error:
            self.db.close()
            path.unlink(missing_ok=True)
            if isinstance(error, sqlite3.Error):
                raise storage_failure(error) from error
            raise
        self.db.close()

    @staticmethod
    def _matched(chosen: tuple[Contact, ...], capture: IPhoneCapture, kind: str) -> Iterator[Interaction]:
        sources = (capture.calls, capture.messages)
        return (item for item in iter_interactions_for_contacts(chosen, sources) if item.kind == kind)

    def _write_source(self, category: str, rows: Iterator[Contact | Interaction]) -> tuple[int, int, str]:
        count = 0
        pages = 0
        chain = EMPTY_CHAIN
        batch: list[dict[str, object]] = []
        batch_size = 2

        def flush() -> None:
            nonlocal pages, chain, batch, batch_size
            if not batch:
                return
            chunk = _encode(batch)
            digest = hashlib.sha256(chunk).hexdigest()
            chain = hashlib.sha256((chain + digest).encode("ascii")).hexdigest()
            self.workspace.check_bound(len(chunk))
            self.db.execute("INSERT INTO page VALUES (?, ?, ?, ?)",
                            (category, pages, chunk.decode("ascii"), digest))
            self.workspace.check_bound()
            pages += 1
            batch = []
            batch_size = 2

        for item in rows:
            encoded = _encode(_record(item))
            if len(encoded) + 2 > PAGE_MAX_BYTES:
                raise ValueError("A sanitized record exceeds one transfer page")
            if batch and (len(batch) >= PAGE_MAX_ROWS or batch_size + len(encoded) + 1 > PAGE_MAX_BYTES):
                flush()
            batch.append(json.loads(encoded))
            batch_size += len(encoded) + (1 if len(batch) > 1 else 0)
            count += 1
        flush()
        return count, pages, chain

    def page(self, category: str, cursor: int) -> dict[str, object] | None:
        if category not in ("contacts", "calls", "messages") or cursor < 0:
            return None
        try:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
                row = db.execute("SELECT chunk, sha256 FROM page WHERE category = ? AND cursor = ?",
                                 (category, cursor)).fetchone()
        except sqlite3.Error as error:
            raise storage_failure(error) from error
        if row is None:
            return None
        page_count = self.manifest["sources"][category]["pageCount"]
        return {"format": "paged-records-v2", "category": category, "cursor": cursor,
                "chunk": row[0], "pageSha256": row[1],
                "nextCursor": cursor + 1 if cursor + 1 < page_count else None}

    def close(self) -> None:
        self.path.unlink(missing_ok=True)
