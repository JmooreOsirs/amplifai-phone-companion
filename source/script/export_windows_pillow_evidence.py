"""Bind locked Pillow wheel source metadata to the exact MSI-installed native files."""
from __future__ import annotations

import base64
import csv
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path

VERSION = "12.3.0"
DIST_INFO = f"pillow-{VERSION}.dist-info"
NATIVE_NAMES = ("_avif", "_imaging", "_imagingcms", "_imagingft", "_imagingmath", "_imagingtk", "_webp")
METADATA_LIMITS = {"METADATA": 128 * 1024, "WHEEL": 16 * 1024, "RECORD": 256 * 1024,
                   f"sboms/pillow-{VERSION}.cdx.json": 256 * 1024}


def bounded_bytes(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Pillow evidence requires regular files")
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Pillow evidence file exceeds its fixed bound")
    return data


def export(installed: Path, candidate_receipt: Path, destination: Path, msi_sha256: str) -> dict:
    distribution = importlib.metadata.distribution("Pillow")
    if distribution.version != VERSION:
        raise ValueError("Pillow evidence must use the exact locked wheel version")
    files = {str(path).replace("\\", "/"): path for path in distribution.files or ()}
    metadata = {}
    for relative, limit in METADATA_LIMITS.items():
        name = f"{DIST_INFO}/{relative}"
        if name not in files:
            raise ValueError("Locked Pillow wheel source metadata is missing")
        metadata[relative] = bounded_bytes(Path(distribution.locate_file(files[name])), limit)
    json.loads(metadata[f"sboms/pillow-{VERSION}.cdx.json"])
    wheel_records = {row[0]: row[1:] for row in csv.reader(io.StringIO(metadata["RECORD"].decode("utf-8"))) if row}
    receipt_bytes = bounded_bytes(candidate_receipt, 4 * 1024 * 1024)
    receipt = json.loads(receipt_bytes)
    records = {record["path"]: record for record in receipt["files"]}
    native = []
    for name in NATIVE_NAMES:
        wheel_path = f"PIL/{name}.cp312-win_amd64.pyd"
        installed_path = f"resources/phone-helper/_internal/{wheel_path}"
        if wheel_path not in files or installed_path not in records:
            raise ValueError("Pillow native evidence is incomplete")
        data = bounded_bytes(Path(distribution.locate_file(files[wheel_path])), 16 * 1024 * 1024)
        digest = hashlib.sha256(data).hexdigest()
        record = records[installed_path]
        encoded_hash = base64.urlsafe_b64encode(bytes.fromhex(digest)).rstrip(b"=").decode("ascii")
        if wheel_records.get(wheel_path) != ["sha256=" + encoded_hash, str(len(data))]:
            raise ValueError("Installed Pillow wheel bytes differ from its RECORD")
        if len(data) != record["bytes"] or digest != record["sha256"]:
            raise ValueError("Pillow wheel native bytes differ from the MSI candidate")
        shipped = bounded_bytes(installed / installed_path, 16 * 1024 * 1024)
        if len(shipped) != len(data) or hashlib.sha256(shipped).hexdigest() != digest:
            raise ValueError("MSI-installed Pillow native bytes differ from the locked wheel")
        native.append({"wheelPath": wheel_path, "installedPath": installed_path,
                       "bytes": len(data), "sha256": digest})
    destination.mkdir()
    exported = []
    for relative, data in metadata.items():
        name = relative.rsplit("/", 1)[-1]
        (destination / name).write_bytes(data)
        exported.append({"path": name, "wheelPath": f"{DIST_INFO}/{relative}", "bytes": len(data),
                         "sha256": hashlib.sha256(data).hexdigest()})
    result = {"status": "locked-pillow-wheel-to-actual-msi-native-byte-match", "schema": 1,
              "pillowVersion": VERSION, "msiSha256": msi_sha256,
              "candidateReceiptSha256": hashlib.sha256(receipt_bytes).hexdigest(),
              "packageInventorySha256": receipt["package_inventory_sha256"],
              "metadata": exported, "nativeFiles": native}
    (destination / "pillow-native-evidence.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result
