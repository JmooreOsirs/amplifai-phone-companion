"""Append final signed-app custody to an unchanged qualified source payload."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import producer
import release


def finalize_source(source: Path, output: Path, inventory: Path, source_commit: str) -> dict:
    signed = release.exact_json(inventory.read_bytes())
    actual = {row["path"]: row for row in signed["files"]}
    if len(actual) != len(signed["files"]):
        raise ValueError("duplicate signed app inventory record")
    with zipfile.ZipFile(source) as before:
        mapping = producer.source_map(before)
        if mapping["sourceCommit"] != source_commit:
            raise ValueError("final source identity differs")
        bindings = []
        for path, record in mapping["native"].items():
            if path not in actual:
                raise ValueError("final app omits source-mapped native code")
            bindings.append({"path": path, "preSignSha256": record["preSignSha256"],
                             "postSignSha256": actual[path]["sha256"],
                             "sourceInputs": record["sourceInputs"], "noticeInputs": record["noticeInputs"]})
        appended = producer.APPEND_PATHS
        receipt = {"schema": 1, "sourceCommit": source_commit, "sourceInputSha256": release.digest(source),
                   "signedInventorySha256": release.digest(inventory), "nativeBindings": bindings, "files": []}
        with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as after:
            for item in before.infolist():
                with before.open(item) as stream, after.open(item, "w") as target:
                    checksum = hashlib.sha256()
                    while block := stream.read(1024 * 1024):
                        checksum.update(block)
                        target.write(block)
                receipt["files"].append({"path": item.filename, "bytes": item.file_size, "sha256": checksum.hexdigest()})
            after.writestr(appended[0], inventory.read_bytes())
            receipt["files"].append({"path": appended[0], "bytes": inventory.stat().st_size, "sha256": release.digest(inventory)})
            after.writestr(appended[1], json.dumps(receipt, sort_keys=True, indent=2).encode() + b"\n")
    verify_final_source(output, source_commit)
    return {"sourceCommit": source_commit, "sourceInputSha256": receipt["sourceInputSha256"],
            "sourceSha256": release.digest(output), "signedInventorySha256": receipt["signedInventorySha256"],
            "nativeBindings": len(bindings), "completeMemberReadback": True}


def verify_final_source(archive: Path, source_commit: str) -> dict:
    with zipfile.ZipFile(archive) as zipped:
        members = producer.source_members(zipped)
        receipt_name = producer.APPEND_PATHS[1]
        if receipt_name not in members or members[receipt_name].file_size > 8 * 1024**2:
            raise ValueError("bounded source finalization receipt required")
        receipt = release.exact_json(zipped.read(receipt_name))
        rows = receipt["files"]
        if (type(receipt.get("schema")) is not int or receipt["schema"] != 1 or receipt.get("sourceCommit") != source_commit
                or len({row["path"] for row in rows}) != len(rows)
                or set(members) != {row["path"] for row in rows} | {receipt_name}):
            raise ValueError("final source member set or identity differs")
        for row in rows:
            item = members[row["path"]]
            with zipped.open(item) as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if item.file_size != row["bytes"] or checksum != row["sha256"]:
                raise ValueError("final source readback differs")
        inventory_bytes = zipped.read(producer.APPEND_PATHS[0])
        if hashlib.sha256(inventory_bytes).hexdigest() != receipt["signedInventorySha256"]:
            raise ValueError("source/signature inventory binding differs")
        mapping = release.exact_json(zipped.read("release-source-map.json"))
        inventory = release.exact_json(inventory_bytes)
        signed = {row["path"]: row["sha256"] for row in inventory["files"]}
        bindings = {row["path"]: row for row in receipt["nativeBindings"]}
        if (mapping["sourceCommit"] != source_commit or set(bindings) != set(mapping["native"])
                or len(bindings) != len(receipt["nativeBindings"]) or len(signed) != len(inventory["files"])):
            raise ValueError("final native source coverage differs")
        for path, row in bindings.items():
            original = mapping["native"][path]
            if (row["preSignSha256"] != original["preSignSha256"] or row["postSignSha256"] != signed.get(path)
                    or row["sourceInputs"] != original["sourceInputs"] or row["noticeInputs"] != original["noticeInputs"]):
                raise ValueError("final native source/signature mapping differs")
    return receipt
