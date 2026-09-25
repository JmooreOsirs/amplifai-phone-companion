#!/usr/bin/env python3
"""Inventory the existing frozen helper without running phone collection.

Run inside a venv synced from the exact portable lock. Archive entries and
external files are evidence of inclusion; absence is reported as 'not observed'
rather than proof that a package's code is absent from every bundled file.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import re
import subprocess
from pathlib import Path


PACKAGE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--helper", type=Path, required=True)
    parser.add_argument("--viewer", type=Path, required=True)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()

    output = subprocess.check_output(
        [str(args.viewer), "--list", "--recursive", "--brief", str(args.helper)],
        text=True,
    )
    entries = [line.strip() for line in output.splitlines() if line.startswith(" ")]
    pyz_started = False
    pyz_entries: list[str] = []
    boot_entries: list[str] = []
    for line in output.splitlines():
        if line.startswith("Contents of 'PYZ.pyz'"):
            pyz_started = True
            continue
        if line.startswith("Contents of '"):
            pyz_started = False
            continue
        if line.startswith(" "):
            (pyz_entries if pyz_started else boot_entries).append(line.strip())
    roots = {entry.split(".", 1)[0] for entry in pyz_entries}
    internal = args.helper.parent / "_internal"
    external_files = sorted(path for path in internal.rglob("*")
                            if path.is_file() and not path.is_symlink())
    symlinks = sorted(path for path in internal.rglob("*") if path.is_symlink())
    external_roots = {path.relative_to(internal).parts[0]
                      for path in [*external_files, *symlinks]}
    package_map = metadata.packages_distributions()
    records: list[dict[str, object]] = []
    for line in args.lock.read_text().splitlines():
        match = PACKAGE.match(line)
        if not match:
            continue
        name, version = match.groups()
        dist = metadata.distribution(name)
        dist_name = canonical(dist.metadata["Name"])
        import_roots = sorted(
            root for root, names in package_map.items()
            if any(canonical(candidate) == dist_name for candidate in names)
        )
        archive_matches = sorted(root for root in import_roots if root in roots)
        external_matches = sorted(
            root for root in import_roots
            if root in external_roots or any(
                filename.startswith(root + ".") for filename in external_roots
            )
        )
        info_matches = sorted(
            root for root in external_roots
            if root.endswith(".dist-info") and canonical(root.split("-")[0]) == canonical(name)
        )
        record: dict[str, object] = {
            "name": name,
            "version": version,
            "import_roots": import_roots,
            "pyz_roots": archive_matches,
            "external_roots": external_matches,
            "external_dist_info": info_matches,
        }
        if archive_matches or external_matches or info_matches:
            record["classification"] = "observed_in_helper"
        elif name in {"pyinstaller", "pyinstaller-hooks-contrib"}:
            record["classification"] = "build_tool_with_runtime_hook_evidence"
        else:
            record["classification"] = "not_observed_in_helper"
        records.append(record)

    files = []
    for path in [args.helper, *external_files]:
        files.append({
            "path": str(path.relative_to(args.helper.parent)),
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    outer_files = sorted(
        path for path in args.app.rglob("*")
        if path.is_file() and not path.is_symlink()
        and args.helper.parent not in path.parents
    )
    outer_manifest = [
        {"path": str(path.relative_to(args.app)), "size": path.stat().st_size,
         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in outer_files
    ]
    manifest = {
        "purpose": "frozen-helper technical inventory; not legal clearance",
        "archive_entries": len(entries),
        "pyz_module_entries": len(pyz_entries),
        "boot_entries": boot_entries,
        "external_file_count": len(external_files),
        "external_symlink_count": len(symlinks),
        "external_symlinks": [
            {"path": str(path.relative_to(internal)), "target": str(path.readlink())}
            for path in symlinks
        ],
        "external_roots": sorted(external_roots),
        "locked_packages": records,
        "file_hashes": files,
        "outer_app_file_count": len(outer_files),
        "outer_app_file_hashes": outer_manifest,
    }
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    counts: dict[str, int] = {}
    for record in records:
        status = str(record["classification"])
        counts[status] = counts.get(status, 0) + 1
    print(json.dumps({"packages": len(records), "classifications": counts,
                      "pyz_modules": len(pyz_entries), "external_files": len(external_files),
                      "external_symlinks": len(symlinks),
                      "outer_app_files": len(outer_files)},
                     sort_keys=True))


if __name__ == "__main__":
    main()
