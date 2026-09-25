#!/usr/bin/env python3
"""Assemble an offline, artifact-bound source review package without modifying an app."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path, PurePosixPath

from extract_companion_notices import read_member
from scan_companion_bundle import SECRET, SENSITIVE_NAME

NATIVE_DISTRIBUTIONS = {
    "_cffi_backend": "cffi", "charset_normalizer": "charset-normalizer",
    "cryptography": "cryptography", "psutil": "psutil", "wcwidth": "wcwidth",
}
PACKAGE_SCRIPTS = (
    "build_and_run.sh", "build_portable_candidate.sh", "collect_portable_sources.py",
    "record_native_sources.py", "inventory_portable_helper.py", "extract_companion_notices.py",
    "render_companion_coverage.py", "scan_companion_bundle.py", "companion_release_preflight.sh",
    "package_companion_review.py", "test_companion_review_package.py",
    "collect_companion_native_closure.py",
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def contained_file(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if parts.is_absolute() or not parts.parts or ".." in parts.parts or "\\" in relative:
        raise ValueError(f"unsafe manifest path: {relative}")
    root = root.resolve()
    path = root
    for part in parts.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"symlink is not permitted: {relative}")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"missing or outside evidence file: {relative}")
    return path


def verify_file(root: Path, record: dict, key: str = "path") -> Path:
    path = contained_file(root, record[key])
    if digest(path) != record["sha256"]:
        raise ValueError(f"evidence hash mismatch: {record[key]}")
    return path


def verify_app(app: Path, inventory: dict) -> int:
    expected = {
        "Contents/Resources/helper/" + record["path"]: record["sha256"]
        for record in inventory["file_hashes"]
    }
    expected.update({record["path"]: record["sha256"] for record in inventory["outer_app_file_hashes"]})
    actual = set()
    for path in app.rglob("*"):
        if path.is_symlink():
            raise ValueError("minimal app contains an unexpected symlink")
        if path.is_file():
            actual.add(path.relative_to(app).as_posix())
    if actual != expected.keys():
        raise ValueError("app file set differs from the selected inventory")
    for relative, expected_hash in expected.items():
        verify_file(app, {"path": relative, "sha256": expected_hash})
    return len(expected)


def authored_paths(repo: Path) -> list[Path]:
    tracked = subprocess.check_output([
        "git", "-C", str(repo), "ls-files", "-z", "collector/amplifai_phone",
        "collector/macos", "collector/tests", "collector/agent_entry.py",
        "collector/README.md", "collector/requirements.txt", "collector/portable-requirements.in",
        "collector/portable-requirements.lock", "public/brand/amplifai-by-nexus-original.png",
    ]).decode().split("\0")
    paths = [contained_file(repo, name) for name in tracked if name]
    paths += [contained_file(repo, "script/" + name) for name in PACKAGE_SCRIPTS]
    for path in paths:
        relative = path.relative_to(repo).as_posix()
        if SENSITIVE_NAME.search(relative) or (path.suffix != ".png" and SECRET.search(path.read_bytes())):
            raise ValueError(f"unsafe authored input: {relative}")
    return sorted(paths)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def assemble(repo: Path, app: Path, inventory_path: Path, evidence: Path, output: Path,
             native_closures: tuple[Path, ...] = ()) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError("output must be a new directory; existing packages are never replaced")
    inventory = json.loads(inventory_path.read_text())
    app_files = verify_app(app, inventory)
    sources = json.loads(contained_file(evidence, "third_party/source-archive-manifest.json").read_text())
    native = json.loads(contained_file(evidence, "third_party/native-source-manifest.json").read_text())
    notices = json.loads(contained_file(evidence, "third_party/notice-extraction-manifest.json").read_text())
    locked = dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)",
                            (repo / "collector/portable-requirements.lock").read_text(), re.MULTILINE))
    if locked != {record["name"]: record["version"] for record in sources["records"]}:
        raise ValueError("source manifest does not match the authored dependency lock")
    if locked != {record["name"]: record["version"] for record in inventory["locked_packages"]}:
        raise ValueError("app inventory does not match the authored dependency lock")
    native_records = [record for record in native["records"]
                      if record["name"] in {"CPython", "python-build-standalone"}]
    if len(native_records) != 2:
        raise ValueError("CPython source and standalone build recipe are required")
    archive_records = [*sources["records"], *native_records]
    archives = evidence / "third_party/source_archives"
    archive_roots = {record["filename"]: archives for record in archive_records}
    native_names = {record["name"] for record in native_records}
    native_notices = [record for record in notices["native_extracted"] if record["component"] in native_names]
    notice_records = [*notices["extracted"], *native_notices]
    notice_roots = {record["output"]: evidence / "third_party" for record in notice_records}
    for closure in native_closures:
        extra = json.loads(contained_file(closure, "native-source-manifest.json").read_text())
        extracted = json.loads(contained_file(closure, "notice-extraction-manifest.json").read_text())
        if extra["failures"] or extra["components_without_notice_files"]:
            raise ValueError("native closure contains unresolved source/notice failures")
        for record in extra["records"]:
            if record["filename"] in archive_roots or record["source_status"] != "checksum-matched":
                raise ValueError("duplicate or unverified native closure source")
            archive_roots[record["filename"]] = closure / "source_archives"
            archive_records.append(record)
            native_records.append(record)
        for record in extracted["native_extracted"]:
            if record["output"] in notice_roots:
                raise ValueError("duplicate native closure notice")
            notice_roots[record["output"]] = closure
            notice_records.append(record)
            native_notices.append(record)
    for record in archive_records:
        verify_file(archive_roots[record["filename"]], record, "filename")
    for record in notice_records:
        verify_file(notice_roots[record["output"]], record, "output")
    authored = authored_paths(repo)
    for path in authored:
        relative = path.relative_to(repo).as_posix()
        if relative.startswith("collector/amplifai_phone/"):
            bundled = contained_file(app, "Contents/Resources/" + relative)
            if digest(path) != digest(bundled):
                raise ValueError(f"authored Python source differs from app: {relative}")
    by_name = {record["name"]: record for record in sources["records"]}
    natives = []
    for record in inventory["file_hashes"]:
        if not record["path"].endswith((".so", ".dylib")):
            continue
        root = PurePosixPath(record["path"]).parts[1].split(".")[0]
        distribution = "CPython" if root == "libpython3" else NATIVE_DISTRIBUTIONS.get(root)
        if distribution is None:
            raise ValueError(f"unmapped native component: {record['path']}")
        source = (next(item for item in native_records if item["name"] == "CPython")
                  if distribution == "CPython" else by_name[distribution])
        natives.append({**record, "component": distribution, "version": source["version"],
                        "source_archive": source["filename"], "source_sha256": source["sha256"],
                        "build_provenance": "version source available; binary build not reproduced"})
    sbom_root = "Contents/Resources/helper/_internal/cryptography-" + by_name["cryptography"]["version"] + ".dist-info/sboms/"
    sboms = {name: contained_file(app, sbom_root + name)
             for name in ("sbom.json", "cryptography-rust.cyclonedx.json")}
    crypto = by_name["cryptography"]
    cargo = tomllib.loads(read_member(archives / crypto["filename"],
                                    f"cryptography-{crypto['version']}/Cargo.lock").decode())
    cargo_by_name = {(item["name"], item["version"]): item for item in cargo["package"]}
    nested = []
    native_by_version = {(record["name"], record["version"]): record for record in native_records}
    for name, path in sboms.items():
        for component in json.loads(path.read_text())["components"]:
            package = cargo_by_name.get((component["name"], component["version"]))
            available = native_by_version.get((component["name"], component["version"]))
            expected_hash = (package.get("checksum") if package else next(
                (item["content"] for item in component.get("hashes", []) if item["alg"] == "SHA-256"), None))
            if available and available["sha256"] != expected_hash:
                raise ValueError(f"native source does not match artifact declaration: {component['name']}")
            nested.append({"name": component["name"], "version": component["version"],
                           "sbom": name, "licenses": component.get("licenses", []),
                           "hashes": component.get("hashes", []), "cargo_lock": package,
                           "source_status": "sdist workspace" if package and not package.get("source")
                           else "artifact checksum matched; source and notices present" if available
                           else "external source and notice reconciliation required"})
    nested_complete = all(item["source_status"] != "external source and notice reconciliation required" for item in nested)
    interpreter_inputs = [record for record in native_records if record.get("kind") == "CPython static dependency"]
    interpreter_complete = {record["name"] for record in interpreter_inputs} == {
        "bzip2", "expat", "mpdecimal", "openssl-3.5", "sqlite", "xz"
    }
    report = {
        "purpose": "offline minimal Mac source review package; no distribution clearance",
        "release_ready": False, "app_name": app.name, "verified_app_files": app_files,
        "inventory_sha256": digest(inventory_path), "lock_sha256": digest(repo / "collector/portable-requirements.lock"),
        "source_revision": subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip(),
        "python_sources": len(sources["records"]), "native_source_archives": len(native_records),
        "notice_files": len(notice_records), "native_files": natives,
        "runtime_hooks": [entry for entry in inventory["boot_entries"] if entry.startswith("pyi_rth_")],
        "cryptography_nested_components": nested,
        "cryptography_source_checksum_closure": nested_complete,
        "interpreter_static_source_inputs": interpreter_inputs,
        "enumerated_source_inputs_complete": nested_complete and interpreter_complete,
        "authored_companion_license": "GPL-3.0-or-later",
        "authored_companion_copyright": "Copyright (C) 2026 OSIRS LLC",
        "blockers": [
            *([] if nested_complete else ["OpenSSL and external Rust sources/notices in cryptography SBOM require reconciliation."]),
            *([] if interpreter_complete else ["CPython static input source closure is missing."]),
            "Revised candidate has no distribution signing/notarization or owner-operated fresh install/phone proof.",
        ],
        "verification_limits": [
            "Native source inputs are checksum-matched to the artifact SBOM/Cargo.lock and interpreter build recipe; native binaries have not been rebuilt from source.",
            "Source and notice coverage is technical evidence, not a legal compliance determination.",
        ],
    }
    output.mkdir(parents=True, exist_ok=False)
    for path in authored:
        copy_file(path, output / "source" / path.relative_to(repo))
    for record in archive_records:
        copy_file(archive_roots[record["filename"]] / record["filename"], output / "third_party/source_archives" / record["filename"])
    for record in notice_records:
        copy_file(notice_roots[record["output"]] / record["output"], output / "third_party" / record["output"])
    notice_text = ["AMPLIFai companion third-party notices\n",
                   "Retained upstream runtime and build-source notices; original terms and copyright remain applicable.\n"]
    for record in notice_records:
        payload = (notice_roots[record["output"]] / record["output"]).read_bytes()
        notice_text.append(f"\n--- {record.get('distribution', record.get('component'))}: {record['output']} ---\n"
                           f"Source: {record.get('source_archive', record.get('source_url'))}; SHA-256: {record['sha256']}\n")
        notice_text.append(payload.decode("utf-8", errors="replace") + "\n")
    (output / "THIRD-PARTY-NOTICES.txt").write_text("".join(notice_text))
    write_json(output / "third_party/source-archive-manifest.json", sources)
    write_json(output / "third_party/native-source-manifest.json", {**native, "records": native_records})
    write_json(output / "third_party/notice-extraction-manifest.json", {**notices, "native_extracted": native_notices})
    copy_file(inventory_path, output / "third_party/helper-inventory.json")
    for name, path in sboms.items():
        copy_file(path, output / "third_party/artifact-sboms" / name)
    write_json(output / "third_party/reconciliation.json", report)
    for source, target in (("COMPANION-LICENSE.txt", "LICENSE"), ("COMPANION-COPYRIGHT.txt", "COPYRIGHT"),
                           ("COMPANION-SOURCE-RELEASE-README.md", "README.md")):
        copy_file(repo / "docs" / source, output / target)
    subprocess.run(["python3", str(repo / "script/render_companion_coverage.py"),
                    "--source-manifest", str(output / "third_party/source-archive-manifest.json"),
                    "--helper-manifest", str(output / "third_party/helper-inventory.json"),
                    "--output", str(output / "COMPANION-DEPENDENCY-COVERAGE.md"),
                    "--label", "Mac companion minimal revision"], check=True)
    subprocess.run(["python3", str(repo / "script/scan_companion_bundle.py"), "--bundle", str(output),
                    "--report", str(output / "third_party/bundle-scan-report.json")], check=True)
    write_json(output / "third_party/package-file-manifest.json", {
        "purpose": "all package files except this manifest; integrity only, not legal clearance",
        "files": [{"path": path.relative_to(output).as_posix(), "sha256": digest(path)}
                  for path in sorted(output.rglob("*")) if path.is_file()],
    })
    return {key: report[key] for key in ("release_ready", "verified_app_files", "python_sources",
                                       "native_source_archives", "notice_files")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--evidence-bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-closure", type=Path, action="append", default=[])
    args = parser.parse_args()
    try:
        result = assemble(Path(__file__).resolve().parents[1], args.app.resolve(),
                          args.inventory.resolve(), args.evidence_bundle.resolve(), args.output.absolute(),
                          tuple(path.resolve() for path in args.native_closure))
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Package refused: {error}\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
