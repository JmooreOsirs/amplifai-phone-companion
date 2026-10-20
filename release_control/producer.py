"""Secret-free qualification of an app built by the reviewed native workflow."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import zipfile

import release

MAX_SOURCE = 4 * 1024**3
APPEND_PATHS = ["release-evidence/final-signed-app-inventory.json", "release-evidence/source-finalization.json"]
PHONE_RUN_MODULES = ("test_bridge_run_timing", "test_bridge_recovery")
PHONE_RUN_SCRIPTS = ("check_model.sh", "check_window_lifecycle.sh")


def require_secret_free_job() -> None:
    if any(os.environ.get(key) for key in ("APPLE_CERTIFICATE_P12_BASE64", "SPARKLE_PRIVATE_KEY", "NOTARY_APP_PASSWORD")):
        raise ValueError("native build/qualification job must not receive product secrets")


def run_phone_run_checks(checkout: Path) -> None:
    """Shared by protected qualification and the native premerge gate; never skip missing tests."""
    require_secret_free_job()
    paths = [checkout / "source/collector/tests" / (name + ".py") for name in PHONE_RUN_MODULES]
    paths += [checkout / "source/collector/macos/Tests" / name for name in PHONE_RUN_SCRIPTS]
    for path in paths:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(checkout.resolve()):
            raise ValueError("required phone-run qualification file missing or unsafe: " + path.name)
    env = dict(os.environ, PYTHONPATH=str(checkout / "source/collector") + ":" + str(checkout / "source/collector/tests"),
               PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", AMPLIFAI_TEST_PYTHON=sys.executable)
    subprocess.run([sys.executable, "-m", "unittest", *PHONE_RUN_MODULES, "-v"],
                   cwd=checkout, env=env, check=True, timeout=1200)
    for script in PHONE_RUN_SCRIPTS:
        subprocess.run(["sh", str(checkout / "source/collector/macos/Tests" / script)],
                       cwd=checkout, env=env, check=True, timeout=1200)


def source_members(archive: zipfile.ZipFile) -> dict:
    members = archive.infolist()
    if len(members) > 30000 or sum(item.file_size for item in members) > MAX_SOURCE:
        raise ValueError("source archive exceeds bounds")
    result = {}
    for item in members:
        path = PurePosixPath(item.filename)
        mode = item.external_attr >> 16
        if (path.is_absolute() or not path.parts or ".." in path.parts or "\\" in item.filename
                or path.as_posix() != item.filename or item.filename in result
                or stat.S_IFMT(mode) not in {0, stat.S_IFREG} or item.is_dir()):
            raise ValueError("source archive must contain unique regular relative files")
        result[item.filename] = item
    return result


def source_map(archive: zipfile.ZipFile) -> dict:
    members = source_members(archive)
    if "release-source-map.json" not in members or members["release-source-map.json"].file_size > 4 * 1024**2:
        raise ValueError("bounded reviewed source map required")
    mapping = release.exact_json(archive.read("release-source-map.json"))
    if type(mapping.get("schema")) is not int or mapping["schema"] != 1 or mapping.get("appendPaths") != APPEND_PATHS:
        raise ValueError("source map schema/append-only evidence paths differ")
    records = mapping["files"]
    if len({row["path"] for row in records}) != len(records):
        raise ValueError("duplicate source map record")
    if set(members) != {row["path"] for row in records} | {"release-source-map.json"}:
        raise ValueError("source map must inventory every payload member")
    for row in records:
        member = members[row["path"]]
        with archive.open(member) as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if member.file_size != row["bytes"] or checksum != row["sha256"]:
            raise ValueError("source payload readback differs")
    return mapping


def validate_source(archive_path: Path, checkout: Path, app: Path, commit: str, native: dict) -> dict:
    if release.command(["git", "-C", checkout, "rev-parse", "HEAD"]) != commit:
        raise ValueError("build checkout differs from approved full source SHA")
    if release.command(["git", "-C", checkout, "status", "--porcelain", "--untracked-files=all"]):
        raise ValueError("source checkout changed during native build")
    tracked = subprocess.check_output(["git", "-C", str(checkout), "ls-files", "-z"]).decode().split("\0")
    with zipfile.ZipFile(archive_path) as archive:
        mapping = source_map(archive)
        if mapping.get("sourceCommit") != commit or set(mapping["authored"]) != set(filter(None, tracked)):
            raise ValueError("source archive omits current authored files or source identity")
        records = {row["path"]: row for row in mapping["files"]}
        for name, member in mapping["authored"].items():
            path = checkout / name
            if path.is_symlink() or release.digest(path) != records[member]["sha256"]:
                raise ValueError("authored archive bytes differ from reviewed checkout")
        native_files = {row["path"]: row["sha256"] for row in native["files"]}
        if set(mapping["native"]) != set(native_files):
            raise ValueError("native source mapping omits actual Mach-O code")
        for name, row in mapping["native"].items():
            if (row["preSignSha256"] != native_files[name] or not row["sourceInputs"] or not row["noticeInputs"]
                    or any(member not in records for member in row["sourceInputs"] + row["noticeInputs"])):
                raise ValueError("native source/notice mapping differs from actual app")
        legal = mapping["legal"]
        required = {"LICENSE", "COPYRIGHT", "SOURCE-README.md", "THIRD-PARTY-NOTICES.txt", "source-package-file-manifest.json"}
        if set(legal) != required:
            raise ValueError("all five Legal/source offer files required")
        for name, member in legal.items():
            path = app / "Contents/Resources/Legal" / name
            if path.is_symlink() or release.digest(path) != records[member]["sha256"]:
                raise ValueError("in-app legal/source offer differs from source payload")
    return {"authoredFiles": len(mapping["authored"]), "nativeFiles": len(mapping["native"]),
            "sourceFiles": len(mapping["files"]), "sourceMapSha256": hashlib.sha256(json.dumps(mapping, sort_keys=True).encode()).hexdigest()}


def qualification_record(source: dict, run_id: int, app_zip: Path, source_zip: Path) -> dict:
    profile = release.qualification_profile(source)
    record = {key: source[key] for key in ("sourceCommit", "channel", "version", "build")}
    record.update(schema=2 if profile else 1, buildRunId=run_id, privateDataUsed=False,
                  checks=release.qualification_checks(source))
    if profile:
        record["qualificationProfile"] = profile
    for key, path in (("appArchive", app_zip), ("sourceArchive", source_zip)):
        record[key] = {"name": path.name, "bytes": path.stat().st_size, "sha256": release.digest(path)}
    return record


def validate_signing_input(source_zip: Path, app: Path, commit: str, channel: str) -> None:
    from verify_macos_architecture import audit
    native = audit(app, release.CHANNELS[channel][0], "14.0")
    if not native["passed"]:
        raise ValueError("fresh signing-input native audit failed")
    actual = {row["path"]: row["sha256"] for row in native["files"]}
    with zipfile.ZipFile(source_zip) as archive:
        mapping = source_map(archive)
    expected = {path: row["preSignSha256"] for path, row in mapping["native"].items()}
    if mapping["sourceCommit"] != commit or actual != expected:
        raise ValueError("fresh signing-input bytes differ from qualified source mapping")


def qualify(source: dict, checkout: Path, app: Path, source_zip: Path, output: Path, run_id: int) -> None:
    # The build job must not have product credentials, even by misconfiguration.
    require_secret_free_job()
    profile = release.qualification_profile(source)
    if app.name != release.CHANNELS[source["channel"]][2] + ".app":
        raise ValueError("delivery app name differs from the channel bundle contract")
    release.validate_app(app, source, source["channel"])
    from verify_macos_architecture import audit
    if profile:
        run_phone_run_checks(checkout)
    env = dict(os.environ, PYTHONPATH=str(checkout / "source/collector") + ":" + str(checkout / "source/collector/tests"))
    modules = ["test_metadata", "test_ios_backup", "test_agent", "test_bridge", "test_bridge_ack",
               "test_workspace", "test_backup_capacity", "test_backup_stream", "test_backup_watchdog",
               "test_contact_provenance", "test_companion_status", "test_filtered_journal_cleanup",
               "test_source_diagnostics", "test_source_diagnostic_flow"]
    subprocess.run([sys.executable, "-m", "unittest", *modules, "-v"], cwd=checkout, env=env, check=True, timeout=1200)
    for script in ("check_flow.sh", "check_updater.sh", "check_contact_provenance.sh"):
        subprocess.run(["sh", str(checkout / "source/collector/macos/Tests" / script)], cwd=checkout,
                       env=env, check=True, timeout=1200)
    helper = app / "Contents/Resources/helper/amplifai-agent"
    subprocess.run([sys.executable, str(checkout / "source/collector/tests/qualify_frozen_encrypted_worker.py"),
                    "--helper", str(helper)], cwd=checkout, env=env, check=True, timeout=1200)
    subprocess.run([sys.executable, str(checkout / "source/script/verify_frozen_companion_status.py"),
                    "--helper", str(helper), "--architecture", release.CHANNELS[source["channel"]][0],
                    "--version", source["version"], "--build", source["build"]], cwd=checkout, env=env, check=True, timeout=120)
    # Qualification executes candidate code; it may have changed the app.
    native = audit(app, release.CHANNELS[source["channel"]][0], "14.0")
    if not native["passed"]:
        raise ValueError("final qualified native audit failed")
    validate_source(source_zip, checkout, app, source["sourceCommit"], native)
    output.mkdir(parents=True, exist_ok=False)
    app_zip = output / "app.zip"
    with zipfile.ZipFile(app_zip, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(app.rglob("*")):
            name = app.name + "/" + path.relative_to(app).as_posix()
            if path.is_symlink():
                item = zipfile.ZipInfo(name)
                item.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(item, str(path.readlink()).encode())
            elif path.is_file():
                archive.write(path, name)
    copied_source = output / "source.zip"
    shutil.copyfile(source_zip, copied_source)
    record = qualification_record(source, run_id, app_zip, copied_source)
    release.validate_delivery_record(record, source, {"runId": run_id})
    (output / "qualification.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkout", "app", "source-archive", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--channel", choices=release.CHANNELS, required=True)
    parser.add_argument("--policy", type=Path, required=True,
                        help="Current reviewed main policy read as data; producer code stays at the reviewed native source SHA")
    args = parser.parse_args()
    policy = release.exact_json(args.policy.read_bytes())
    source = next((item for item in policy["approvedSources"] if item["sourceCommit"] == args.source_commit
                   and item["channel"] == args.channel and item.get("releaseAuthorized") is True), None)
    if (source is None or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or os.environ.get("GITHUB_REPOSITORY") != policy["repository"]
            or os.environ.get("GITHUB_REF") != policy["sourceRef"]
            or os.environ.get("GITHUB_SHA") != args.source_commit
            or os.environ.get("GITHUB_REF_PROTECTED") != "true"
            or os.environ.get("GITHUB_ACTOR") not in policy["operators"]
            or os.environ.get("GITHUB_TRIGGERING_ACTOR") != os.environ.get("GITHUB_ACTOR")
            or os.environ.get("GITHUB_WORKFLOW_REF") != policy["repository"] + "/" + source["buildWorkflow"] + "@" + policy["sourceRef"]):
        parser.error("reviewed source authorization and native manual build run required")
    qualify(source, args.checkout.resolve(), args.app.resolve(), args.source_archive.resolve(),
            args.output, int(os.environ["GITHUB_RUN_ID"]))


if __name__ == "__main__":
    main()
