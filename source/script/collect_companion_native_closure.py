#!/usr/bin/env python3
"""Collect artifact-declared OpenSSL/Rust sources without executing upstream code."""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import hashlib
import json
import re
import shutil
import tarfile
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from collect_portable_sources import LICENSE_NAME
from extract_companion_notices import read_member, safe_member
from package_companion_review import digest, verify_file, write_json

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_NOTICE_BYTES = 2 * 1024 * 1024
IDENTIFIER = re.compile(r"[A-Za-z0-9_.+-]+")
STANDALONE_INPUTS = {"bzip2", "expat", "mpdecimal", "openssl-3.5", "sqlite", "xz"}


def requested_sources(report: dict) -> list[dict]:
    records = []
    for component in report["cryptography_nested_components"]:
        if component["source_status"] == "sdist workspace":
            continue
        name, version = component["name"], component["version"]
        if not IDENTIFIER.fullmatch(name) or not IDENTIFIER.fullmatch(version):
            raise ValueError("invalid component name/version")
        lock = component["cargo_lock"]
        if lock:
            if lock.get("source") != "registry+https://github.com/rust-lang/crates.io-index":
                raise ValueError(f"unrecognized Cargo source: {name}")
            expected = lock["checksum"]
            filename = f"{name}-{version}.crate"
            url = f"https://static.crates.io/crates/{name}/{filename}"
            evidence = "artifact Rust SBOM and exact cryptography sdist Cargo.lock"
            kind = "Rust crate"
        else:
            if component["sbom"] != "sbom.json" or name != "openssl":
                raise ValueError(f"component has no trusted source hash: {name}")
            hashes = [item["content"] for item in component["hashes"] if item["alg"] == "SHA-256"]
            if len(hashes) != 1:
                raise ValueError("OpenSSL SBOM must declare one source SHA-256")
            expected = hashes[0]
            filename = f"openssl-{version}.tar.gz"
            url = f"https://github.com/openssl/openssl/releases/download/openssl-{version}/{filename}"
            evidence = "OpenSSL source SHA-256 embedded in selected app cryptography SBOM"
            kind = "OpenSSL"
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError(f"invalid source checksum: {name}")
        records.append({"name": name, "version": version, "kind": kind,
                        "filename": filename, "upstream_url": url, "sha256": expected,
                        "binary_version_evidence": evidence, "license_declarations": component["licenses"]})
    if len({record["filename"] for record in records}) != len(records):
        raise ValueError("duplicate native source filename")
    return records


def archive_notices(path: Path) -> list[str]:
    with tarfile.open(path, "r:*") as archive:
        notices = []
        for member in archive.getmembers():
            safe_member(member.name)
            if LICENSE_NAME.match(Path(member.name).name):
                if not member.isfile() or member.size > MAX_NOTICE_BYTES:
                    raise ValueError(f"unsafe or oversized notice: {member.name}")
                notices.append(member.name)
    return sorted(notices)


def standalone_sources(evidence: Path) -> list[dict]:
    manifest = json.loads((evidence / "third_party/native-source-manifest.json").read_text())
    recipe = next(item for item in manifest["records"] if item["name"] == "python-build-standalone")
    archive = verify_file(evidence / "third_party/source_archives", recipe, "filename")
    with tarfile.open(archive, "r:*") as opened:
        member = next(item.name for item in opened.getmembers() if item.name.endswith("/pythonbuild/downloads.py"))
    tree = ast.parse(read_member(archive, member))
    assignment = next(item for item in tree.body if isinstance(item, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "DOWNLOADS" for target in item.targets))
    if not isinstance(assignment.value, ast.Dict):
        raise ValueError("standalone DOWNLOADS is not a literal mapping")
    records = []
    for key, value in zip(assignment.value.keys, assignment.value.values):
        name = ast.literal_eval(key)
        if name not in STANDALONE_INPUTS:
            continue
        item = ast.literal_eval(value)
        url = urlparse(item["url"])
        if (url.scheme != "https" or url.hostname not in {
                "github.com", "astral-sh.github.io", "www.bytereef.org", "www.sqlite.org"
        } or url.username or url.password or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])):
            raise ValueError("invalid standalone source declaration")
        records.append({"name": name, "version": item["version"], "kind": "CPython static dependency",
                        "filename": item["url"].rsplit("/", 1)[-1], "upstream_url": item["url"],
                        "sha256": item["sha256"], "license_declarations": item.get("licenses", []),
                        "binary_version_evidence": "matched interpreter build 20260203 source recipe; exact input SHA-256",
                        "_recipe_archive": str(archive), "_recipe_member": member.split("/", 1)[0] + "/" + item["license_file"]})
    if {item["name"] for item in records} != STANDALONE_INPUTS:
        raise ValueError("standalone recipe is missing a required static dependency")
    return records


