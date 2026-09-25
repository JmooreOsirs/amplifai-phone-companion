#!/usr/bin/env python3
"""Collect exact PyPI sdists for the portable lock into a local review bundle.

No package code is executed. Every saved archive must match a lock hash and
PyPI's release hash. Missing source is reported, never substituted by a newer
release. This is a technical inventory, not a distribution license decision.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


PACKAGE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)")
HASH = re.compile(r"--hash=sha256:([a-f0-9]{64})")
USER_AGENT = "amplifai-local-source-audit/1"
LICENSE_NAME = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|COPYRIGHT|AUTHORS)([._-].*)?$", re.I)


def archive_license_paths(path: Path) -> list[str]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            paths = archive.namelist()
    elif tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as archive:
            paths = archive.getnames()
    else:
        return []
    return sorted(name for name in paths if LICENSE_NAME.match(Path(name).name))


def locked_packages(path: Path) -> list[dict[str, object]]:
    packages: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for line in path.read_text().splitlines():
        match = PACKAGE.match(line)
        if match:
            current = {"name": match.group(1), "version": match.group(2), "hashes": []}
            packages.append(current)
        if current is not None:
            current["hashes"].extend(HASH.findall(line))
    if not packages or any(not package["hashes"] for package in packages):
        raise ValueError("Portable lock is empty or has an unhashed package")
    return packages


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def collect(package: dict[str, object], destination: Path) -> dict[str, object]:
    name = str(package["name"])
    version = str(package["version"])
    record: dict[str, object] = {"name": name, "version": version}
    try:
        metadata = json.loads(fetch(f"https://pypi.org/pypi/{name}/{version}/json"))
        info = metadata["info"]
        raw_license = info.get("license") or ""
        record.update(
            license_expression=info.get("license_expression"),
            license_classifiers=sorted(
                item for item in (info.get("classifiers") or [])
                if item.startswith("License ::")
            ),
            license_field=raw_license if len(raw_license) <= 256 else None,
            license_field_sha256=(hashlib.sha256(raw_license.encode()).hexdigest()
                                  if len(raw_license) > 256 else None),
        )
        sdists = [entry for entry in metadata["urls"] if entry["packagetype"] == "sdist"]
        if len(sdists) != 1:
            record.update(status="missing_or_ambiguous_sdist", count=len(sdists))
            return record
        sdist = sdists[0]
        digest = sdist["digests"]["sha256"]
        filename = sdist["filename"]
        record.update(filename=filename, sha256=digest, url=sdist["url"], size=sdist["size"])
        if digest not in package["hashes"]:
            record["status"] = "sdist_hash_not_in_lock"
            return record
        target = destination / filename
        if target.exists():
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual == digest:
                record["status"] = "verified_existing"
                record["embedded_license_paths"] = archive_license_paths(target)
                return record
            record["status"] = "existing_hash_mismatch"
            record["actual_sha256"] = actual
            return record
        payload = fetch(sdist["url"])
        actual = hashlib.sha256(payload).hexdigest()
        if actual != digest:
            record.update(status="download_hash_mismatch", actual_sha256=actual)
            return record
        with tempfile.NamedTemporaryFile(dir=destination, prefix=".source-", delete=False) as temp:
            temp.write(payload)
            temporary = Path(temp.name)
        temporary.replace(target)
        record["status"] = "downloaded_verified"
        record["embedded_license_paths"] = archive_license_paths(target)
        return record
    except (OSError, urllib.error.URLError, ValueError, KeyError, TypeError) as error:
        record.update(status="fetch_error", error=f"{type(error).__name__}: {error}")
        return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    packages = locked_packages(args.lock)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda package: collect(package, args.destination), packages))
    manifest = {
        "purpose": "local technical source review; not a license or release clearance",
        "lock": str(args.lock),
        "total_locked": len(packages),
        "records": records,
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    counts: dict[str, int] = {}
    for record in records:
        status = str(record["status"])
        counts[status] = counts.get(status, 0) + 1
    print(json.dumps({"total_locked": len(packages), "status_counts": counts}, sort_keys=True))


if __name__ == "__main__":
    main()
