#!/usr/bin/env python3
"""Extract exact embedded notice files from hash-verified PyPI sdists.

This is mechanical evidence collection, not a final third-party notices file.
No archive member is executed or blindly extracted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

EXTRA_EVIDENCE = {
    "bpylist2": ("bpylist2-4.1.1/README.rst", "full_mit_text_in_readme"),
    "hexdump": ("README.txt", "public_domain_declaration_in_readme"),
}
LOGURU_TAG_LICENSE = (
    "https://raw.githubusercontent.com/Delgan/loguru/"
    "ae3bfd1b85b6b4a3db535f69b975687c79498be4/LICENSE"
)
LOGURU_LICENSE_SHA256 = "b35d026cc7aca9d5859a02eb87ddf7a386a24c986838651bd1f283f94e003327"

def safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe archive member: {name}")
    return path


def read_member(archive: Path, member: str) -> bytes:
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as opened:
            info = opened.getinfo(member)
            if info.is_dir():
                raise ValueError(f"notice member is directory: {member}")
            return opened.read(info)
    with tarfile.open(archive, "r:*") as opened:
        info = opened.getmember(member)
        if not info.isfile():
            raise ValueError(f"notice member is not a regular file: {member}")
        stream = opened.extractfile(info)
        if stream is None:
            raise ValueError(f"notice member cannot be read: {member}")
        return stream.read()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--native-manifest", type=Path, required=True)
    parser.add_argument("--archives", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    source = json.loads(args.source_manifest.read_text())
    native_source = json.loads(args.native_manifest.read_text())
    args.destination.mkdir(parents=True, exist_ok=True)
    notices = []
    without_embedded = []
    for record in source["records"]:
        archive = args.archives / record["filename"]
        if hashlib.sha256(archive.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"source archive changed: {archive.name}")
        paths = record["embedded_license_paths"]
        if not paths:
            without_embedded.append(record["name"])
        for member in paths:
            safe = safe_member(member)
            payload = read_member(archive, member)
            if len(payload) > 2_000_000:
                raise ValueError(f"notice member unexpectedly large: {member}")
            output = args.destination / f"{record['name']}-{record['version']}" / Path(*safe.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(payload)
            notices.append({
                "distribution": record["name"], "version": record["version"],
                "source_archive": record["filename"], "source_member": member,
                "output": str(output.relative_to(args.destination.parent)),
                "sha256": hashlib.sha256(payload).hexdigest(),
            })
        if record["name"] in EXTRA_EVIDENCE:
            member, kind = EXTRA_EVIDENCE[record["name"]]
            payload = read_member(archive, member)
            output = args.destination / f"{record['name']}-{record['version']}" / Path(*safe_member(member).parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(payload)
            notices.append({
                "distribution": record["name"], "version": record["version"],
                "source_archive": record["filename"], "source_member": member,
                "output": str(output.relative_to(args.destination.parent)),
                "sha256": hashlib.sha256(payload).hexdigest(), "evidence_kind": kind,
            })
    loguru_output = args.destination / "loguru-0.7.3/LICENSE.upstream-tag"
    if loguru_output.exists():
        loguru_payload = loguru_output.read_bytes()
    else:
        request = urllib.request.Request(LOGURU_TAG_LICENSE, headers={"User-Agent": "amplifai-local-source-audit/1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            loguru_payload = response.read()
    if hashlib.sha256(loguru_payload).hexdigest() != LOGURU_LICENSE_SHA256:
        raise ValueError("Loguru exact-tag license hash mismatch")
    loguru_output.parent.mkdir(parents=True, exist_ok=True)
    loguru_output.write_bytes(loguru_payload)
    notices.append({
        "distribution": "loguru", "version": "0.7.3",
        "source_url": LOGURU_TAG_LICENSE,
        "output": str(loguru_output.relative_to(args.destination.parent)),
        "sha256": LOGURU_LICENSE_SHA256,
        "evidence_kind": "full_mit_text_at_exact_upstream_tag",
    })
    native_notices = []
    for record in native_source["records"]:
        archive = args.archives / record["filename"]
        if hashlib.sha256(archive.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"native source archive changed: {archive.name}")
        slug = re.sub(r"[^a-z0-9]+", "-", record["name"].lower()).strip("-")
        for member in record["embedded_notice_paths"]:
            payload = read_member(archive, member)
            if len(payload) > 2_000_000:
                raise ValueError(f"native notice member unexpectedly large: {member}")
            output = args.destination / "native" / slug / Path(*safe_member(member).parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(payload)
            native_notices.append({
                "component": record["name"], "source_status": record["source_status"],
                "source_archive": record["filename"], "source_member": member,
                "output": str(output.relative_to(args.destination.parent)),
                "sha256": hashlib.sha256(payload).hexdigest(),
            })
    args.manifest.write_text(json.dumps({
        "purpose": "exact sdist notice extraction for local rights review; not final notices",
        "extracted": notices,
        "native_extracted": native_notices,
        "packages_without_embedded_notice": without_embedded,
    }, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"notice_files": len(notices), "native_notice_files": len(native_notices),
                      "packages_without_embedded_notice": without_embedded}, sort_keys=True))


if __name__ == "__main__":
    main()
