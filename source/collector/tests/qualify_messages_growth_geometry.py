"""Synthetic-only reproduction of the observed byte geometry; no phone access."""
import hashlib
import json
import sqlite3
import time
from contextlib import closing

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from test_sqlite_encrypted_size_compat import EncryptedSizeCompatibilityTests
from amplifai_phone.ios_backup import DATABASES

ACTUAL, DECLARED, RAW = 794722304, 794714112, 794722320


def case(page, fault=None):
    test = EncryptedSizeCompatibilityTests()
    test.setUp()
    try:
        backup = test._backup(("messages",), delta=-8192, page_size=page)
        path = test.root / f"messages-{test.backup_path.name}.sqlite"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE fixture_payload (value BLOB)")
            db.execute("INSERT INTO fixture_payload VALUES (zeroblob(?))", ((ACTUAL // page - 4) * (page - 4),))
            if db.execute("PRAGMA page_count").fetchone()[0] * page != ACTUAL:
                raise RuntimeError("Synthetic size construction failed")
            if db.execute("PRAGMA integrity_check(1)").fetchmany(2) != [("ok",)]:
                raise RuntimeError("Positive synthetic source is invalid")
        if fault:
            with path.open("r+b") as stream:
                stream.seek(92 if fault == "stale-header" else page)
                stream.write(b"\xff" * (4 if fault == "stale-header" else 1))
        relative = DATABASES["messages"]
        file_id = hashlib.sha1(f"HomeDomain-{relative}".encode()).hexdigest()
        source = test.backup_path / file_id[:2] / file_id
        padder = padding.PKCS7(128).padder()
        cipher = Cipher(algorithms.AES(test.file_key), modes.CBC(bytes(16))).encryptor()
        digest = hashlib.sha1()
        with path.open("rb") as plain, source.open("wb") as encrypted:
            while chunk := plain.read(256 * 1024):
                data = cipher.update(padder.update(chunk))
                encrypted.write(data)
                digest.update(data)
            data = cipher.update(padder.finalize()) + cipher.finalize()
            encrypted.write(data)
            digest.update(data)
        if source.stat().st_size != RAW:
            raise RuntimeError("Synthetic ciphertext size differs")
        connection = backup._manifest_db._conn
        connection.execute("UPDATE Files SET file=? WHERE fileID=?", (test._archive(relative, DECLARED, digest.digest()), file_id))
        connection.commit()
        samples = []
        from amplifai_phone.ios_backup import _extract_database
        from amplifai_phone.metadata import UnsupportedSchema
        started = time.monotonic()
        try:
            accepted = _extract_database(backup, relative, test.copy_path / "messages", workspace=test.workspace,
                                         payload_diagnostic_callback=samples.append)
            code = samples[-1]["size_difference_validation"]
        except UnsupportedSchema as error:
            accepted = False
            code = samples[-1].get("size_difference_validation")
            if error.code != "selected_payload_length":
                raise
        record = samples[-1]
        return {"pageSize": page, "fault": fault, "actual": path.stat().st_size,
                "declared": DECLARED, "raw": source.stat().st_size, "accepted": bool(accepted),
                "validation": code, "elapsedSeconds": round(time.monotonic() - started, 3),
                "producerMatched": record.get("producer_digest_matches"),
                "copyMatched": record.get("plaintext_copy_checksum_matches")}
    finally:
        test.doCleanups()


if __name__ == "__main__":
    for page, fault in ((4096, None), (8192, None), (4096, "stale-header"), (4096, "corrupt-page")):
        result = case(page, fault)
        expected = "unsupported_size_growth" if fault == "stale-header" else "structural_invalid" if fault else "validated_messages_two_page_growth" if page == 4096 else "validated_messages_one_page_growth"
        if (result["accepted"] != (fault is None) or result["validation"] != expected
                or result["producerMatched"] is not True or result["copyMatched"] is not True):
            raise AssertionError("Synthetic encrypted geometry qualification failed: " + json.dumps(result))
        print(json.dumps(result), flush=True)
