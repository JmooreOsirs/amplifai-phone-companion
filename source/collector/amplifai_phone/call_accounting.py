"""Bounded local-only call exclusion counts; never part of a browser handoff.

Parser reasons are mutually exclusive in date, cutoff, address order. Selection
counts describe the exact reviewed phone match, not account persistence or
historical completeness. No source values or row identities are retained here.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .source_diagnostics import (
    MAX_ATTEMPTS, MAX_BYTES, DiagnosticHistory, DiagnosticsStorageError,
    _HISTORY, _attempt_id, _encoded, _integer, _invalid, _locked, _marker,
    _pairs, _posix_directory, _retained, _windows_directory,
)

if TYPE_CHECKING:
    from .metadata import SourceResult

PARSER_FIELDS = (
    "rawRows", "invalidDateRows", "beforeCutoffRows", "invalidAddressRows",
    "validBeforeSelectionRows",
)
SELECTION_FIELDS = ("selectedRows", "selectedContactMismatchRows")


@dataclass(frozen=True)
class CallAccounting:
    raw_rows: int
    invalid_date_rows: int
    before_cutoff_rows: int
    invalid_address_rows: int
    valid_before_selection_rows: int


def validate_call_accounting(value: object) -> dict[str, int | None]:
    """Copy only fixed integer totals and reject incomplete or personal facts."""
    if type(value) is not dict or set(value) != set(PARSER_FIELDS + SELECTION_FIELDS):
        _invalid()
    if any(not _integer(value[field]) for field in PARSER_FIELDS):
        _invalid()
    if value["rawRows"] != sum(value[field] for field in PARSER_FIELDS[1:]):
        _invalid()
    selected, unmatched = (value[field] for field in SELECTION_FIELDS)
    if selected is None or unmatched is None:
        if selected is not None or unmatched is not None:
            _invalid()
    elif (not _integer(selected) or not _integer(unmatched)
          or selected + unmatched != value["validBeforeSelectionRows"]):
        _invalid()
    return {field: value[field] for field in PARSER_FIELDS + SELECTION_FIELDS}


def call_accounting(source: SourceResult, selected_rows: int | None = None) -> dict[str, int | None] | None:
    """Legacy/unavailable readers stay unmeasured; never infer reason counts."""
    accounting = source.call_accounting
    if accounting is None:
        return None
    if type(accounting) is not CallAccounting:
        _invalid()
    values = (accounting.raw_rows, accounting.invalid_date_rows,
              accounting.before_cutoff_rows, accounting.invalid_address_rows,
              accounting.valid_before_selection_rows)
    if (any(not _integer(value) for value in values)
            or not _integer(source.rows_seen) or not _integer(source.excluded)
            or source.rows_seen != accounting.raw_rows
            or len(source.records) != accounting.valid_before_selection_rows
            or source.excluded != sum(values[1:4])):
        _invalid()
    if selected_rows is not None and (not _integer(selected_rows)
                                      or selected_rows > accounting.valid_before_selection_rows):
        _invalid()
    return validate_call_accounting({
        **dict(zip(PARSER_FIELDS, values)),
        "selectedRows": selected_rows,
        "selectedContactMismatchRows": (None if selected_rows is None
                                        else accounting.valid_before_selection_rows - selected_rows),
    })


def validate_call_accounting_history(value: object) -> list[dict[str, object]]:
    if type(value) is not list or len(value) > MAX_ATTEMPTS:
        _invalid()
    result, seen = [], set()
    for entry in value:
        if type(entry) is not dict or set(entry) != {"attemptId", "createdAt", "callAccounting"}:
            _invalid()
        identity = _attempt_id(entry["attemptId"])
        if identity in seen or not _integer(entry["createdAt"]):
            _invalid()
        seen.add(identity)
        result.append({"attemptId": identity, "createdAt": entry["createdAt"],
                       "callAccounting": validate_call_accounting(entry["callAccounting"])})
    result.sort(key=lambda entry: (entry["createdAt"], entry["attemptId"]), reverse=True)
    if len(_encoded(result)) > MAX_BYTES:
        _invalid()
    return result


class CallAccountingHistory(DiagnosticHistory):
    """Separate local v1 history using the existing private storage safeguards."""

    @property
    def root(self):
        return super().root.with_name("call-accounting")

    def _read(self, directory):
        try:
            document = json.loads(directory.read(_HISTORY, MAX_BYTES).decode("ascii"),
                                  object_pairs_hook=_pairs)
        except FileNotFoundError:
            return []
        if (type(document) is not dict or set(document) != {"version", "attempts"}
                or type(document["version"]) is not int or document["version"] != 1):
            _invalid()
        return validate_call_accounting_history(document["attempts"])

    def _operate(self, attempt_id=None, accounting=None, created_at=None):
        try:
            root = self.root
            if any(part in {".", ".."} for part in root.parts):
                _invalid()
            now = int(time.time())
            if not _integer(now):
                _invalid()
            if attempt_id is not None:
                identity = _attempt_id(attempt_id, normalize=True)
                current = validate_call_accounting(accounting)
                created = now if created_at is None else created_at
                if not _integer(created) or created > now:
                    _invalid()
            guard = _windows_directory if os.name == "nt" else _posix_directory
            with guard(root, attempt_id is not None) as directory:
                if directory is None:
                    return []
                with _marker(directory), _locked(directory):
                    self._clean_temporary(directory)
                    previous = self._read(directory)
                    entries = _retained(previous, now)
                    if attempt_id is not None:
                        old = next((entry for entry in entries if entry["attemptId"] == identity), None)
                        entries = [entry for entry in entries if entry["attemptId"] != identity]
                        entries.append({"attemptId": identity, "createdAt": old["createdAt"] if old else created,
                                        "callAccounting": current})
                        entries = _retained(entries, now)
                    if entries != previous or attempt_id is not None:
                        self._write(directory, entries)
                    return validate_call_accounting_history(entries)
        except Exception:
            raise DiagnosticsStorageError() from None

    def record(self, attempt_id: str, accounting: object, *, created_at: int | None = None):
        """Retain this attempt's latest measured parser/review totals only."""
        return self._operate(attempt_id, accounting, created_at)
