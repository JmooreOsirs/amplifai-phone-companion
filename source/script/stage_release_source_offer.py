#!/usr/bin/env python3
"""Bind a clean native candidate to pinned dependency sources and stage its Legal offer.

Only a temporary, unsigned candidate may be supplied. This performs technical source
attribution; it does not claim binary reproduction or legal clearance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT.parent / "release_control"), str(ROOT / "script")]
import producer
from verify_macos_architecture import audit
from scan_companion_bundle import SECRET, SENSITIVE_NAME, TEXT_SUFFIXES


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def safe_name(name):
    path = PurePosixPath(name)
    if (not path.parts or path.is_absolute() or ".." in path.parts
            or "\\" in name or path.as_posix() != name):
        raise ValueError("unsafe source member")
    return name


def selected_dependency(name, channel):
    common = ("third_party/source_archives/", "third_party/notices/")
    if name.startswith(common):
        return True
    if channel == "macos-arm64":
        return name.startswith("third_party/artifact-sboms/") or name in {
            "THIRD-PARTY-NOTICES.txt", "third_party/source-archive-manifest.json",
            "third_party/native-source-manifest.json", "third_party/notice-extraction-manifest.json",
            "third_party/sparkle-source-manifest.json"}
    return name.startswith(("intel-dependency-source-inputs/", "intel-python-source-inputs/",
                            "third_party/current-intel-sboms/")) or name in {
        "INTEL-PYTHON-NOTICES.txt", "INTEL-RUST-NOTICES.txt", "INTEL-THIRD-PARTY-NOTICES.txt",
        "OpenSSL-3.5.9-LICENSE.txt", "third_party/current-intel-source-closure.json",
        "third_party/retained-source-input-manifest.json"}


def extract_dependencies(archive_path, output, channel):
    """One bounded pass; never extract baseline authored code or old app inventories."""
    seen = set()
    total = 0
    prefix = None

    def copy(name, size, regular, stream_factory):
        nonlocal total, prefix
        # Release archives have exactly one named top-level directory.
        safe_name(name)
        path = PurePosixPath(name)
        if len(path.parts) < 2:
            return
        if prefix is None:
            prefix = path.parts[0]
        if path.parts[0] != prefix:
            raise ValueError("baseline source archive has multiple roots")
        relative = safe_name("/".join(path.parts[1:]))
        if not selected_dependency(relative, channel):
            return
        if not regular or relative in seen:
            raise ValueError("dependency source must be a unique regular file")
        total += size
        if len(seen) >= 29000 or total > producer.MAX_SOURCE:
            raise ValueError("dependency source exceeds bounds")
        seen.add(relative)
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with stream_factory() as source, target.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        if target.stat().st_size != size:
            raise ValueError("dependency extraction size differs")

    if zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path) as archive:
            for item in archive.infolist():
                if item.is_dir():
                    continue
                mode = stat.S_IFMT(item.external_attr >> 16)
                copy(item.filename, item.file_size, mode in {0, stat.S_IFREG}, lambda: archive.open(item))
    else:
        with tarfile.open(archive_path, "r|gz") as archive:
            for item in archive:
                if item.isdir():
                    continue
                copy(item.name, item.size, item.isfile(), lambda: archive.extractfile(item))
    if not seen:
        raise ValueError("baseline contains no selected source inputs")


def records(root):
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("source offer contains a symlink")
        if path.is_file():
            result.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
                           "sha256": digest(path)})
    return result


def verify_inputs(checkout, dependencies, app, channel, inputs):
    for name, key in (("portable-requirements.lock", "portableLockSha256"),
                      ("macos/Package.resolved", "sparklePackageSha256")):
        if digest(checkout / "source/collector" / name) != inputs[key]:
            raise ValueError("dependency lock or Sparkle pin differs from retained closure")
    sbom_root = app / "Contents/Resources/helper/_internal/cryptography-50.0.1.dist-info/sboms"
    if channel == "macos-arm64":
        manifests = {name: json.loads((dependencies / "third_party" / name).read_text()) for name in (
            "source-archive-manifest.json", "native-source-manifest.json", "notice-extraction-manifest.json",
            "sparkle-source-manifest.json")}
        python = manifests["source-archive-manifest.json"]
        native = manifests["native-source-manifest.json"]
        notices = manifests["notice-extraction-manifest.json"]
        sparkle = manifests["sparkle-source-manifest.json"]
        if python["total_locked"] != 102 or len(python["records"]) != 102 or len(native["records"]) != 56:
            raise ValueError("retained dependency closure shape changed")
        expected = [("third_party/source_archives/" + row["filename"], row["sha256"])
                    for row in python["records"] + native["records"] + [sparkle]]
        expected += [("third_party/" + row["output"], row["sha256"])
                     for row in notices["extracted"] + notices["native_extracted"]]
        expected.append(("third_party/notices/Sparkle-LICENSE", sparkle["license_sha256"]))
        for name, checksum in expected:
            if digest(dependencies / safe_name(name)) != checksum:
                raise ValueError("retained source or notice checksum differs")
        package = json.loads((checkout / "source/collector/macos/Package.resolved").read_text())
        if not any(pin["identity"] == "sparkle" and pin["state"]["revision"] == sparkle["revision"]
                   and pin["state"]["version"] == sparkle["version"] for pin in package["pins"]):
            raise ValueError("Sparkle source revision differs")
        for name in ("sbom.json", "cryptography-rust.cyclonedx.json"):
            if digest(sbom_root / name) != digest(dependencies / "third_party/artifact-sboms" / name):
                raise ValueError("current helper SBOM differs; requalify dependency closure")
    else:
        closure = json.loads((dependencies / "third_party/current-intel-source-closure.json").read_text())
        if (closure["pythonVersion"] != inputs["pythonVersion"]
                or closure["runtimeArchiveSha256"] != inputs["runtime"]["sha256"]):
            raise ValueError("Intel runtime differs from retained closure")
        name = "cryptography-rust.cyclonedx.json"
        if digest(sbom_root / name) != digest(dependencies / "third_party/current-intel-sboms" / name):
            raise ValueError("current Intel SBOM differs; requalify dependency closure")


def native_inputs(path, files, authored, channel):
    dep = "dependencies/"
    sources = sorted(name for name in files if name.startswith(dep) and "/source_archives/" in name)
    notices = sorted(name for name in files if name.startswith(dep) and "/notices/" in name)
    if "Sparkle.framework/" in path:
        return ([name for name in sources if name.endswith("Sparkle-2.10.0-source.tar")],
                [name for name in notices if "Sparkle" in name])
    if path == "Contents/MacOS/AmplifaiPhone":
        return ([member for name, member in authored.items() if name.startswith("source/collector/macos/")],
                [authored["LICENSE"], authored["COPYRIGHT"]])
    if path == "Contents/Resources/helper/amplifai-agent":
        return ([name for name in sources if "/pyinstaller-" in name] +
                [member for name, member in authored.items() if name.startswith("source/collector/")
                 and name.endswith(".py")],
                [name for name in notices if "/pyinstaller-" in name] + [authored["LICENSE"]])
    components = {"_cffi_backend.": "cffi", "/charset_normalizer/": "charset_normalizer",
                  "/psutil/": "psutil", "/wcwidth/": "wcwidth", "/cryptography/": "cryptography"}
    component = next((value for key, value in components.items() if key in path), None)
    if component:
        selected = [name for name in sources if PurePosixPath(name).name.startswith(component + "-")]
        selected_notices = [name for name in notices if "/" + component.replace("_", "-") + "-" in name
                            or "/" + component + "-" in name]
        if component == "cryptography":
            # Exact current SBOM gates reuse of the complete static/Rust source closure.
            selected += [name for name in sources if name.endswith(".crate")
                         or PurePosixPath(name).name.startswith("openssl-")]
            if channel == "macos-intel-test":
                selected += [name for name in files if name.startswith(dep + "intel-dependency-source-inputs/")]
            selected_notices += notices
        return sorted(set(selected)), sorted(set(selected_notices))
    if path.endswith("/libpython3.12.dylib"):
        if channel == "macos-intel-test":
            return ([name for name in files if name.startswith(dep + "intel-python-source-inputs/")],
                    [dep + "INTEL-PYTHON-NOTICES.txt"])
        return ([name for name in sources if any(PurePosixPath(name).name.startswith(prefix) for prefix in (
            "Python-", "python-build-standalone-", "bzip2-", "expat-", "mpdecimal-", "openssl-", "sqlite", "xz-"))],
                [name for name in notices if "/native/" in name])
    raise ValueError("unmapped native component: " + path)


def stage(checkout, app, baseline, output, channel):
    checkout = checkout.resolve(strict=True)
    if app.is_symlink():
        raise ValueError("candidate app must not be a symlink")
    app = app.resolve(strict=True)
    # No installed app mutation or existing signed candidate reuse.
    if not str(app).startswith(("/private/tmp/", "/tmp/", "/private/var/folders/", "/var/folders/",
                                "/Users/runner/", "/Volumes/workspace/")):
        raise ValueError("stage Legal only in a temporary build candidate")
    for relative in ("Contents", "Contents/Resources", "Contents/Resources/Legal"):
        if (app / relative).is_symlink():
            raise ValueError("candidate Legal path traverses a symlink")
    signature = subprocess.run(["codesign", "-dv", str(app)], capture_output=True, text=True)
    if "Authority=" in signature.stderr:
        raise ValueError("stage Legal before distribution signing")
    if subprocess.run(["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=all"],
                      capture_output=True, check=True, text=True).stdout:
        raise ValueError("source staging requires a clean committed checkout")
    commit = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    inputs = json.loads((checkout / "source/collector/macos-release-source-inputs.json").read_text())["channels"][channel]
    if baseline.is_symlink() or baseline.stat().st_size != inputs["baseline"]["bytes"] or digest(baseline) != inputs["baseline"]["sha256"]:
        raise ValueError("pinned baseline source archive differs")
    output.mkdir(parents=True, exist_ok=False)
    tree = output / "source"
    tree.mkdir()
    dependencies = tree / "dependencies"
    extract_dependencies(baseline, dependencies, channel)
    verify_inputs(checkout, dependencies, app, channel, inputs)
    authored = {}
    tracked = subprocess.check_output(["git", "-C", str(checkout), "ls-files", "-z"]).decode().split("\0")
    for name in filter(None, tracked):
        safe_name(name)
        original = checkout / name
        if original.is_symlink() or not original.is_file() or SENSITIVE_NAME.search(name):
            raise ValueError("unsupported or sensitive authored input")
        if original.suffix.lower() in TEXT_SUFFIXES | {".yml", ".yaml"} and SECRET.search(original.read_bytes()):
            raise ValueError("possible secret in authored source")
        member = "authored/" + name
        target = tree / member
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        authored[name] = member
    # Check readable embedded authored Python independently of the frozen helper tests.
    for name, member in authored.items():
        if name.startswith("source/collector/amplifai_phone/") and name.endswith(".py"):
            embedded = app / "Contents/Resources" / name.removeprefix("source/")
            if embedded.is_symlink() or digest(embedded) != digest(tree / member):
                raise ValueError("embedded authored Python differs from current source")
    legal = {name: "legal/" + name for name in ("LICENSE", "COPYRIGHT", "SOURCE-README.md", "THIRD-PARTY-NOTICES.txt")}
    (tree / "legal").mkdir()
    for name in ("LICENSE", "COPYRIGHT"):
        shutil.copyfile(checkout / name, tree / legal[name])
    notices_name = "THIRD-PARTY-NOTICES.txt" if channel == "macos-arm64" else "INTEL-THIRD-PARTY-NOTICES.txt"
    shutil.copyfile(dependencies / notices_name, tree / legal["THIRD-PARTY-NOTICES.txt"])
    (tree / legal["SOURCE-README.md"]).write_text(
        "# AMPLIFai Phone corresponding source\n\n"
        f"Current authored commit: `{commit}`. Channel: `{channel}`.\n\n"
        "`authored/` contains every tracked file from that clean commit. `dependencies/` contains "
        "checksum-pinned retained dependency sources, notices and provider recipes. Retained declarations "
        "describe their historical baseline; release-source-map.json binds current native file hashes. "
        "Current helper SBOM equality and dependency/runtime pins gate reuse. No upstream binary "
        "reproduction or legal determination is claimed. No collection or personal data is included.\n")
    native = audit(app, "arm64" if channel == "macos-arm64" else "x86_64", "14.0")
    if not native["passed"]:
        raise ValueError("current native architecture audit failed")
    write_json(tree / "release-evidence/source-binding.json", {
        "schema": 1, "sourceCommit": commit, "channel": channel, "baseline": inputs["baseline"],
        "runtime": inputs["runtime"], "privateDataUsed": False,
        "retainedSbomsMatch": True, "binaryReproductionClaimed": False, "legalDeterminationClaimed": False})
    # Manifest omits itself and the map to avoid circular hashes.
    manifest = "legal/source-package-file-manifest.json"
    write_json(tree / manifest, {"schema": 1, "sourceCommit": commit,
        "excluded": [manifest, "release-source-map.json", *producer.APPEND_PATHS], "files": records(tree)})
    legal["source-package-file-manifest.json"] = manifest
    payload = records(tree)
    files = {row["path"] for row in payload}
    native_map = {}
    for row in native["files"]:
        sources, notices = native_inputs(row["path"], files, authored, channel)
        if not sources or not notices or any(name not in files for name in sources + notices):
            raise ValueError("native component lacks exact source/notice members: " + row["path"])
        native_map[row["path"]] = {"preSignSha256": row["sha256"], "sourceInputs": sources, "noticeInputs": notices}
    write_json(tree / "release-source-map.json", {"schema": 1, "sourceCommit": commit,
        "files": payload, "authored": authored, "native": native_map, "legal": legal,
        "appendPaths": producer.APPEND_PATHS})
    legal_dir = app / "Contents/Resources/Legal"
    if legal_dir.is_symlink():
        raise ValueError("candidate Legal directory is a symlink")
    legal_dir.mkdir(exist_ok=True)
    for name, member in legal.items():
        target = legal_dir / name
        if target.is_symlink():
            raise ValueError("candidate Legal target is a symlink")
        shutil.copyfile(tree / member, target)
    source_zip = output / "source.zip"
    with zipfile.ZipFile(source_zip, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(tree.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(tree).as_posix())
    receipt = producer.validate_source(source_zip, checkout, app, commit, native)
    write_json(output / "source-staging-receipt.json", {"schema": 1, "sourceCommit": commit,
        "channel": channel, **receipt, "sourceArchiveSha256": digest(source_zip), "privateDataUsed": False})
    return source_zip


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkout", "app", "baseline", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--channel", choices=("macos-arm64", "macos-intel-test"), required=True)
    args = parser.parse_args()
    print(stage(args.checkout, args.app, args.baseline, args.output, args.channel))


if __name__ == "__main__":
    main()
