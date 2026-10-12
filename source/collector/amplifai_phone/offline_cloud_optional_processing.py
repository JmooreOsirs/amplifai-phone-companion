"""Offline-only, source-local cloud processing dispositions.

The capture descriptor is immutable. An optional parser failure gets a separate
append-only outcome only after the source set and worker authority are verified.
This module is not imported by the released companion.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import apsw

from .metadata import UnsupportedSchema, read_calls, read_contacts, read_messages
from .offline_grouped_object_vfs import open_grouped_wal_source
from .offline_object_vfs import ObjectVfsError
from .offline_single_stage_capture import (
    ProcessingRanges,
    SingleStageCaptureLink,
    SingleStageSource,
)
from .record_store import RecordStore
from .workspace import WorkspaceError


@dataclass(frozen=True)
class ProcessingCoverage:
    category: str
    status: str
    reason_code: str | None
    rows_seen: int | None
    retained_rows: int | None
    output_sha256: str | None


class OutcomeJournal(Protocol):
    def processing_category(
        self,
        admission,
        epoch: int,
        category: str,
        disposition: str,
        descriptor_sha256: str,
    ) -> None: ...

    def record_processing_outcome(
        self,
        admission,
        epoch: int,
        category: str,
        descriptor_sha256: str,
        coverage: ProcessingCoverage,
    ) -> bool: ...


def _source_local_reason(error: BaseException) -> str | None:
    # A VFS/provider/storage/authorization failure can be wrapped by SQLite.
    # Never make that failure look like missing phone history.
    pending = [error]
    seen: set[int] = set()
    while pending:
        cursor = pending.pop()
        if id(cursor) in seen:
            continue
        seen.add(id(cursor))
        if isinstance(cursor, (ObjectVfsError, WorkspaceError)):
            return None
        pending.extend(
            nested
            for nested in (cursor.__cause__, cursor.__context__)
            if nested is not None
        )
    if type(error) is UnsupportedSchema:
        return "optional_schema_unsupported"
    if type(error) in (apsw.CorruptError, apsw.NotADBError):
        return "optional_sqlite_invalid"
    return None


def _digest_records(category: str, records) -> str:
    digest = hashlib.sha256()
    digest.update((category + "\n").encode("ascii"))
    for record in records:
        digest.update(
            json.dumps(asdict(record), sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        digest.update(b"\n")
    return digest.hexdigest()


def process_sealed_sources(
    link: SingleStageCaptureLink,
    sources: dict[str, SingleStageSource],
    epoch: int,
    store: RecordStore,
    journal: OutcomeJournal,
    since: datetime,
    *,
    check_cancel: Callable[[], None] | None = None,
) -> dict[str, ProcessingCoverage]:
    """Read a frozen selected set; Unavailable never means zero observed rows."""
    if (
        not link._sealed
        or link.backend.revoked
        or set(sources) - set(link.admission.categories)
    ):
        raise ObjectVfsError("cloud_processing_source_denied")
    if any(store.counts.values()):
        raise ObjectVfsError("cloud_processing_store_not_fresh")
    if "contacts" not in sources or set(sources) | set(
        link.capture_unavailable_reasons
    ) != set(link.admission.categories):
        raise ObjectVfsError("cloud_processing_source_incomplete")
    coverage: dict[str, ProcessingCoverage] = {}
    for category in (
        name
        for name in ("contacts", "calls", "messages")
        if name in link.admission.categories
    ):
        if check_cancel is not None:
            check_cancel()
        link.assert_current(link.admission, category)
        if link.backend.revoked:
            raise ObjectVfsError("cloud_processing_revoked")
        count, _size, descriptor = link.backend.descriptor_digest(category)
        disposition = "sealed" if category in sources else "unavailable"
        # The journal is authoritative for the current epoch, disposition and
        # immutable descriptor even when no Range read follows.
        journal.processing_category(
            link.admission, epoch, category, disposition, descriptor
        )
        if category not in sources:
            if (
                category == "contacts"
                or category not in link.capture_unavailable_reasons
            ):
                raise ObjectVfsError("cloud_processing_source_incomplete")
            result = ProcessingCoverage(
                category,
                "unavailable",
                link.capture_unavailable_reasons[category],
                None,
                None,
                None,
            )
        else:
            source = sources[category]
            if source.category != category or count <= 0:
                raise ObjectVfsError("cloud_processing_source_binding")
            backend = ProcessingRanges(link, category, epoch)

            def open_source(_path: Path, source=source, backend=backend):
                return open_grouped_wal_source(
                    source.source_set,
                    source.main_index,
                    source.wal_index,
                    backend,
                    link.backend.object_key,
                    link.receipt_key,
                    check_cancel=check_cancel,
                )

            path = link.index_root / f"{category}-source.sqlite3"
            try:
                if category == "contacts":
                    parsed = read_contacts(
                        path, check_cancel, store, open_connection=open_source
                    )
                elif category == "calls":
                    parsed = read_calls(
                        path, since, check_cancel, store, open_connection=open_source
                    )
                else:
                    parsed = read_messages(
                        path, since, check_cancel, store, open_connection=open_source
                    )
            except (UnsupportedSchema, apsw.CorruptError, apsw.NotADBError) as error:
                reason = _source_local_reason(error)
                if category == "contacts" or reason is None:
                    raise
                store.discard_category(category)
                result = ProcessingCoverage(
                    category, "unavailable", reason, None, None, None
                )
            else:
                result = ProcessingCoverage(
                    category,
                    "available",
                    None,
                    parsed.rows_seen,
                    len(parsed.records),
                    _digest_records(category, parsed.records),
                )
        if check_cancel is not None:
            check_cancel()
        link.assert_current(link.admission, category)
        if link.backend.revoked:
            raise ObjectVfsError("cloud_processing_revoked")
        journal.record_processing_outcome(
            link.admission, epoch, category, descriptor, result
        )
        coverage[category] = result
    return coverage
