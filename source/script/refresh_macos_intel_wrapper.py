#!/usr/bin/env python3
"""Refresh only native Swift code in the fixed qualified RC25 Intel candidate.

The workflow downloads the fixed draft below after its native run succeeds.
An independently checked ZIP hash is mandatory. No collector mode runs here.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile

from build_macos_intel_candidate import candidate_plist
from verify_macos_architecture import sha256

ROOT = Path(__file__).resolve().parents[2]
BASELINE_COMMIT = "6fa60a6b77e8a676556ce6bc68ee61235d34e750"
BASELINE_TAG = "intel-rc25-candidate-6fa60a6b77e8"
BASELINE_RUN = 38636941728
APP_NAME = "AMPLIFai Phone Intel Test.app"
ZIP_NAME = "AMPLIFai-Phone-Intel-rc25-candidate.zip"
SOURCE_NAME = "AMPLIFai-Intel-Build-Source.tar.gz"
SOURCE_DIRS = ("intel-dependency-source-inputs", "intel-python-source-inputs")
IMMUTABLE_INPUTS = (
    "source/collector/amplifai_phone", "source/collector/agent_entry.py",
    "source/collector/portable-requirements.lock",
    "source/collector/intel-dependency-inputs.json", "source/collector/intel-python-inputs.json",
    "source/script/prepare_intel_dependencies.py", "source/script/collect_intel_python_sources.py",
    "source/collector/macos/Package.swift", "source/collector/macos/Package.resolved",
    "source/collector/macos/Info.plist",
)


def run(arguments, **kwargs):
    print("+", " ".join(map(str, arguments)), flush=True)
    return subprocess.run(list(map(str, arguments)), check=True, **kwargs)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def checksum_assets(directory: Path, expected_zip_sha256: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_zip_sha256):
        raise ValueError("Supply the independently verified lowercase SHA-256 of the fixed baseline ZIP")
    if sha256(directory / ZIP_NAME) != expected_zip_sha256:
        raise ValueError("Baseline ZIP differs from the independently verified hash")
    checksums = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (?:\./)?([^/]+)", line)
        if not match or match[2] in {".", "..", "SHA256SUMS"} or match[2] in checksums:
            raise ValueError("Invalid baseline asset checksum manifest")
        name = match[2]
        if sha256(directory / name) != match[1]:
            raise ValueError(f"Baseline asset checksum mismatch: {name}")
        checksums[name] = match[1]
    required = {ZIP_NAME, SOURCE_NAME, "candidate-receipt.json", "architecture-receipt.json",
                "status-receipt.json", "frozen-worker-receipt.json", "frozen-metadata-receipt.json",
                "dependency-receipt.json"}
    if not required.issubset(checksums):
        raise ValueError("Baseline draft omits qualified build/source receipts")
    return checksums


def validate_baseline(receipt: dict, app: Path):
    expected = {"kind": "macos-intel-candidate", "architecture": "x86_64",
                "git_commit": BASELINE_COMMIT, "build": "261014251",
                "version": "2026.10.14-rc25-intel1",
                "bundle_id": "ai.satoris.amplifai.phone.intel-candidate", "updater_enabled": True}
    if any(type(receipt.get(key)) is not type(value) or receipt.get(key) != value
           for key, value in expected.items()):
        raise ValueError("Candidate receipt is not the fixed qualified Intel baseline")
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    if info != candidate_plist(info, enable_published_updates=True):
        raise ValueError("Baseline app identity or updater configuration differs")
    helper = app / "Contents/Resources/helper/amplifai-agent"
    if sha256(helper) != receipt.get("helper_sha256"):
        raise ValueError("Frozen Intel helper does not match its qualified receipt")
    return info


def tree_manifest(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            if root.resolve() not in path.resolve(strict=True).parents:
                raise ValueError("Preserved helper has an escaping symlink")
            result[relative] = {"symlink": os.readlink(path)}
        elif path.is_file():
            result[relative] = {"sha256": sha256(path), "mode": stat.S_IMODE(path.stat().st_mode)}
        elif not path.is_dir():
            raise ValueError("Preserved helper has an unsupported special file")
    if not result:
        raise ValueError("Frozen helper tree is empty")
    return result


def validate_zip(path: Path):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 20000 or sum(item.file_size for item in entries) > 1024**3:
            raise ValueError("Candidate ZIP exceeds the bounded app size")
        names = set()
        for item in entries:
            name = PurePosixPath(item.filename)
            if (name.is_absolute() or ".." in name.parts or "\\" in item.filename
                    or not name.parts or name.parts[0] not in {APP_NAME, "__MACOSX"}
                    or str(name) in names):
                raise ValueError("Unsafe candidate ZIP path")
            names.add(str(name))
            if stat.S_ISLNK(item.external_attr >> 16):
                target = archive.read(item).decode("utf-8")
                resolved = PurePosixPath(os.path.normpath(str(name.parent / target)))
                if resolved.is_absolute() or resolved.parts[0] != APP_NAME or ".." in resolved.parts:
                    raise ValueError("Unsafe candidate ZIP symlink")


def extract_source_inputs(archive: Path, destination: Path):
    prefixes = tuple(f"intel-source/{name}/" for name in SOURCE_DIRS)
    with tarfile.open(archive, "r:gz") as source:
        members = [item for item in source.getmembers()
                   if item.name.startswith(prefixes) or item.name in tuple(p[:-1] for p in prefixes)]
        if len(members) > 100000 or sum(item.size for item in members) > 2 * 1024**3:
            raise ValueError("Baseline source inputs exceed their extraction bound")
        for item in members:
            if (PurePosixPath(item.name).is_absolute() or ".." in PurePosixPath(item.name).parts
                    or not (item.isfile() or item.isdir())):
                raise ValueError("Unsafe baseline source-input member")
        source.extractall(destination, members=members, filter="data")
    if any(not (destination / "intel-source" / name).is_dir() for name in SOURCE_DIRS):
        raise ValueError("Baseline corresponding source inputs are missing")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", required=True, type=Path)
    parser.add_argument("--baseline-zip-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() != "x86_64":
        parser.error("Wrapper refresh requires a native Intel macOS runner")
    if args.output.exists():
        parser.error("Output must be a new directory")
    baseline = args.baseline_dir.resolve(strict=True)
    assets = checksum_assets(baseline, args.baseline_zip_sha256)
    run(["git", "diff", "--exit-code", "HEAD", "--", "source", ".github/workflows/macos-intel.yml"],
        cwd=ROOT, stdout=subprocess.DEVNULL)
    changed = subprocess.check_output(["git", "diff", "--name-only", BASELINE_COMMIT, "HEAD", "--",
                                       *IMMUTABLE_INPUTS], cwd=ROOT, text=True).strip()
    if changed:
        raise ValueError("Helper or pinned framework inputs changed; a full native build is required: " + changed)
    output = args.output.resolve()
    output.mkdir(parents=True)
    validate_zip(baseline / ZIP_NAME)
    run(["ditto", "-x", "-k", baseline / ZIP_NAME, output])
    app = output / APP_NAME
    receipt = json.loads((baseline / "candidate-receipt.json").read_text())
    info = validate_baseline(receipt, app)
    metadata = json.loads((baseline / "frozen-metadata-receipt.json").read_text())
    if (json.loads((baseline / "architecture-receipt.json").read_text()).get("passed") is not True
            or json.loads((baseline / "status-receipt.json").read_text()).get("status") != "qualified"
            or json.loads((baseline / "frozen-worker-receipt.json").read_text()).get("status") != "pass"
            or metadata.get("ready") is not True or metadata.get("helper_sha256") != receipt["helper_sha256"]
            or assets["dependency-receipt.json"] != receipt.get("dependency_receipt_sha256")):
        raise ValueError("Baseline receipts do not establish successful helper qualification")
    helper_root = app / "Contents/Resources/helper"
    if sha256(app / "Contents/MacOS/AmplifaiPhone") != receipt.get("swift_sha256"):
        raise ValueError("Baseline native wrapper does not match its qualified receipt")
    preserved = tree_manifest(helper_root)
    write_json(output / "preserved-helper-manifest.json", preserved)
    if subprocess.check_output(["lipo", "-archs", helper_root / "amplifai-agent"], text=True).strip() != "x86_64":
        raise ValueError("Baseline helper is not the exact native Intel architecture")
    run(["codesign", "--verify", "--deep", "--strict", app])
    package = ROOT / "source/collector/macos"
    swift = ["swift", "build", "--configuration", "release", "--arch", "x86_64",
             "--package-path", package]
    run(swift)
    binary_dir = Path(subprocess.check_output(list(map(str, swift + ["--show-bin-path"])), text=True).strip())
    wrapper = app / "Contents/MacOS/AmplifaiPhone"
    shutil.copy2(binary_dir / "AmplifaiPhone", wrapper)
    with (output / "wrapper-rpath-receipt.json").open("w") as stream:
        run([sys.executable, ROOT / "source/script/normalize_macos_wrapper.py", wrapper,
             "--architecture", "x86_64"], stdout=stream)
    # Retain the exact signed baseline Sparkle framework and entire frozen helper.
    legal = app / "Contents/Resources/Legal"
    tracked = subprocess.check_output(["git", "ls-files", "-z", "source"], cwd=ROOT).decode().split("\0")
    manifest = {name.removeprefix("source/"): sha256(ROOT / name) for name in sorted(tracked) if name}
    write_json(legal / "source-package-file-manifest.json", manifest)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    (legal / "SOURCE-README.md").write_text(
        "# Intel candidate corresponding source\n\n"
        f"Native wrapper source commit: {commit}.\n"
        f"The helper and its complete runtime are byte-for-byte preserved from {BASELINE_COMMIT}.\n"
        "The accompanying full build-source archive contains current authored source and the original "
        "qualified Intel dependency and Python source inputs. Adjacent Intel notices still apply.\n")
    run(["codesign", "--force", "--sign", "-", wrapper])
    run(["codesign", "--force", "--sign", "-", app])
    run(["codesign", "--verify", "--deep", "--strict", app])
    if tree_manifest(helper_root) != preserved:
        raise ValueError("Wrapper refresh altered frozen helper bytes, modes, or links")
    run([sys.executable, ROOT / "source/script/verify_macos_architecture.py", app, "--arch", "x86_64",
         "--minimum-macos", "14.0", "--output", output / "architecture-receipt.json"])
    with (output / "status-receipt.json").open("w") as stream:
        run([sys.executable, ROOT / "source/script/verify_frozen_companion_status.py", "--helper",
             helper_root / "amplifai-agent", "--architecture", "x86_64", "--version",
             info["CFBundleShortVersionString"], "--build", info["CFBundleVersion"]], stdout=stream)
    run(["sh", ROOT / "source/collector/macos/Tests/check_updater.sh"])
    if tree_manifest(helper_root) != preserved:
        raise ValueError("Qualification altered the preserved helper tree")
    retained = output / "baseline-qualification"
    retained.mkdir()
    for name in assets:
        if name.endswith(".json"):
            shutil.copy2(baseline / name, retained / name)
    shutil.copy2(baseline / "SHA256SUMS", retained / "SHA256SUMS")
    receipt.update(kind="macos-intel-wrapper-refresh", git_commit=commit, swift_sha256=sha256(wrapper),
                   baseline_commit=BASELINE_COMMIT, baseline_tag=BASELINE_TAG, baseline_run=BASELINE_RUN,
                   baseline_zip_sha256=args.baseline_zip_sha256, baseline_assets_sha256=assets,
                   helper_tree_preserved=True, helper_qualification="retained baseline; not rerun",
                   current_gates=["native Swift release", "actual updater/status and closed-stdin regression",
                                  "native closure", "frozen status process/HTTP/EOF"],
                   collection_started=False, owner_app_launched=False)
    write_json(output / "candidate-receipt.json", receipt)
    delivery = output / "delivery"
    delivery.mkdir()
    run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, delivery / ZIP_NAME])
    for path in output.glob("*.json"):
        shutil.copy2(path, delivery / path.name)
    source_dir = output / "intel-source"
    source_dir.mkdir()
    archive = output / "current-source.tar"
    run(["git", "archive", "HEAD", "-o", archive], cwd=ROOT)
    with tarfile.open(archive) as source:
        source.extractall(source_dir, filter="data")
    extract_source_inputs(baseline / SOURCE_NAME, output / "baseline-source")
    python_source_receipt = output / "baseline-source/intel-source/intel-python-source-inputs/receipt.json"
    if sha256(python_source_receipt) != receipt.get("python_source_receipt_sha256"):
        raise ValueError("Retained Python source inputs differ from the qualified baseline")
    for name in SOURCE_DIRS:
        shutil.move(output / "baseline-source/intel-source" / name, source_dir / name)
    run(["tar", "-czf", delivery / SOURCE_NAME, "-C", output, "intel-source"])
    # Draft release assets are flat; package the labeled, unchanged baseline evidence.
    run(["tar", "-czf", delivery / "baseline-qualification.tar.gz", "-C", output, "baseline-qualification"])
    checksums = "".join(f"{sha256(path)}  ./{path.name}\n" for path in sorted(delivery.iterdir()))
    (delivery / "SHA256SUMS").write_text(checksums)
    print(delivery)


if __name__ == "__main__":
    main()
