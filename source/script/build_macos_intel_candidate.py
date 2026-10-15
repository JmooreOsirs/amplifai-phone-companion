#!/usr/bin/env python3
"""Build RC26 as a separate, ad-hoc signed Intel candidate; never install it.

Run with the prepared CPython 3.12 Intel environment. This intentionally does
not modify the existing ARM release scripts, sign with Developer ID, contact
an iPhone, or publish/enable a website download.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import plistlib
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "source"
MINIMUM = "14.0"
VERSION = "2026.10.15-rc26-intel1"
BUILD = "261015261"
RELEASE = "macos-2026.10.15-rc26-intel1-test"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def run(command: list[str], **kwargs):
    print("+", " ".join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo: Path, *arguments: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *arguments], stderr=subprocess.PIPE)


def source_freeze(repo: Path, expected_commit: str) -> dict:
    """Freeze only clean committed authored inputs; independent of host/time/paths."""
    if not COMMIT.fullmatch(expected_commit):
        raise ValueError("Expected a full approved Git commit")
    commit = _git(repo, "rev-parse", "HEAD").decode().strip()
    if commit != expected_commit:
        raise ValueError("Checkout differs from the approved source commit")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("Authored source checkout must be clean before freezing")
    records = []
    for row in _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
        if not row:
            continue
        header, encoded_name = row.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        name = encoded_name.decode("utf-8")
        parts = PurePosixPath(name)
        if mode not in {"100644", "100755"} or kind != "blob" or parts.is_absolute() or ".." in parts.parts or "\\" in name:
            raise ValueError("Unsupported authored source entry")
        path = repo
        for component in parts.parts:
            path /= component
            if path.is_symlink():
                raise ValueError("Authored source cannot traverse a link")
        if not path.is_file():
            raise ValueError("Authored source input is missing")
        content = _git(repo, "cat-file", "blob", blob)
        checksum = hashlib.sha256(content).hexdigest()
        if sha256(path) != checksum or path.stat().st_size != len(content):
            raise ValueError("Working source differs from the committed blob")
        if (bool(path.stat().st_mode & 0o111)) != (mode == "100755"):
            raise ValueError("Authored source executable mode differs from Git")
        records.append({"path": name, "bytes": len(content), "sha256": checksum,
                        "git_mode": mode, "git_blob": blob})
    if not records or len({record["path"] for record in records}) != len(records):
        raise ValueError("Authored source inventory is empty or duplicated")
    if (_git(repo, "rev-parse", "HEAD").decode().strip() != expected_commit
            or _git(repo, "status", "--porcelain=v1", "--untracked-files=all")):
        raise ValueError("Authored source changed while freezing")
    return {"schema": 1, "kind": "intel-rc26-authored-source-freeze", "version": VERSION,
            "build": BUILD, "commit": commit,
            "tree": _git(repo, "rev-parse", commit + "^{tree}").decode().strip(),
            "files": sorted(records, key=lambda record: record["path"])}


def freeze_bytes(receipt: dict) -> bytes:
    return (json.dumps(receipt, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def verify_release_policy(repo: Path = ROOT) -> dict:
    policy = json.loads((repo / "docs/INTEL-RC26-BASELINE.json").read_text())
    if (policy.get("schema") != 1 or policy.get("release") != RELEASE
            or policy.get("version") != VERSION or policy.get("build") != BUILD
            or policy.get("bundle_id") != "ai.satoris.amplifai.phone.intel-candidate"
            or policy.get("channel") != "macos-intel-test" or policy.get("minimum_macos") != MINIMUM):
        raise ValueError("Intel release policy differs from the approved rc26 identity")
    expected = {"source/collector/portable-requirements.lock", "source/collector/intel-dependency-inputs.json",
                "source/collector/intel-python-inputs.json", "source/collector/macos/Package.resolved"}
    if set(policy["pinned_inputs"]) != expected:
        raise ValueError("Intel release policy omits a pinned dependency input")
    for name, checksum in policy["pinned_inputs"].items():
        if not SHA256.fullmatch(checksum) or sha256(repo / name) != checksum:
            raise ValueError("Intel dependency/source recipe differs from the approved pin")
    return policy


def verify_source_freeze(repo: Path, path: Path, expected_commit: str, expected_sha256: str) -> dict:
    if not SHA256.fullmatch(expected_sha256) or path.is_symlink() or not path.is_file():
        raise ValueError("Expected an exact authored-source receipt digest")
    current = source_freeze(repo, expected_commit)
    data = freeze_bytes(current)
    if hashlib.sha256(data).hexdigest() != expected_sha256 or path.read_bytes() != data:
        raise ValueError("Current authored inputs differ from the approved freeze receipt")
    verify_release_policy(repo)
    return current


def candidate_plist(original: dict, *, enable_published_updates: bool = False) -> dict:
    info = dict(original)
    info.update({
        "CFBundleIdentifier": "ai.satoris.amplifai.phone.intel-candidate",
        "CFBundleName": "AMPLIFai Phone Intel Test",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": BUILD,
        "LSMinimumSystemVersion": MINIMUM,
        "SUEnableAutomaticChecks": enable_published_updates,
        "SUAutomaticallyUpdate": enable_published_updates,
        "SUAllowsAutomaticUpdates": enable_published_updates,
        "AMPLIFaiQualifiedUpdateFeed": enable_published_updates,
    })
    info.pop("SUFeedURL", None)
    if enable_published_updates:
        if (info.get("SUPublicEDKey") != "vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs="
                or info.get("SURequireSignedFeed") is not True
                or info.get("SUVerifyUpdateBeforeExtraction") is not True):
            raise ValueError("Intel updates require the retained signed-feed trust configuration")
        info["SUFeedURL"] = "https://amplifai-database-engine.vercel.app/phone/macos/intel-appcast.xml"
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dependency-receipt", type=Path)
    parser.add_argument("--python-sources", type=Path)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--expected-source-manifest-sha256")
    parser.add_argument("--source-freeze-receipt", type=Path)
    parser.add_argument("--freeze-source-receipt", type=Path,
                        help="Write a new reproducible source receipt only; no native build or package.")
    parser.add_argument("--verify-source-freeze", action="store_true",
                        help="Verify the approved receipt only; no native build or package.")
    parser.add_argument("--enable-published-updates", action="store_true",
                        help="Prepare the fixed Intel update channel; final signed feed/archive qualification remains a release gate.")
    args = parser.parse_args()
    if args.freeze_source_receipt is not None:
        if args.verify_source_freeze or args.freeze_source_receipt.resolve().is_relative_to(ROOT.resolve()):
            parser.error("Write the new freeze receipt outside the authored source checkout")
        verify_release_policy(ROOT)
        receipt = source_freeze(ROOT, args.expected_source_commit)
        data = freeze_bytes(receipt)
        if args.expected_source_manifest_sha256 is not None and hashlib.sha256(data).hexdigest() != args.expected_source_manifest_sha256:
            parser.error("Source receipt differs from approved dispatch digest")
        args.freeze_source_receipt.parent.mkdir(parents=True, exist_ok=True)
        with args.freeze_source_receipt.open("xb") as stream:
            stream.write(data)
        print(json.dumps({"commit": receipt["commit"], "source_files": len(receipt["files"]),
                          "source_freeze_sha256": hashlib.sha256(data).hexdigest()}, sort_keys=True))
        return 0
    if args.verify_source_freeze:
        if args.source_freeze_receipt is None or args.expected_source_manifest_sha256 is None:
            parser.error("Verification requires the approved source receipt and digest")
        receipt = verify_source_freeze(ROOT, args.source_freeze_receipt, args.expected_source_commit,
                                       args.expected_source_manifest_sha256)
        print(json.dumps({"commit": receipt["commit"], "source_files": len(receipt["files"]),
                          "source_freeze_sha256": args.expected_source_manifest_sha256}, sort_keys=True))
        return 0
    if any(value is None for value in (args.output, args.dependency_receipt, args.python_sources,
                                      args.source_freeze_receipt, args.expected_source_manifest_sha256)):
        parser.error("Native build requires output, dependency/Python inputs and approved source receipt/digest")
    if sys.platform != "darwin" or platform.machine() != "x86_64":
        parser.error("Build with an x86_64 macOS Python runtime on an Intel runner.")
    if sys.version_info[:2] != (3, 12):
        parser.error("The release dependency lock requires CPython 3.12.")
    frozen = verify_source_freeze(ROOT, args.source_freeze_receipt,
                                  args.expected_source_commit, args.expected_source_manifest_sha256)
    policy = verify_release_policy(ROOT)
    receipt_path = args.dependency_receipt.resolve(strict=True)
    dependency_receipt = json.loads(receipt_path.read_text())
    if dependency_receipt.get("status") != "built-and-validated":
        parser.error("Dependencies must have completed native build and validation.")
    python_sources = args.python_sources.resolve(strict=True)
    python_receipt = json.loads((python_sources / "receipt.json").read_text())
    if (python_receipt.get("status") != "downloaded-and-hash-verified"
            or python_receipt.get("python_version") != platform.python_version()
            or python_receipt.get("target_triple") != "x86_64-apple-darwin"):
        parser.error("Matching Intel Python corresponding source must be verified first.")
    if args.output.exists():
        parser.error("Output must be a new directory; previous builds are preserved.")
    output = args.output.resolve()
    output.mkdir(parents=True)
    shutil.copy2(args.source_freeze_receipt, output / "authored-source-freeze.json")
    env = dict(os.environ, MACOSX_DEPLOYMENT_TARGET=MINIMUM,
               PYTHONPATH=str(SOURCE / "collector"))
    package = SOURCE / "collector/macos"
    swift_build = output / "swift-build"
    swift_args = ["swift", "build", "--configuration", "release", "--arch", "x86_64",
                  "--package-path", str(package), "--scratch-path", str(swift_build)]
    run(swift_args, env=env)
    binary_dir = Path(subprocess.check_output(swift_args + ["--show-bin-path"], env=env, text=True).strip())
    app = output / "AMPLIFai Phone Intel Test.app"
    contents = app / "Contents"
    resources = contents / "Resources"
    (contents / "MacOS").mkdir(parents=True)
    (contents / "Frameworks").mkdir()
    resources.mkdir()
    shutil.copy2(binary_dir / "AmplifaiPhone", contents / "MacOS/AmplifaiPhone")
    with (output / "wrapper-rpath-receipt.json").open("w") as wrapper_receipt:
        run([sys.executable, SOURCE / "script/normalize_macos_wrapper.py", contents / "MacOS/AmplifaiPhone",
             "--architecture", "x86_64"], stdout=wrapper_receipt)
    run(["ditto", binary_dir / "Sparkle.framework", contents / "Frameworks/Sparkle.framework"])
    with (package / "Info.plist").open("rb") as stream:
        info = candidate_plist(plistlib.load(stream), enable_published_updates=args.enable_published_updates)
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    shutil.copy2(SOURCE / "public/brand/amplifai-by-nexus-original.png", resources / "amplifai-logo.png")
    icon_dir = output / "icon"
    icon_dir.mkdir()
    icon_tool = icon_dir / "prepare-icon"
    run(["swiftc", "-parse-as-library", "-target", "x86_64-apple-macos14.0",
         package / "Tools/prepare_app_icon.swift", "-o", icon_tool], env=env)
    run([icon_tool, resources / "amplifai-logo.png", icon_dir], env=env)
    shutil.copy2(icon_dir / "AmplifaiPhone.icns", resources / "AmplifaiPhone.icns")
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
         "--target-arch", "x86_64", "--log-level", "WARN", "--name", "amplifai-agent",
         "--paths", SOURCE / "collector", "--exclude-module", "PIL",
         "--distpath", output / "helper-dist", "--workpath", output / "helper-work",
         "--specpath", output / "helper-spec", SOURCE / "collector/agent_entry.py"], env=env)
    run(["ditto", output / "helper-dist/amplifai-agent", resources / "helper"])
    legal = resources / "Legal"
    legal.mkdir()
    for name in ("LICENSE", "COPYRIGHT", "THIRD-PARTY-NOTICES.txt"):
        shutil.copy2(ROOT / name, legal / name)
    shutil.copy2(receipt_path, legal / "intel-dependency-receipt.json")
    shutil.copy2(args.source_freeze_receipt, legal / "authored-source-freeze.json")
    shutil.copy2(receipt_path.parent / "source-inputs/OpenSSL-LICENSE.txt", legal / "OpenSSL-3.5.9-LICENSE.txt")
    shutil.copy2(python_sources / "receipt.json", legal / "intel-python-source-receipt.json")
    shutil.copy2(python_sources / "INTEL-PYTHON-NOTICES.txt", legal / "INTEL-PYTHON-NOTICES.txt")
    with (legal / "THIRD-PARTY-NOTICES.txt").open("a") as stream:
        stream.write("\n\nINTEL CANDIDATE ADDENDUM\n"
                     "The original ARM runtime inventory above is historical. This Intel candidate uses "
                     "CPython 3.12.15 / python-build-standalone 20261009 and rebuilds cryptography 50.0.1 "
                     "against static OpenSSL 3.5.9. The adjacent INTEL-PYTHON-NOTICES.txt, "
                     "OpenSSL-3.5.9-LICENSE.txt and Intel receipts identify the matching runtime sources "
                     "and notices. Other Python package versions are unchanged.\n")
    (legal / "SOURCE-README.md").write_text(
        "# Intel candidate corresponding source\n\n"
        "This is a modified, ad-hoc signed RC26 Intel test build.\n"
        "Exact current authored source and commit are in the adjacent authored-source-freeze.json and accompanying Intel build-source archive.\n"
        "Historical unchanged dependency source only: " + policy["historical_dependency_source"]["archive_url"] + "\n"
        "The adjacent Intel dependency receipt identifies rebuilt native dependencies.\n"
        "Use the accompanying build-source archive for this exact candidate and its source inputs.\n")
    manifest = {record["path"].removeprefix("source/"): record["sha256"] for record in frozen["files"]
                if record["path"].startswith("source/")}
    (legal / "source-package-file-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # PyInstaller signs its own Mach-O files; sign the copied framework's nested
    # code inside-out, then the framework and enclosing application.
    macho_magics = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                    b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
    for path in sorted(app.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
        if magic in macho_magics:
            run(["codesign", "--force", "--sign", "-", path])
    for path in sorted(app.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir() and not path.is_symlink() and path.suffix in {".app", ".xpc", ".framework"}:
            run(["codesign", "--force", "--sign", "-", path])
    run(["codesign", "--force", "--sign", "-", app])
    run(["codesign", "--verify", "--deep", "--strict", app])
    run([sys.executable, SOURCE / "script/verify_macos_architecture.py", app,
         "--arch", "x86_64", "--minimum-macos", MINIMUM,
         "--output", output / "architecture-receipt.json"])
    helper = resources / "helper/amplifai-agent"
    with (output / "status-receipt.json").open("w") as status_receipt:
        run([sys.executable, SOURCE / "script/verify_frozen_companion_status.py", "--helper", helper,
             "--architecture", "x86_64", "--version", info["CFBundleShortVersionString"],
             "--build", info["CFBundleVersion"]], stdout=status_receipt)
    result = subprocess.run([str(helper), "runtime-check"], env=env, text=True, capture_output=True, check=True)
    (output / "helper-runtime-check.json").write_text(result.stdout)
    verify_source_freeze(ROOT, args.source_freeze_receipt, args.expected_source_commit,
                         args.expected_source_manifest_sha256)
    receipt = {
        "kind": "macos-intel-candidate", "architecture": "x86_64", "minimum_macos_declared": MINIMUM,
        "build_host_macos": platform.mac_ver()[0], "python": sys.version,
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "release": policy["release"],
        "authored_source_commit": frozen["commit"], "authored_source_tree": frozen["tree"],
        "authored_source_files": len(frozen["files"]),
        "source_freeze_sha256": sha256(args.source_freeze_receipt),
        "historical_dependency_source_archive_sha256": policy["historical_dependency_source"]["archive_sha256"],
        "version": info["CFBundleShortVersionString"], "bundle_id": info["CFBundleIdentifier"],
        "swift_sha256": sha256(contents / "MacOS/AmplifaiPhone"), "helper_sha256": sha256(helper),
        "dependency_receipt_sha256": sha256(receipt_path),
        "python_source_receipt_sha256": sha256(python_sources / "receipt.json"),
        "developer_id_signed": False, "notarized": False, "physical_iphone_tested": False,
        "account_save_report_tested": False, "oldest_os_tested": False,
        "account_origin": "https://amplifai-database-engine.vercel.app", "updater_enabled": args.enable_published_updates,
        "update_feed": info.get("SUFeedURL"), "update_channel": "macos-intel-test",
        "build": info["CFBundleVersion"], "final_feed_archive_qualified": False,
    }
    (output / "candidate-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
