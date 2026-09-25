#!/usr/bin/env python3
"""Scoped local-only safety/coverage scan of the companion source candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

SENSITIVE_NAME = re.compile(
    r"(^\.env($|\.)|\.(pem|p12|pfx|mobileprovision|sqlite|sqlite3|db|csv|vcf)$|"
    r"(^|/)(test data files|\.git|\.vercel|\.next)(/|$))",
    re.I,
)
SECRET = re.compile(
    rb"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    rb"\b(?:sk_live|sk_test)_[A-Za-z0-9]{16,}|"
    rb"\bsk-[A-Za-z0-9_-]{32,}|"
    rb"\bgh[pousr]_[A-Za-z0-9]{20,}|"
    rb"\bAKIA[0-9A-Z]{16}\b|"
    rb"postgres(?:ql)?://[^\s:@/]+:[^\s@/]+@",
    re.I,
)
TEXT_SUFFIXES = {".py", ".swift", ".sh", ".md", ".toml", ".txt", ".plist", ".json", ".in", ".lock"}
ROOT_DOCS = {
    "README.md", "COMPANION-LICENSE-REVIEW.md", "COMPANION-NOTICES-CANDIDATE.md",
    "COMPANION-DEPENDENCY-COVERAGE.md", "COMPANION-NATIVE-SOURCE-COVERAGE.md",
    "LICENSE", "COPYRIGHT", "THIRD-PARTY-NOTICES.txt",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source_manifest = json.loads((args.bundle / "third_party/source-archive-manifest.json").read_text())
    native_manifest = json.loads((args.bundle / "third_party/native-source-manifest.json").read_text())
    notice_manifest = json.loads((args.bundle / "third_party/notice-extraction-manifest.json").read_text())
    expected_archives = {
        record["filename"]: record["sha256"]
        for record in [*source_manifest["records"], *native_manifest["records"]]
    }
    expected_notices = {
        f"third_party/{record['output']}": record["sha256"]
        for record in [*notice_manifest["extracted"], *notice_manifest["native_extracted"]]
    }
    findings: list[str] = []
    scanned_text = 0
    files = sorted(path for path in args.bundle.rglob("*") if path.is_file())
    for path in files:
        relative = path.relative_to(args.bundle).as_posix()
        if path.is_symlink():
            findings.append(f"unexpected_symlink:{relative}")
            continue
        if SENSITIVE_NAME.search(relative):
            findings.append(f"sensitive_filename:{relative}")
        if relative.startswith("source/"):
            if not relative.startswith(("source/collector/", "source/script/", "source/public/brand/")):
                findings.append(f"unrelated_source_path:{relative}")
        elif relative.startswith("third_party/source_archives/"):
            filename = path.name
            expected = expected_archives.get(filename)
            if not expected:
                findings.append(f"unmanifested_archive:{relative}")
            elif digest(path) != expected:
                findings.append(f"archive_hash_mismatch:{relative}")
        elif relative.startswith("third_party/notices/"):
            expected = expected_notices.get(relative)
            if not expected:
                findings.append(f"unmanifested_notice:{relative}")
            elif digest(path) != expected:
                findings.append(f"notice_hash_mismatch:{relative}")
        elif relative.startswith("third_party/"):
            if path.suffix != ".json":
                findings.append(f"unexpected_third_party_file:{relative}")
        elif relative not in ROOT_DOCS:
            findings.append(f"unexpected_root_file:{relative}")
        if path.suffix.lower() in TEXT_SUFFIXES and not relative.startswith("third_party/"):
            scanned_text += 1
            if SECRET.search(path.read_bytes()):
                findings.append(f"possible_secret:{relative}")
    if len(expected_archives) != len(source_manifest["records"]) + len(native_manifest["records"]):
        findings.append("duplicate_archive_filename_in_manifests")
    present = {path.name for path in (args.bundle / "third_party/source_archives").iterdir() if path.is_file()}
    for filename in expected_archives.keys() - present:
        findings.append(f"missing_archive:{filename}")
    present_notices = {
        path.relative_to(args.bundle).as_posix()
        for path in (args.bundle / "third_party/notices").rglob("*") if path.is_file()
    }
    for relative in expected_notices.keys() - present_notices:
        findings.append(f"missing_notice:{relative}")
    report = {
        "scope": "local candidate paths, authored text, manifest hashes; upstream compressed contents not secret-scanned",
        "file_count": len(files),
        "authored_text_files_scanned": scanned_text,
        "verified_source_archives": len(expected_archives),
        "verified_notice_evidence_files": len(expected_notices),
        "findings": sorted(findings),
    }
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    if findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
