#!/usr/bin/env python3
"""Build RC24 as a separate, ad-hoc signed Intel candidate; never install it.

Run with the prepared CPython 3.12 Intel environment. This intentionally does
not modify the existing ARM release scripts, sign with Developer ID, contact
an iPhone, or publish/enable a website download.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "source"
MINIMUM = "14.0"
BASELINE_PATH = ROOT / "docs/INTEL-RC24-BASELINE.json"
INTEL_MODIFIED_BASE_FILES = {
    "source/collector/macos/Sources/Services/CompanionUpdater.swift",
    "source/collector/macos/Sources/Views/ContentView.swift",
    "source/collector/macos/Tests/CompanionUpdaterChecks.swift",
    "source/collector/macos/Tests/check_flow.sh",
    "source/collector/macos/Tests/check_updater.sh",
    "source/collector/macos/Tests/check_contact_provenance.sh",
    "source/collector/macos/Sources/Services/CollectorModel.swift",
    "source/collector/amplifai_phone/workspace.py",
    "source/collector/amplifai_phone/sqlite_size_compat.py",
    "source/collector/tests/test_workspace.py",
    "source/collector/tests/test_sqlite_size_compat.py",
}


def run(command: list[str], **kwargs):
    print("+", " ".join(map(str, command)), flush=True)
    return subprocess.run(list(map(str, command)), check=True, **kwargs)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_base_source() -> dict:
    baseline = json.loads(BASELINE_PATH.read_text())
    if baseline.get("release") != "macos-2026.10.13-rc24-release":
        raise ValueError("Intel build requires the accepted RC24 source baseline")
    files = baseline["files"]
    if not INTEL_MODIFIED_BASE_FILES.issubset(files):
        raise ValueError("RC24 baseline omits a modified Intel source file")
    for relative, expected in files.items():
        if relative in INTEL_MODIFIED_BASE_FILES:
            continue
        path = ROOT / relative
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"Source differs from accepted RC24: {relative}")
    return baseline


def candidate_plist(original: dict) -> dict:
    info = dict(original)
    info.update({
        "CFBundleIdentifier": "ai.satoris.amplifai.phone.intel-candidate",
        "CFBundleName": "AMPLIFai Phone Intel Test",
        "CFBundleShortVersionString": "2026.10.13-rc24-intel1",
        "CFBundleVersion": "261013241",
        "LSMinimumSystemVersion": MINIMUM,
        "SUEnableAutomaticChecks": False,
        "SUAutomaticallyUpdate": False,
        "SUAllowsAutomaticUpdates": False,
    })
    # Defense in depth: compiled x86_64 updater refuses all checks, and this
    # artifact does not carry the ARM appcast URL at all.
    info.pop("SUFeedURL", None)
    return info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dependency-receipt", type=Path, required=True)
    parser.add_argument("--python-sources", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() != "x86_64":
        parser.error("Build with an x86_64 macOS Python runtime on an Intel runner.")
    if sys.version_info[:2] != (3, 12):
        parser.error("The release dependency lock requires CPython 3.12.")
    baseline = verify_base_source()
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
    run(["ditto", binary_dir / "Sparkle.framework", contents / "Frameworks/Sparkle.framework"])
    with (package / "Info.plist").open("rb") as stream:
        info = candidate_plist(plistlib.load(stream))
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
        "This is a modified, ad-hoc signed RC24 Intel build. It is not the signed ARM release.\n"
        "Exact authored source is in the accompanying Intel build-source archive.\n"
        "Original RC24 dependencies/source: " + baseline["source_archive_url"] + "\n"
        "The adjacent Intel dependency receipt identifies rebuilt native dependencies.\n"
        "Use the accompanying build-source archive for this exact candidate and its source inputs.\n")
    manifest = {str(p.relative_to(SOURCE)): sha256(p) for p in sorted(SOURCE.rglob("*"))
                if p.is_file() and not any(part in {".build", "__pycache__", "dist"} for part in p.parts)}
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
    probe_home = output / "probe-home"
    probe_home.mkdir()
    probe_env = dict(env, HOME=str(probe_home))
    for command in ("inspect", "runtime-check"):
        result = subprocess.run([str(helper), command], env=probe_env, text=True, capture_output=True, check=True)
        (output / f"helper-{command}.json").write_text(result.stdout)
    receipt = {
        "kind": "macos-intel-candidate", "architecture": "x86_64", "minimum_macos_declared": MINIMUM,
        "build_host_macos": platform.mac_ver()[0], "python": sys.version,
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "base_release": baseline["release"],
        "base_source_archive_sha256": baseline["source_archive_sha256"],
        "base_source_files_checked": len(baseline["files"]) - len(INTEL_MODIFIED_BASE_FILES),
        "version": info["CFBundleShortVersionString"], "bundle_id": info["CFBundleIdentifier"],
        "swift_sha256": sha256(contents / "MacOS/AmplifaiPhone"), "helper_sha256": sha256(helper),
        "dependency_receipt_sha256": sha256(receipt_path),
        "python_source_receipt_sha256": sha256(python_sources / "receipt.json"),
        "developer_id_signed": False, "notarized": False, "physical_iphone_tested": False,
        "account_save_report_tested": False, "oldest_os_tested": False,
        "account_origin": "https://amplifai-database-engine.vercel.app", "updater_enabled": False,
    }
    (output / "candidate-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