def collect(record: dict, output: Path, caches: list[Path]) -> dict:
    target = output / "source_archives" / record["filename"]
    public_record = {key: value for key, value in record.items() if not key.startswith("_")}
    try:
        found = next((path for cache in caches for path in cache.glob("*/" + record["filename"])
                      if path.is_file() and not path.is_symlink() and digest(path) == record["sha256"]), None)
        if found:
            shutil.copy2(found, target)
            origin = "verified cache"
        else:
            request = urllib.request.Request(record["upstream_url"], headers={"User-Agent": "amplifai-source-review/1"})
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read(MAX_ARCHIVE_BYTES + 1)
            if len(payload) > MAX_ARCHIVE_BYTES:
                raise ValueError("archive exceeds bounded download limit")
            if hashlib.sha256(payload).hexdigest() != record["sha256"]:
                raise ValueError("download does not match recorded source checksum")
            target.write_bytes(payload)
            origin = "verified download"
        if digest(target) != record["sha256"]:
            raise ValueError("saved source checksum mismatch")
        paths = archive_notices(target)
        notice_records = []
        for member in paths:
            payload = read_member(target, member)
            relative = Path("notices/native") / (record["name"] + "-" + record["version"]) / member
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            notice_records.append({"component": record["name"], "version": record["version"],
                                   "source_status": "checksum-matched", "source_archive": record["filename"],
                                   "source_member": member, "output": relative.as_posix(),
                                   "sha256": hashlib.sha256(payload).hexdigest()})
        if not paths and record.get("_recipe_archive"):
            recipe_archive = Path(record["_recipe_archive"])
            member = record["_recipe_member"]
            payload = read_member(recipe_archive, member)
            relative = Path("notices/native") / (record["name"] + "-" + record["version"]) / Path(member).name
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            notice_records.append({"component": record["name"], "version": record["version"],
                                   "source_status": "checksum-matched", "source_archive": recipe_archive.name,
                                   "source_member": member, "output": relative.as_posix(),
                                   "sha256": hashlib.sha256(payload).hexdigest(),
                                   "evidence_kind": "standalone recipe notice; no standalone notice in upstream archive"})
        return {**public_record, "status": origin, "source_status": "checksum-matched",
                "size": target.stat().st_size, "embedded_notice_paths": paths,
                "notice_records": notice_records}
    except (OSError, ValueError, tarfile.TarError) as error:
        return {**public_record, "status": "failed", "error": f"{type(error).__name__}: {error}"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--reconciliation", type=Path)
    source.add_argument("--standalone-evidence", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cargo-cache", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.exit(1, "Output must be a new directory. Existing evidence is never replaced.\n")
    records = (requested_sources(json.loads(args.reconciliation.read_text())) if args.reconciliation
               else standalone_sources(args.standalone_evidence))
    (args.output / "source_archives").mkdir(parents=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        collected = list(pool.map(lambda record: collect(record, args.output, args.cargo_cache), records))
    failures = [{"name": record["name"], "version": record["version"], "error": record["error"]}
                for record in collected if record["status"] == "failed"]
    missing_notices = [record["name"] for record in collected
                       if record["status"] != "failed" and not record["notice_records"]]
    notice_records = [notice for record in collected for notice in record.pop("notice_records", [])]
    write_json(args.output / "native-source-manifest.json", {
        "purpose": "artifact-declared source checksum closure; not rights or binary-build clearance",
        "reconciliation_sha256": digest(args.reconciliation) if args.reconciliation else None, "records": collected,
        "failures": failures, "components_without_notice_files": missing_notices,
    })
    write_json(args.output / "notice-extraction-manifest.json", {"native_extracted": notice_records})
    print(json.dumps({"requested": len(records), "verified": len(records) - len(failures),
                      "notice_files": len(notice_records), "failures": failures,
                      "components_without_notice_files": missing_notices}, sort_keys=True))
    if failures or missing_notices:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
