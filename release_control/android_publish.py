"""Publish only an exact, runtime-qualified Android signing artifact."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import tempfile

import android_release as android
import publish
import release

RUNTIME_CHECKS = {"upgrade", "api29", "api36", "bulkSelectionIme", "nativeHandoffAck",
                  "expiryRevocation", "cleanup", "syntheticDataOnly"}


def publication_spec(spec: dict) -> dict:
    value = spec.get("publication")
    android.require(isinstance(value, dict) and value.get("runtimeChecks") == dict.fromkeys(RUNTIME_CHECKS, True),
                    "runtime qualification has not been admitted by protected policy")
    for key in ("signedArtifactSha256", "signedApkSha256", "runtimeReceiptSha256"):
        android.require(re.fullmatch(r"[0-9a-f]{64}", value.get(key, "")) is not None,
                        "publication evidence digest missing")
    android.require(re.fullmatch(r"[0-9a-f]{40}", value.get("signingControlCommit", "")) is not None,
                    "signer control commit missing")
    for key in ("signingRunId", "signedArtifactId"):
        android.require(type(value.get(key)) is int and value[key] > 0, "signing provenance missing")
    android.require(value.get("runtimeReceiptPath") == f"release_control/android_runtime/{spec['version']}.json",
                    "runtime receipt must be a version-specific reviewed control file")
    return value


def runtime_receipt(spec: dict, approved: dict) -> dict:
    path = android.ROOT.parent / approved["runtimeReceiptPath"]
    android.require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 1024**2
                    and release.digest(path) == approved["runtimeReceiptSha256"], "runtime evidence file changed")
    receipt = android.read_json(path)
    android.require(receipt.get("sourceCommit") == spec["sourceCommit"] and receipt.get("version") == spec["version"]
                    and receipt.get("build") == spec["build"] and receipt.get("signedApkSha256") == approved["signedApkSha256"]
                    and receipt.get("runtimeChecks") == approved["runtimeChecks"] and receipt.get("privateDataUsed") is False,
                    "runtime evidence belongs to a different source or signed artifact")
    return receipt


def signer_provenance(approved: dict, artifact: dict, run: dict, jobs: dict) -> None:
    android.require(run.get("id") == approved["signingRunId"]
                    and run.get("head_sha") == approved["signingControlCommit"]
                    and run.get("head_branch") == "main" and run.get("path") == android.WORKFLOW
                    and run.get("event") == "workflow_dispatch"
                    and run.get("status") == "completed" and run.get("conclusion") == "success"
                    and run.get("actor", {}).get("login") in ("JmooreOsirs", "bac1876")
                    and run.get("triggering_actor", {}).get("login") == run.get("actor", {}).get("login")
                    and run.get("repository", {}).get("full_name") == android.REPOSITORY
                    and run.get("head_repository", {}).get("full_name") == android.REPOSITORY,
                    "signing workflow provenance changed")
    signers = [j for j in jobs.get("jobs", []) if j.get("name") == "android-sign"]
    android.require(len(signers) == 1 and signers[0].get("conclusion") == "success"
                    and signers[0].get("started_at") and signers[0].get("completed_at")
                    and signers[0]["started_at"] <= artifact.get("created_at", "") <= signers[0]["completed_at"]
                    and artifact.get("id") == approved["signedArtifactId"]
                    and artifact.get("expired") is False
                    and artifact.get("digest") == "sha256:" + approved["signedArtifactSha256"]
                    and artifact.get("workflow_run", {}).get("id") == approved["signingRunId"]
                    and artifact.get("workflow_run", {}).get("head_sha") == approved["signingControlCommit"]
                    and type(artifact.get("size_in_bytes")) is int and 0 < artifact["size_in_bytes"] <= 128 * 1024**2,
                    "signed artifact was not produced by the approved protected signer")


def verify_feed(feed: Path, apk: Path, spec: dict, root: Path) -> None:
    data = feed.read_bytes()
    android.require(len(data) <= 4096 and b"\r" not in data, "invalid feed framing")
    lines = data.decode("ascii").splitlines(keepends=True)
    names = android.asset_names(spec["version"])
    expected = ["AMPLIFAI_ANDROID_UPDATE_V1", "package=" + android.PACKAGE,
                f"versionCode={spec['build']}", f"versionName={spec['version']}", "minSdk=26",
                f"apkBytes={apk.stat().st_size}", "apkSha256=" + release.digest(apk),
                "signerSha256=" + android.CERTIFICATE,
                f"apkUrl=https://github.com/{android.REPOSITORY}/releases/download/android-{spec['version']}/{names['apk']}"]
    android.require(len(lines) == 10 and [line.rstrip("\n") for line in lines[:9]] == expected
                    and all(line.endswith("\n") for line in lines) and lines[-1].startswith("signature="),
                    "signed feed metadata differs from the exact APK")
    signature = base64.b64decode(lines[-1][10:].strip(), validate=True)
    android.require(len(signature) == 384, "unexpected RSA signature size")
    payload = root / "feed-payload"
    payload.write_bytes("".join(lines[:9]).encode("ascii"))
    sig = root / "feed-signature"
    sig.write_bytes(signature)
    public = root / "publisher.pem"
    public.write_text(release.command(["openssl", "pkey", "-pubin", "-inform", "DER", "-in",
                                      android.ROOT / "android_update_publisher.der"]))
    release.command(["openssl", "dgst", "-sha256", "-verify", public, "-signature", sig, payload])


def prior_feed(spec: dict, root: Path, suffix: str) -> None:
    prior = spec["previousFeed"]
    for index, origin in enumerate(("https://amplifai.satoris.ai", "https://amplifai-database-engine.vercel.app")):
        target = root / f"prior-{suffix}-{index}.txt"
        release.download(origin + "/phone/android-update-v1.txt", target, prior["sha256"], prior["bytes"])
        android.require(target.stat().st_size == prior["bytes"], "previous website feed changed")


def release_order(spec: dict, *, own_draft: str | None = None) -> None:
    for value in publish.release_snapshot(android.REPOSITORY):
        match = re.fullmatch(r"android-(20\d{2})\.(\d{2})\.(\d{2})-rc([1-9]\d{0,2})", value.get("tag_name", ""))
        if match is None:
            continue
        version = value["tag_name"][8:]
        if version == own_draft and value.get("draft") is True:
            continue
        year, month, day, candidate = map(int, match.groups())
        build = int(f"{year % 100:02d}{month:02d}{day:02d}{candidate:02d}")
        android.require(build < spec["build"], "same or newer Android release already exists")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy = android.read_json(android.ROOT / "android_policy.json")
    android.context(policy, os.environ)
    spec = android.specification(policy, os.environ.get("RELEASE_VERSION", ""))
    approved = publication_spec(spec)
    runtime = runtime_receipt(spec, approved)
    android.protected_custody(policy, spec["sourceCommit"], policy["publicationEnvironment"])
    android.require(not args.output.exists(), "publication output already exists")
    artifact = release.api(f"{android.REPOSITORY}/actions/artifacts/{approved['signedArtifactId']}")
    run = release.api(f"{android.REPOSITORY}/actions/runs/{approved['signingRunId']}")
    jobs = release.api(f"{android.REPOSITORY}/actions/runs/{approved['signingRunId']}/jobs?per_page=100")
    signer_provenance(approved, artifact, run, jobs)
    names = android.asset_names(spec["version"])
    with tempfile.TemporaryDirectory(prefix="android-publication-") as temporary:
        root = Path(temporary)
        archive = root / "signed.zip"
        release.download(f"https://api.github.com/repos/{android.REPOSITORY}/actions/artifacts/{approved['signedArtifactId']}/zip",
                         archive, approved["signedArtifactSha256"], 128 * 1024**2, authenticated=True)
        inputs = root / "inputs"
        inputs.mkdir()
        android.extract_flat(archive, inputs, {names["apk"], names["source"], names["feed"],
                                               "instrumentation.apk", "signing-receipt.json", "build-qualification.json"})
        signed = android.read_json(inputs / "signing-receipt.json")
        android.require(signed.get("sourceCommit") == spec["sourceCommit"] and signed.get("version") == spec["version"]
                        and signed.get("build") == spec["build"] and signed.get("certificateSha256") == android.CERTIFICATE
                        and signed.get("controlCommit") == approved["signingControlCommit"]
                        and signed.get("signingRunId") == approved["signingRunId"], "signing receipt changed")
        android.require(set(signed["files"]) == {names["apk"], names["source"], names["feed"], "instrumentation.apk", "build-qualification.json"},
                        "signed file inventory changed")
        for name, record in signed["files"].items():
            android.file_matches(inputs / name, record)
        apk = inputs / names["apk"]
        android.require(release.digest(apk) == approved["signedApkSha256"], "runtime-qualified APK differs")
        android.file_matches(inputs / names["source"], spec["sourceArchive"])
        tools = Path(os.environ["ANDROID_HOME"]) / "build-tools/36.0.0"
        android.apk_metadata(apk, tools, spec)
        android.signature(apk, tools)
        verify_feed(inputs / names["feed"], apk, spec, root)
        qualification = {"schema": 1, "version": spec["version"], "build": spec["build"],
                         "sourceCommit": spec["sourceCommit"], "certificateSha256": android.CERTIFICATE,
                         "signedApkSha256": approved["signedApkSha256"], "runtimeChecks": approved["runtimeChecks"],
                         "runtimeReceiptSha256": approved["runtimeReceiptSha256"],
                         "runtimeReceiptUrl": f"https://github.com/{android.REPOSITORY}/blob/{os.environ['GITHUB_SHA']}/{approved['runtimeReceiptPath']}",
                         "runtimeEvidence": runtime,
                         "buildQualification": android.read_json(inputs / "build-qualification.json"),
                         "signingRunId": approved["signingRunId"], "publicationRunId": int(os.environ["GITHUB_RUN_ID"]),
                         "physicalCustomerAcceptance": False}
        (inputs / names["qualification"]).write_text(json.dumps(qualification, indent=2) + "\n")
        files = [inputs / names[key] for key in ("apk", "source", "feed", "qualification")]
        sums = inputs / names["checksums"]
        sums.write_text("".join(f"{release.digest(path)}  {path.name}\n" for path in files))
        files.append(sums)
        tag = "android-" + spec["version"]
        release_order(spec)
        prior_feed(spec, root, "before")
        publish.absent_release_and_tag(android.REPOSITORY, tag)
        publish.create_exact_tag(android.REPOSITORY, tag, spec["sourceCommit"])
        publish.verify_exact_tag(android.REPOSITORY, tag, spec["sourceCommit"])
        notes = root / "notes.txt"
        notes.write_text(f"AMPLIFai Phone Android {spec['version']}\nSource: {spec['sourceCommit']}\n"
                         "Official RSA-signed website APK. Synthetic runtime qualification; physical customer acceptance remains separate.\n")
        repo = "github.com/" + android.REPOSITORY
        release.command(["gh", "release", "create", tag, "--repo", repo, "--draft", "--verify-tag",
                         "--title", "AMPLIFai Phone Android " + spec["version"], "--notes-file", notes])
        release.command(["gh", "release", "upload", tag, *files, "--repo", repo])
        draft = root / "draft"
        draft.mkdir()
        for path in files:
            release.command(["gh", "release", "download", tag, "--repo", repo, "--pattern", path.name, "--dir", draft])
            android.file_matches(draft / path.name, {"bytes": path.stat().st_size, "sha256": release.digest(path)})
        release_order(spec, own_draft=spec["version"])
        prior_feed(spec, root, "publish")
        publish.verify_exact_tag(android.REPOSITORY, tag, spec["sourceCommit"])
        release.command(["gh", "release", "edit", tag, "--repo", repo, "--draft=false", "--prerelease=false", "--latest=false"])
        args.output.mkdir()
        assets = []
        for path in files:
            digest = release.digest(path)
            url = f"https://github.com/{android.REPOSITORY}/releases/download/{tag}/{path.name}"
            release.download(url, args.output / path.name, digest, path.stat().st_size)
            assets.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest, "url": url})
        receipt = {"sourceCommit": spec["sourceCommit"], "version": spec["version"], "build": spec["build"],
                   "releaseUrl": f"https://github.com/{android.REPOSITORY}/releases/tag/{tag}", "assets": assets,
                   "websitePromoted": False, "physicalCustomerAcceptance": False}
        (args.output / "publication-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
