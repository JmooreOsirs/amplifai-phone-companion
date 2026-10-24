"""Secret-free Android producer executed from protected main."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile

import android_release as android
import release


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)


def source_archive(root: Path, source: str, version: str, target: Path) -> dict:
    listing = git(root, "ls-tree", "-rz", "--full-tree", source, "--", "source/collector/android",
                  "source/script/android_release_signing.swift")
    files = []
    prefix = f"AMPLIFai-Phone-Android-Corresponding-Source-{version}/"
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as zipped:
        for entry in listing.split(b"\0"):
            if not entry:
                continue
            metadata, raw_name = entry.split(b"\t", 1)
            mode, kind, _sha = metadata.decode().split()
            name = raw_name.decode()
            android.require(kind == "blob" and mode in ("100644", "100755")
                            and "\\" not in name and not name.startswith("/")
                            and not any(part in ("..", ".git", "build", ".gradle") for part in Path(name).parts)
                            and not name.lower().endswith((".p12", ".jks", ".keystore", ".env")),
                            "unexpected source archive entry")
            data = git(root, "show", source + ":" + name)
            row = {"path": name, "mode": mode, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            files.append(row)
            info = zipfile.ZipInfo(prefix + name, (1980, 1, 1, 0, 0, 0))
            info.external_attr = int(mode, 8) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zipped.writestr(info, data)
        android.require(len(files) >= 83 and any(r["path"].endswith("assets/legal/LICENSE") for r in files),
                        "corresponding source or legal notices missing")
        manifest = {"sourceCommit": source, "files": files}
        zipped.writestr(prefix + "SOURCE-MANIFEST.json", json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    with zipfile.ZipFile(target) as zipped:
        android.require(len(zipped.infolist()) == len(files) + 1, "source archive inventory changed")
        for row in files:
            info = zipped.getinfo(prefix + row["path"])
            android.require(info.file_size == row["bytes"] and info.external_attr >> 16 == int(row["mode"], 8)
                            and hashlib.sha256(zipped.read(info)).hexdigest() == row["sha256"],
                            "source archive readback changed")
    return manifest


def unit_results(directory: Path, minimum: int) -> dict:
    paths = list(directory.glob("TEST-*.xml"))
    android.require(0 < len(paths) <= 128, "unit test report set is unavailable")
    counts = dict.fromkeys(("tests", "failures", "errors", "skipped"), 0)
    names = set()
    for path in paths:
        android.require(path.stat().st_size <= 1024**2, "oversized unit report")
        root = ET.fromstring(path.read_bytes())
        android.require(root.tag == "testsuite", "unexpected unit report")
        names.add(root.attrib["name"].rsplit(".", 1)[-1])
        for key in counts:
            counts[key] += int(root.get(key, "0"))
    android.require(counts["tests"] >= minimum and all(counts[key] == 0 for key in ("failures", "errors", "skipped"))
                    and {"NativeAuthorityTest", "NativeAccountReturnTest", "NativeCompletionStateTest",
                         "ReleasePairingContractTest"} <= names, "required Android regression suite did not pass")
    return counts


def updater_fixtures(project: Path, scratch: Path, clean: dict) -> dict:
    """Prepare public/synthetic test-only APKs without any release credential."""
    assets = project / "app/src/androidTest/assets"
    assets.mkdir(parents=True, exist_ok=True)
    historical = assets / "rc7-signed.apk"
    url = ("https://github.com/" + android.REPOSITORY + "/releases/download/"
           "android-2026.10.08-rc7/AMPLIFai-Phone-Android-2026.10.08-rc7.apk")
    expected = "e4ce5ad5397fa82449f10e74f86b4070ae20fc9c1650329fc6472b842676b777"
    release.download(url, historical, expected, 108317)
    android.require(historical.stat().st_size == 108317, "historical fixture size changed")
    tools = Path(clean["ANDROID_HOME"]) / "build-tools/36.0.0"
    manifest = project / "app/src/androidTest/fixtures/wrong-signer/AndroidManifest.xml"
    android.require(manifest.is_file(), "synthetic fixture source is missing")
    scratch.mkdir(mode=0o700)
    key = scratch / "synthetic-only.p12"
    unsigned = scratch / "synthetic-only-unsigned.apk"
    wrong = assets / "rc8-debug.apk"

    def run(args):
        subprocess.run([str(arg) for arg in args], env=clean, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=90)

    try:
        run([tools / "aapt", "package", "-f", "-M", manifest, "-I",
             Path(clean["ANDROID_HOME"]) / "platforms/android-37.0/android.jar", "-F", unsigned])
        run([Path(clean["JAVA_HOME"]) / "bin/keytool", "-genkeypair", "-noprompt",
             "-keystore", key, "-storetype", "PKCS12", "-storepass", "synthetic-test-only",
             "-keypass", "synthetic-test-only", "-alias", "synthetic-test-only", "-keyalg", "RSA",
             "-keysize", "2048", "-validity", "2", "-dname", "CN=Synthetic updater rejection fixture"])
        key.chmod(0o600)
        run([tools / "apksigner", "sign", "--ks", key, "--ks-pass", "pass:synthetic-test-only",
             "--key-pass", "pass:synthetic-test-only", "--v4-signing-enabled", "false", "--out", wrong, unsigned])
        result = subprocess.check_output([str(tools / "apksigner"), "verify", "--verbose", "--print-certs", str(wrong)],
                                         env=clean, text=True, stderr=subprocess.PIPE, timeout=30)
        android.require("Number of signers: 1" in result and android.CERTIFICATE not in result,
                        "synthetic fixture must have a different signer")
        android.require(0 < wrong.stat().st_size < 65536, "synthetic fixture is not bounded")
        return {"historical": {"url": url, "sha256": expected, "bytes": 108317},
                "wrongSigner": {"sha256": release.digest(wrong), "bytes": wrong.stat().st_size,
                                "manifestSha256": release.digest(manifest), "productionIdentityUsed": False}}
    finally:
        shutil.rmtree(scratch)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy = android.read_json(android.ROOT / "android_policy.json")
    android.context(policy, os.environ, android.BUILD_WORKFLOW)
    android.require(re.fullmatch(r"[0-9a-f]{40}", args.source) is not None, "exact source SHA required")
    comparison = release.api(f"{android.REPOSITORY}/compare/{args.source}...release/native")
    android.require(comparison.get("status") in ("ahead", "identical")
                    and comparison.get("merge_base_commit", {}).get("sha") == args.source,
                    "candidate must be in the current protected native lineage")
    android.require(not args.output.exists(), "build output already exists")
    workspace = Path(os.environ["RUNNER_TEMP"]) / "android-source"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), "fetch", "--depth=1", "https://github.com/" + android.REPOSITORY + ".git", args.source],
                   check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(workspace), "checkout", "--detach", "FETCH_HEAD"], check=True,
                   stdout=subprocess.DEVNULL)
    android.require(git(workspace, "rev-parse", "HEAD").decode().strip() == args.source, "source checkout changed")
    project = workspace / "source/collector/android"
    config = (project / "app/build.gradle.kts").read_text()
    version = re.search(r'versionName = "([^"]+)"', config).group(1)
    build = int(re.search(r'versionCode = ([0-9]+)', config).group(1))
    android.require(re.fullmatch(r"20\d{2}\.\d{2}\.\d{2}-rc[1-9]\d{0,2}", version) is not None
                    and 26102312 < build < 2**31, "candidate release identity must advance RC12")
    clean = {key: os.environ[key] for key in ("PATH", "HOME", "JAVA_HOME", "ANDROID_HOME", "ANDROID_SDK_ROOT", "LANG", "TMPDIR") if key in os.environ}
    clean["GRADLE_USER_HOME"] = str(Path(os.environ["RUNNER_TEMP"]) / "android-gradle")
    fixtures = updater_fixtures(project, Path(os.environ["RUNNER_TEMP"]) / "android-fixture-key", clean)
    with (Path(os.environ["RUNNER_TEMP"]) / "android-build.log").open("w") as log:
        result = subprocess.run([str(project / "gradlew"), "--no-daemon", "--no-configuration-cache", "--no-build-cache",
                                 "testWebsiteDebugUnitTest", "testPlayDebugUnitTest", "lintWebsiteRelease",
                                 "assembleWebsiteRelease", "assembleWebsiteDebugAndroidTest"], cwd=project,
                                env=clean, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
    android.require(result.returncode == 0, "Android build/tests/lint failed; signing is unavailable")
    counts = {"website": unit_results(project / "app/build/test-results/testWebsiteDebugUnitTest", 68),
              "play": unit_results(project / "app/build/test-results/testPlayDebugUnitTest", 64)}
    args.output.mkdir()
    for relative, name in (("app/build/outputs/apk/website/release/app-website-release-unsigned.apk", "app-website-release-unsigned.apk"),
                           ("app/build/outputs/apk/androidTest/website/debug/app-website-debug-androidTest.apk", "app-website-debug-androidTest.apk")):
        path = project / relative
        android.require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= android.MAX_APK,
                        "bounded APK output missing")
        shutil.copyfile(path, args.output / name)
    source_archive(workspace, args.source, version, args.output / "source.zip")
    receipt = {"schema": 1, "sourceCommit": args.source, "version": version, "build": build,
               "buildRunId": int(os.environ["GITHUB_RUN_ID"]), "privateDataUsed": False,
               "checks": {"websiteUnit": True, "playUnit": True, "lint": True, "unsignedBuild": True},
               "updaterFixtures": fixtures,
               "unitTests": counts, "files": {p.name: {"bytes": p.stat().st_size, "sha256": release.digest(p)} for p in args.output.iterdir()}}
    (args.output / "build-qualification.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
