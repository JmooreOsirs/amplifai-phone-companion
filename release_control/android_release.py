"""Trusted Android release control; candidate source and APKs are never executed here."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from urllib.parse import quote
import zipfile

import authority
import release

ROOT = Path(__file__).resolve().parent
REPOSITORY = "JmooreOsirs/amplifai-phone-companion"
WORKFLOW = ".github/workflows/android-release.yml"
BUILD_WORKFLOW = ".github/workflows/android-build.yml"
CERTIFICATE = "9f3fa36a8f3b44b8083080828d772cd5990552275a6ffbc68a097826e4e3c4c2"
PACKAGE = "ai.satoris.amplifai.phone"
ALIAS = "amplifai-android-release"
MAX_APK = 32 * 1024**2
MAX_SOURCE = 32 * 1024**2
BUILD_FILES = {"app-website-release-unsigned.apk", "app-website-debug-androidTest.apk",
               "source.zip", "build-qualification.json"}


def tool(args: list, *, signing: bool = False) -> str:
    env = {key: os.environ[key] for key in ("PATH", "HOME", "JAVA_HOME", "LANG", "TMPDIR") if key in os.environ}
    if signing:
        env["ANDROID_RELEASE_PASSWORD"] = os.environ["ANDROID_RELEASE_PASSWORD"]
    try:
        result = subprocess.run(list(map(str, args)), env=env, capture_output=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Android signing tool unavailable or timed out") from None
    require(result.returncode == 0, "Android signing tool failed; private output suppressed")
    return result.stdout.decode("utf-8")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def read_json(path: Path) -> dict:
    return release.exact_json(path.read_bytes())


def context(policy: dict, env: dict, workflow: str = WORKFLOW) -> None:
    release.validate_release_authority(policy)
    require(policy["repository"] == REPOSITORY and policy["controlRef"] == "refs/heads/main"
            and policy["sourceRef"] == "refs/heads/release/native", "unexpected repository policy")
    require(env.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and env.get("GITHUB_REPOSITORY") == REPOSITORY
            and env.get("GITHUB_REF") == "refs/heads/main"
            and env.get("GITHUB_REF_PROTECTED") == "true"
            and env.get("GITHUB_RUN_ATTEMPT") == "1"
            and env.get("GITHUB_ACTOR") in policy["operators"]
            and env.get("GITHUB_TRIGGERING_ACTOR") == env.get("GITHUB_ACTOR")
            and env.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + workflow + "@refs/heads/main",
            "release requires protected main and the original authorized operator")


def specification(policy: dict, version: str) -> dict:
    require(re.fullmatch(r"20\d{2}\.\d{2}\.\d{2}-rc[1-9]\d{0,2}", version) is not None,
            "invalid release version")
    spec = policy.get("releases", {}).get(version)
    require(isinstance(spec, dict) and spec.get("version") == version
            and spec.get("releaseAuthorized") is True, "release is not authorized in protected policy")
    require(re.fullmatch(r"[0-9a-f]{40}", spec.get("sourceCommit", "")) is not None,
            "exact source commit required")
    require(type(spec.get("build")) is int and 26102312 < spec["build"] < 2**31,
            "release build must advance RC12")
    for name in ("unsignedApk", "instrumentationApk", "sourceArchive"):
        record = spec.get(name, {})
        require(set(record) == {"bytes", "sha256"} and type(record["bytes"]) is int
                and 0 < record["bytes"] <= MAX_SOURCE
                and re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is not None,
                "exact bounded release file required")
    return spec


def protected_custody(policy: dict, source: str, environment_name: str, other_environment: str | None = None) -> None:
    def metadata(path):
        return release.api(path.removeprefix("repos/"), allow_list=True)
    authority.require_approved_merge(metadata, policy, os.environ["GITHUB_SHA"], "main")
    authority.require_approved_merge(metadata, policy, source, "release/native")
    for ref in ("main", "release/native"):
        encoded = quote(ref, safe="")
        protection = release.api(f"{REPOSITORY}/branches/{encoded}/protection", audit=True)
        owners = release.api(f"{REPOSITORY}/contents/.github/CODEOWNERS?ref={encoded}")
        require(owners.get("encoding") == "base64", "effective CODEOWNERS unavailable")
        release.validate_branch_custody(policy, protection, base64.b64decode(owners["content"]).decode())
    protected = release.api(f"{REPOSITORY}/branches/main")
    for name in [environment_name] + ([other_environment] if other_environment else []):
        env = release.api(f"{REPOSITORY}/environments/{name}")
        branches = release.api(f"{REPOSITORY}/environments/{name}/deployment-branch-policies")
        release.validate_protection({**policy, "signingEnvironment": name}, env, branches, protected, os.environ)


def validate_authorization_provenance(artifact: dict, jobs: dict, env: dict) -> None:
    inspectors = [job for job in jobs.get("jobs", []) if job.get("name") == "android-inspect"]
    require(artifact.get("id") == int(env["ANDROID_AUTHORIZATION_ARTIFACT_ID"])
            and artifact.get("digest") == "sha256:" + env["ANDROID_AUTHORIZATION_ARTIFACT_SHA256"]
            and artifact.get("expired") is False and 0 < artifact.get("size_in_bytes", 0) <= 1024**2
            and artifact.get("workflow_run", {}).get("id") == int(env["GITHUB_RUN_ID"])
            and artifact.get("workflow_run", {}).get("head_sha") == env["GITHUB_SHA"]
            and len(inspectors) == 1 and inspectors[0].get("conclusion") == "success"
            and inspectors[0].get("run_attempt") == 1
            and inspectors[0].get("started_at") and inspectors[0].get("completed_at")
            and inspectors[0]["started_at"] <= artifact.get("created_at", "") <= inspectors[0]["completed_at"],
            "authorization must come from this run's successful protected preflight")


def authorization_receipt(policy: dict, spec: dict) -> dict:
    return {"authorized": True, "controlCommit": os.environ["GITHUB_SHA"],
            "sourceCommit": spec["sourceCommit"], "version": spec["version"], "build": spec["build"],
            "runId": int(os.environ["GITHUB_RUN_ID"]),
            "runAttempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
            "policySha256": release.digest(ROOT / "android_policy.json"),
            "releaseSpecSha256": hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "checkedEnvironments": [policy["signingEnvironment"], policy["publicationEnvironment"]],
            "custodyChecks": {"main": True, "release/native": True, "signingEnvironment": True, "publicationEnvironment": True}}


def verify_authorization(policy: dict, spec: dict, root: Path) -> None:
    artifact_id = int(os.environ["ANDROID_AUTHORIZATION_ARTIFACT_ID"])
    artifact = release.api(f"{REPOSITORY}/actions/artifacts/{artifact_id}")
    jobs = release.api(f"{REPOSITORY}/actions/runs/{os.environ['GITHUB_RUN_ID']}/attempts/1/jobs?per_page=100")
    validate_authorization_provenance(artifact, jobs, os.environ)
    archive = root / "authorization.zip"
    release.download(f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{artifact_id}/zip", archive,
                     os.environ["ANDROID_AUTHORIZATION_ARTIFACT_SHA256"], 1024**2, authenticated=True)
    proof = root / "authorization"
    proof.mkdir()
    extract_flat(archive, proof, {"authorization.json"})
    require(read_json(proof / "authorization.json") == authorization_receipt(policy, spec),
            "authorization receipt does not match current control/source/policy")


def validate_build(spec: dict, run: dict, artifact: dict) -> None:
    build = spec["buildArtifact"]
    require(run.get("id") == build["runId"] and run.get("head_sha") == build["controlCommit"]
            and run.get("head_branch") == "main" and run.get("path") == BUILD_WORKFLOW
            and run.get("event") == "workflow_dispatch" and run.get("status") == "completed"
            and run.get("run_attempt") == 1
            and run.get("conclusion") == "success"
            and run.get("repository", {}).get("full_name") == REPOSITORY
            and run.get("head_repository", {}).get("full_name") == REPOSITORY
            and run.get("actor", {}).get("login") in ("JmooreOsirs", "bac1876")
            and run.get("triggering_actor", {}).get("login") == run.get("actor", {}).get("login"),
            "build provenance is not an authorized successful main dispatch")
    require(artifact.get("id") == build["id"] and artifact.get("expired") is False
            and artifact.get("digest") == "sha256:" + build["sha256"]
            and artifact.get("workflow_run", {}).get("id") == build["runId"]
            and artifact.get("workflow_run", {}).get("head_sha") == build["controlCommit"]
            and type(artifact.get("size_in_bytes")) is int
            and 0 < artifact["size_in_bytes"] <= 128 * 1024**2,
            "build artifact identity or digest changed")


def extract_flat(archive: Path, destination: Path, expected: set[str]) -> None:
    with zipfile.ZipFile(archive) as zipped:
        rows = zipped.infolist()
        require(len(rows) == len(expected) and {r.filename for r in rows} == expected,
                "unexpected or duplicate artifact members")
        require(sum(r.file_size for r in rows) <= 128 * 1024**2, "artifact exceeds size bound")
        for row in rows:
            require(row.filename == Path(row.filename).name and "\\" not in row.filename
                    and not row.is_dir() and not row.flag_bits & 1
                    and stat.S_IFMT(row.external_attr >> 16) in (0, stat.S_IFREG),
                    "artifact must contain regular flat files")
            require(row.file_size <= MAX_SOURCE, "artifact member exceeds size bound")
            with zipped.open(row) as source, (destination / row.filename).open("xb") as output:
                shutil.copyfileobj(source, output)


def file_matches(path: Path, record: dict) -> None:
    require(path.is_file() and not path.is_symlink() and path.stat().st_size == record["bytes"]
            and release.digest(path) == record["sha256"], "release file bytes differ from reviewed policy")


def inspect_build(policy: dict, spec: dict, root: Path) -> dict:
    build = spec["buildArtifact"]
    run = release.api(f"{REPOSITORY}/actions/runs/{build['runId']}")
    artifact = release.api(f"{REPOSITORY}/actions/artifacts/{build['id']}")
    validate_build(spec, run, artifact)
    comparison = release.api(f"{REPOSITORY}/compare/{policy['qualifiedAncestor']}...{spec['sourceCommit']}")
    require(comparison.get("status") in ("ahead", "identical")
            and comparison.get("merge_base_commit", {}).get("sha") == policy["qualifiedAncestor"],
            "candidate is outside the accepted Android lineage")
    archive = root / "build.zip"
    release.download(f"https://api.github.com/repos/{REPOSITORY}/actions/artifacts/{build['id']}/zip",
                     archive, build["sha256"], 128 * 1024**2, authenticated=True)
    inputs = root / "inputs"
    inputs.mkdir()
    extract_flat(archive, inputs, BUILD_FILES)
    for name, field in (("app-website-release-unsigned.apk", "unsignedApk"),
                        ("app-website-debug-androidTest.apk", "instrumentationApk"),
                        ("source.zip", "sourceArchive")):
        file_matches(inputs / name, spec[field])
    receipt = read_json(inputs / "build-qualification.json")
    require(receipt.get("sourceCommit") == spec["sourceCommit"]
            and receipt.get("build") == spec["build"] and receipt.get("version") == spec["version"]
            and receipt.get("buildRunId") == build["runId"]
            and receipt.get("privateDataUsed") is False
            and receipt.get("checks") == {"websiteUnit": True, "playUnit": True, "lint": True, "unsignedBuild": True},
            "build qualification is incomplete")
    return receipt


def apk_metadata(apk: Path, tools: Path, spec: dict, *, instrumentation: bool = False) -> None:
    require(apk.is_file() and not apk.is_symlink() and 0 < apk.stat().st_size <= MAX_APK,
            "APK is outside the signed update byte bound")
    text = tool([tools / "aapt", "dump", "badging", apk])
    package = PACKAGE + ".test" if instrumentation else PACKAGE
    match = re.search(r"^package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'", text, re.M)
    require(match is not None and match.group(1) == package, "unexpected Android package")
    if not instrumentation:
        require(match.group(2) == str(spec['build']) and match.group(3) == spec['version']
                and re.search(r"^sdkVersion:'26'$", text, re.M) is not None and "application-debuggable" not in text,
                "APK metadata does not match the authorized release")


def signature(apk: Path, tools: Path) -> None:
    output = tool([tools / "apksigner", "verify", "--verbose", "--print-certs", apk])
    require("Number of signers: 1" in output and f"Signer #1 certificate SHA-256 digest: {CERTIFICATE}" in output,
            "APK signer changed")


def asset_names(version: str) -> dict:
    return {"apk": f"AMPLIFai-Phone-Android-{version}.apk",
            "source": f"AMPLIFai-Phone-Android-Corresponding-Source-{version}.zip",
            "feed": "android-update-v1.txt", "qualification": "android-qualification.json",
            "checksums": f"SHA256SUMS-Android-{version.rsplit('-', 1)[1]}.txt"}


def sign(policy: dict, spec: dict, output: Path, tools: Path) -> None:
    names = asset_names(spec["version"])
    require(not output.exists(), "signing output already exists")
    with tempfile.TemporaryDirectory(prefix="amplifai-android-keys-", dir=os.environ["RUNNER_TEMP"]) as tmp:
        private = Path(tmp)
        verify_authorization(policy, spec, private)
        inspect_build(policy, spec, private)
        inputs = private / "inputs"
        apk_metadata(inputs / "app-website-release-unsigned.apk", tools, spec)
        apk_metadata(inputs / "app-website-debug-androidTest.apk", tools, spec, instrumentation=True)
        encoded = os.environ.get("ANDROID_RELEASE_PKCS12_BASE64", "")
        require(0 < len(encoded) <= 128 * 1024 and len(os.environ.get("ANDROID_RELEASE_PASSWORD", "")) == 64,
                "existing signing identity is unavailable")
        key = private / "release.p12"
        key.write_bytes(base64.b64decode(encoded, validate=True))
        key.chmod(0o600)
        java = Path(os.environ["JAVA_HOME"]) / "bin/java"
        tool([java, ROOT / "AndroidFeedSigner.java", "check", key], signing=True)
        output.mkdir()
        for original, target in (("app-website-release-unsigned.apk", names["apk"]),
                                  ("app-website-debug-androidTest.apk", "instrumentation.apk")):
            tool([tools / "apksigner", "sign", "--ks", key, "--ks-key-alias", ALIAS,
                             "--ks-pass", "env:ANDROID_RELEASE_PASSWORD", "--key-pass", "env:ANDROID_RELEASE_PASSWORD",
                             "--v4-signing-enabled", "false",
                             "--out", output / target, inputs / original], signing=True)
            signature(output / target, tools)
        apk_metadata(output / names["apk"], tools, spec)
        shutil.copyfile(inputs / "source.zip", output / names["source"])
        apk = output / names["apk"]
        url = f"https://github.com/{REPOSITORY}/releases/download/android-{spec['version']}/{names['apk']}"
        body = (f"AMPLIFAI_ANDROID_UPDATE_V1\npackage={PACKAGE}\nversionCode={spec['build']}\n"
                f"versionName={spec['version']}\nminSdk=26\napkBytes={apk.stat().st_size}\n"
                f"apkSha256={release.digest(apk)}\nsignerSha256={CERTIFICATE}\napkUrl={url}\n")
        payload = private / "feed-payload"
        payload.write_text(body)
        tool([java, ROOT / "AndroidFeedSigner.java", "feed", key, payload, output / names["feed"]], signing=True)
        shutil.copyfile(inputs / "build-qualification.json", output / "build-qualification.json")
        files = {p.name: {"bytes": p.stat().st_size, "sha256": release.digest(p)} for p in output.iterdir()}
        signed = {"schema": 1, "sourceCommit": spec["sourceCommit"], "version": spec["version"],
                  "build": spec["build"], "controlCommit": os.environ["GITHUB_SHA"],
                  "signingRunId": int(os.environ["GITHUB_RUN_ID"]), "certificateSha256": CERTIFICATE,
                  "files": files, "privateDataUsed": False, "runtimeQualified": False}
        (output / "signing-receipt.json").write_text(json.dumps(signed, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("inspect", "sign"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy = read_json(ROOT / "android_policy.json")
    context(policy, os.environ)
    spec = specification(policy, os.environ.get("RELEASE_VERSION", ""))
    if args.mode == "sign":
        sign(policy, spec, args.output, Path(os.environ["ANDROID_HOME"]) / "build-tools/36.0.0")
    else:
        protected_custody(policy, spec["sourceCommit"], policy["signingEnvironment"], policy["publicationEnvironment"])
        with tempfile.TemporaryDirectory(prefix="android-inspect-") as tmp:
            inspect_build(policy, spec, Path(tmp))
        args.output.mkdir()
        (args.output / "authorization.json").write_text(json.dumps(authorization_receipt(policy, spec), indent=2) + "\n")


if __name__ == "__main__":
    main()
