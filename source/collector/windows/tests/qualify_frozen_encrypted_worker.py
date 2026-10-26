"""Disposable Windows proof of the exact frozen encrypted-main worker.

No device or account is opened. The marked workspace and all three SQLite
files contain fictional rows and are removed by the ordinary workspace exit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from amplifai_phone.workspace import ROOT_MARKER, SessionWorkspace, default_sessions_root


SCHEMAS = {
    "contacts": (
        "CREATE TABLE ABPerson (ROWID INTEGER PRIMARY KEY, First TEXT, Last TEXT)",
        "CREATE TABLE ABMultiValue (record_id INTEGER, property INTEGER, value TEXT)",
        "INSERT INTO ABPerson VALUES (7, 'Fictional', 'Person')",
        "INSERT INTO ABMultiValue VALUES (7, 3, '+12025550123')",
    ),
    "calls": (
        "CREATE TABLE ZCALLRECORD (Z_PK INTEGER PRIMARY KEY, ZDATE, ZDURATION, "
        "ZADDRESS, ZORIGINATED, ZANSWERED)",
        "INSERT INTO ZCALLRECORD VALUES (7, 800000000, 60, '+12025550123', 1, 1)",
    ),
    "messages": (
        "CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT)",
        "CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date, service, "
        "is_from_me, handle_id, text)",
        "INSERT INTO handle VALUES (1, '+12025550123')",
        "INSERT INTO message VALUES (7, 800000000, 'SMS', 0, 1, NULL)",
    ),
}
EXPECTED = {
    "contacts": "validated_contacts_tables",
    "calls": "validated_call_database",
    "messages": "validated_messages_database",
}


def worker(helper: Path, request: dict[str, object]) -> dict[str, object]:
    result = subprocess.run(
        [str(helper), "--internal-encrypted-size-validation"],
        input=json.dumps(request) + "\n", text=True, capture_output=True,
        timeout=35, check=False, cwd=helper.parent,
    )
    if result.returncode != 0 or result.stderr or result.stdout.count("\n") != 1:
        raise AssertionError("Frozen worker did not return one clean frame")
    packet = json.loads(result.stdout)
    if not isinstance(packet, dict) or set(packet) != {"code", "copy_checksum_matches"}:
        raise AssertionError("Frozen worker returned an invalid frame")
    return packet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helper", required=True, type=Path)
    helper = parser.parse_args().helper.resolve(strict=True)
    if helper.name != "AmplifaiPhoneHelper.exe":
        raise ValueError("Only the frozen candidate helper is accepted")
    root = default_sessions_root()
    root_existed, parent_existed = root.exists(), root.parent.exists()
    with SessionWorkspace() as session:
        results: dict[str, str] = {}
        for category, statements in SCHEMAS.items():
            path = session / f"fictional-{category}.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("PRAGMA page_size=4096")
                for statement in statements:
                    db.execute(statement)
                if category == "messages":
                    db.execute("CREATE TABLE fictional_padding(value BLOB)")
            size = path.stat().st_size
            request: dict[str, object] = {
                "category": category, "path": str(path), "actual_size": size,
                "manifest_size": size + 4096,
                "copied_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            packet = worker(helper, request)
            if packet != {"code": EXPECTED[category], "copy_checksum_matches": True}:
                raise AssertionError(f"Frozen {category} worker rejected valid fictional SQLite")
            results[category] = packet["code"]
            if category == "messages":
                growth = worker(helper, {**request, "manifest_size": size - 4096})
                if growth != {"code": "validated_messages_one_page_growth", "copy_checksum_matches": True}:
                    raise AssertionError("Frozen worker rejected proven one-page Messages geometry")
                larger = worker(helper, {**request, "manifest_size": size - 8192})
                if larger != {"code": "validated_messages_two_page_growth", "copy_checksum_matches": True}:
                    raise AssertionError("Frozen worker rejected proven two-4096-page Messages geometry")
                results["messagesOnePage"] = growth["code"]
                results["messagesTwoPages"] = larger["code"]
                excess = worker(helper, {**request, "manifest_size": size - 12288})
                if excess != {"code": "unsupported_size_growth", "copy_checksum_matches": True}:
                    raise AssertionError("Frozen worker admitted three Messages pages")
                results["messagesThreePages"] = excess["code"]
                with path.open("r+b") as damaged:
                    damaged.seek(4096)
                    damaged.write(b"\xff")
                broken = worker(helper, {**request, "manifest_size": size - 8192,
                                        "copied_sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
                if broken != {"code": "structural_invalid", "copy_checksum_matches": True}:
                    raise AssertionError("Frozen worker admitted a corrupt two-page-growth database")
                results["messagesTwoPagesCorrupt"] = broken["code"]
            if category == "calls":
                rejected = worker(helper, {**request, "copied_sha256": "0" * 64})
                if rejected != {"code": "copy_checksum_mismatch", "copy_checksum_matches": False}:
                    raise AssertionError("Frozen worker accepted a wrong copy checksum")
    if session.exists():
        raise AssertionError("Fictional marked session was not removed")
    # A newly created marker-only root is fixture residue. Remove only that
    # exact empty root/parent so the next MSI gate starts clean. Preserve any
    # pre-existing root; never recursively remove unexpected entries.
    if not root_existed:
        marker = root / ROOT_MARKER
        if (root.is_symlink() or root.is_junction() or marker.is_symlink()
                or set(root.iterdir()) != {marker}
                or marker.read_bytes() != b"AMPLIFAI_PHONE_SESSIONS_V1\n"):
            raise AssertionError("Unexpected entries in the fixture-created root")
        marker.unlink()
        root.rmdir()
    if not parent_existed:
        if root.parent.is_symlink() or root.parent.is_junction():
            raise AssertionError("Fixture parent identity changed")
        root.parent.rmdir()
    print(json.dumps({"status": "pass", "frozenEncryptedMain": results,
                      "wrongCopyChecksum": "rejected", "ownedSessionCleanup": "complete"},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
