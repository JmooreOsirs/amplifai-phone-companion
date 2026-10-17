"""Isolated immutable GitHub publication; never write website feeds or use keys."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import tempfile
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

import feeds
import finalize
import release


def validate_signer_provenance(artifact: dict, jobs: dict, env: dict) -> None:
    signers = [job for job in jobs.get("jobs", []) if job.get("name") == "macos-sign-and-notarize"]
    if (artifact.get("id") != int(env["SIGNED_ARTIFACT_ID"])
            or artifact.get("digest") != "sha256:" + env["SIGNED_ARTIFACT_SHA256"]
            or artifact.get("expired") is not False
            or artifact.get("workflow_run", {}).get("id") != int(env["GITHUB_RUN_ID"])
            or artifact.get("workflow_run", {}).get("head_sha") != env["GITHUB_SHA"]
            or not 0 < artifact.get("size_in_bytes", 0) <= release.MAX_ARTIFACT
            or len(signers) != 1 or signers[0].get("conclusion") != "success"
            or not signers[0].get("started_at") or not signers[0].get("completed_at")
            or not signers[0]["started_at"] <= artifact.get("created_at", "") <= signers[0]["completed_at"]):
        raise ValueError("signed artifact must come from the successful signer in this run and attempt")


def extract_delivery(archive: Path, root: Path) -> None:
    with zipfile.ZipFile(archive) as zipped:
        seen = set()
        if len(zipped.infolist()) > 32 or sum(item.file_size for item in zipped.infolist()) > 4 * 1024**3:
            raise ValueError("signed delivery exceeds bounds")
        for item in zipped.infolist():
            name = PurePosixPath(item.filename)
            if (name.is_absolute() or ".." in name.parts or "\\" in item.filename
                    or name.as_posix() != item.filename or item.filename in seen
                    or item.is_dir() or stat.S_IFMT(item.external_attr >> 16) not in {0, stat.S_IFREG}):
                raise ValueError("signed delivery must contain unique regular relative files")
            seen.add(item.filename)
            target = root.joinpath(*name.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(item) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination)


def build_from_tag(tag: str, channel: str) -> int | None:
    pattern = r"macos-(20[0-9]{2})\.([0-9]{2})\.([0-9]{2})-rc([1-9][0-9]*)(?:-intel([1-9][0-9]*))?-(release|test)"
    match = re.fullmatch(pattern, tag)
    if match is None:
        return None
    year, month, day, rc, intel, suffix = match.groups()
    if (channel == "macos-arm64" and (intel is not None or suffix != "release")
            or channel == "macos-intel-test" and (intel is None or suffix != "test")):
        return None
    return int(year[-2:] + month + day + rc + (intel or ""))


def validate_release_order(source: dict, releases: list, *, own_draft: str | None = None) -> None:
    candidate = int(source["build"])
    if build_from_tag(feeds.names(source)["tag"], source["channel"]) != candidate:
        raise ValueError("reviewed source build does not match the existing channel version/tag convention")
    for item in releases:
        if item.get("tag_name") == own_draft and item.get("draft") is True:
            continue
        build = build_from_tag(item.get("tag_name", ""), source["channel"])
        if build is not None and build >= candidate:
            raise ValueError("same/newer published or draft channel release exists; do not publish out of order")


def release_snapshot(repository: str, *, github=None) -> list:
    result = []
    for page in range(1, 11):
        if github is not None:
            rows = github.request_json(f"repos/{repository}/releases?per_page=100&page={page}")
            if not isinstance(rows, list):
                raise ValueError("live release list unavailable")
            result.extend(rows)
            if len(rows) < 100:
                return result
            continue
        request = urllib.request.Request(f"https://api.github.com/repos/{repository}/releases?per_page=100&page={page}", headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(4 * 1024**2 + 1)
        except urllib.error.HTTPError as error:
            error.close()
            raise ValueError("live release ordering unavailable") from None
        except OSError:
            raise ValueError("live release ordering unavailable") from None
        if len(data) > 4 * 1024**2:
            raise ValueError("live release page exceeds bounds")
        rows = release.exact_json(b'{"items":' + data + b'}')["items"]
        if not isinstance(rows, list):
            raise ValueError("live release list unavailable")
        result.extend(rows)
        if len(rows) < 100:
            return result
    raise ValueError("release inventory exceeds bounded ordering audit")


def verify_previous_feed(source: dict, root: Path) -> None:
    receipt = release.exact_json((root / "feed/release-receipt.json").read_bytes())
    rollback = receipt.get("rollback")
    if not rollback or rollback["name"] != "previous-" + release.CHANNELS[source["channel"]][3]:
        raise ValueError("exact observed previous feed binding required")
    target = root / ("live-previous-" + str(len(list(root.glob("live-previous-*")))))
    release.download("https://amplifai-database-engine.vercel.app/phone/macos/" + release.CHANNELS[source["channel"]][3],
                     target, rollback["sha256"], rollback["bytes"])


def validate_delivery(root: Path, source: dict, request: dict, env: dict | None = None,
                      *, control_commit: str | None = None) -> tuple[dict, list[Path]]:
    if (env is None) == (control_commit is None):
        raise ValueError("exactly one real hosted or local control identity required")
    expected_control = env["GITHUB_SHA"] if env is not None else control_commit
    if not isinstance(expected_control, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_control):
        raise ValueError("exact control commit required")
    receipt = release.exact_json((root / "release-receipt.json").read_bytes())
    signed = receipt.get("signing", {})
    if (receipt.get("request") != request or receipt.get("controlCommit") != expected_control
            or receipt.get("signingApproved") is not True or signed.get("notarized") is not True
            or signed.get("sourceFinalizationRequired") is not False or signed.get("feedPrepared") is not True
            or signed.get("developerId") != release.IDENTITY or signed.get("sparklePublicKey") != release.PUBLIC_KEY):
        raise ValueError("signed receipt lacks qualified source/package/feed binding")
    names = feeds.names(source)
    package, source_zip = root / names["package"], root / names["source"]
    spec = release.exact_json((root / "release-descriptor.json").read_bytes())
    for key, path in (("package", package), ("source", source_zip)):
        feeds.existing.check_asset(path, spec[key])
        if release.digest(path) != signed[key + "Sha256"]:
            raise ValueError("signer receipt and descriptor differ")
    final = finalize.verify_final_source(source_zip, source["sourceCommit"])
    if final["signedInventorySha256"] != release.digest(root / "final-signed-app-inventory.json"):
        raise ValueError("final source is bound to a different signed app")
    feed_name = release.CHANNELS[source["channel"]][3]
    feed = root / "feed" / feed_name
    feed_receipt = release.exact_json((root / "feed/release-receipt.json").read_bytes())
    if (feed_receipt["package"] != spec["package"] or feed_receipt["source"] != spec["source"]
            or feed_receipt["feedSha256"] != release.digest(feed)
            or feed_receipt["feedBytes"] != feed.stat().st_size or signed["feed"] != feed_receipt):
        raise ValueError("signed feed preparation receipt differs")
    parsed = feeds.existing.validate_appcast(feed.read_bytes(), source["channel"], "0", openssl="openssl")
    if (parsed["build"] != source["build"] or parsed["version"] != source["version"]
            or parsed["installerUrl"] != spec["installerUrl"] or parsed["installerBytes"] != spec["package"]["bytes"]
            or any(spec.get(key) != source[key] for key in ("channel", "version", "build"))):
        raise ValueError("signed feed release differs from reviewed source")
    signature = (root / (package.name + ".sig")).read_text().strip()
    xml = ET.fromstring(feed.read_bytes())
    if (signature != parsed["installerEdSignature"]
            or xml.findtext("channel/item/{" + feeds.existing.IDENTITY + "}sourceSha256") != spec["source"]["sha256"]):
        raise ValueError("signed feed package/source binding differs")
    feeds.existing.verify_signature(package.read_bytes(), signature, openssl="openssl")
    return spec, [package, source_zip, root / (package.name + ".sig"), feed,
                  root / "release-descriptor.json", root / "release-receipt.json", root / "final-signed-app-inventory.json"]


def verify_local_package(root: Path, source: dict, *, temp_root: Path) -> dict:
    """Reopen the signed image as data and verify the app actually being published."""
    if (not temp_root.is_dir() or temp_root.is_symlink() or temp_root.stat().st_uid != os.getuid()
            or stat.S_IMODE(temp_root.stat().st_mode) & 0o077):
        raise ValueError("local verification requires an owner-only temporary directory")
    work = Path(tempfile.mkdtemp(prefix="amplifai-local-verify-", dir=temp_root))
    work.chmod(0o700)
    mount = work / "mounted"
    mount.mkdir()
    device = None
    package = root / feeds.names(source)["package"]

    def identity(path: Path, label: str, *, deep: bool = False) -> None:
        args = ["codesign", "--verify", "--strict", "--all-architectures"]
        release.command(args + (["--deep"] if deep else []) + [path])
        prefix = work / (label + "-certificate-")
        release.command(["codesign", "--display", "--extract-certificates=" + str(prefix), path])
        leaf = Path(str(prefix) + "0")
        if (leaf.is_symlink() or not leaf.is_file() or not 0 < leaf.stat().st_size <= 65536
                or hashlib.sha1(leaf.read_bytes()).hexdigest().upper() != release.CERTIFICATE_SHA1):
            raise ValueError("published package/app certificate differs from pinned Developer ID")
        release.command(["xcrun", "stapler", "validate", path])

    try:
        identity(package, "package")
        release.command(["spctl", "--assess", "--type", "open", "--context", "context:primary-signature", package])
        attached = plistlib.loads(release.command(["hdiutil", "attach", "-readonly", "-nobrowse", "-plist",
                                                   "-mountpoint", mount, package]).encode())
        entities = attached.get("system-entities", [])
        devices = [item.get("dev-entry", "") for item in entities
                   if re.fullmatch(r"/dev/disk[0-9]+", item.get("dev-entry", ""))]
        physical = [item["dev-entry"] for item in entities
                    if item.get("dev-entry") in devices
                    and item.get("content-hint") in {"GUID_partition_scheme", "Apple_partition_scheme"}]
        # APFS adds a synthesized whole disk; detach the image's physical parent.
        if len(physical) == 1:
            device = physical[0]
        elif not physical and len(devices) == 1:
            device = devices[0]
        if device is None or {item["mount-point"] for item in entities if "mount-point" in item} != {str(mount)}:
            raise ValueError("signed image did not mount at its single private verification location")
        app = mount / (release.CHANNELS[source["channel"]][2] + ".app")
        if app.is_symlink() or not app.is_dir():
            raise ValueError("signed image lacks the exact channel app")
        identity(app, "app", deep=True)
        release.validate_app(app, source, source["channel"])
        release.command(["spctl", "--assess", "--type", "execute", app])
        inventory = {"files": [], "symlinks": []}
        for path in sorted(app.rglob("*")):
            name = path.relative_to(app).as_posix()
            if path.is_symlink():
                inventory["symlinks"].append({"path": name, "target": str(path.readlink())})
            elif path.is_file():
                inventory["files"].append({"path": name, "bytes": path.stat().st_size, "sha256": release.digest(path)})
        expected = release.exact_json((root / "final-signed-app-inventory.json").read_bytes())
        if inventory != expected:
            raise ValueError("mounted app differs from final signed inventory and corresponding source")
    finally:
        if device is not None:
            release.command(["hdiutil", "detach", device])
        elif mount.is_mount():
            release.command(["hdiutil", "detach", mount])
        shutil.rmtree(work)
    return {"packageSignatureVerified": True, "mountedAppSignatureVerified": True,
            "certificateSha1": release.CERTIFICATE_SHA1, "mountedInventoryMatched": True,
            "candidateArchitecture": release.CHANNELS[source["channel"]][0],
            "mountedImageDetached": True, "targetCodeExecuted": False}


def absent_release_and_tag(repository: str, tag: str, *, github=None) -> None:
    # Only an explicit 404 establishes absence; auth/outage errors never do.
    for path in (f"releases/tags/{tag}", f"git/ref/tags/{tag}"):
        if github is not None:
            if github.get_optional(f"repos/{repository}/{path}") is not None:
                raise ValueError("release/tag already exists; never overwrite immutable artifacts")
            continue
        request = urllib.request.Request(f"https://api.github.com/repos/{repository}/{path}", headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(request, timeout=60):
                pass
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            if code == 404:
                continue
            raise ValueError("release/tag absence could not be established") from None
        except OSError:
            raise ValueError("release/tag lookup unavailable") from None
        raise ValueError("release/tag already exists; never overwrite immutable artifacts")


def create_exact_tag(repository: str, tag: str, commit: str, *, github=None) -> None:
    if github is not None:
        created = github.request_json(f"repos/{repository}/git/refs", method="POST",
                                      payload={"ref": "refs/tags/" + tag, "sha": commit})
        validate_tag(created, tag, commit)
        return
    request = urllib.request.Request(f"https://api.github.com/repos/{repository}/git/refs", method="POST",
        data=json.dumps({"ref": "refs/tags/" + tag, "sha": commit}).encode(), headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"], "Accept": "application/vnd.github+json",
            "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 201:
                raise ValueError("atomic new tag creation not confirmed")
            data = response.read(65537)
            if len(data) > 65536:
                raise ValueError("atomic tag response exceeds bounds")
            created = release.exact_json(data)
    except urllib.error.HTTPError as error:
        error.close()
        raise ValueError("atomic tag creation rejected; never reuse or replace an existing ref") from None
    except OSError:
        raise ValueError("atomic tag outcome unknown; inspect exact ref before retry") from None
    validate_tag(created, tag, commit)


def validate_tag(ref: dict, tag: str, commit: str) -> None:
    target = ref.get("object", {})
    if ref.get("ref") != "refs/tags/" + tag or target.get("sha") != commit or target.get("type") != "commit":
        raise ValueError("tag does not resolve directly to the authorized immutable source commit")


def verify_exact_tag(repository: str, tag: str, commit: str, *, github=None) -> None:
    ref = (release.api(f"{repository}/git/ref/tags/{tag}") if github is None else
           github.request_json(f"repos/{repository}/git/ref/tags/{tag}"))
    validate_tag(ref, tag, commit)


def publish(source: dict, files: list[Path], root: Path, output: Path, repository: str, *, github=None) -> dict:
    names = feeds.names(source)
    tag = names["tag"]
    cli_repository = "github.com/" + repository
    validate_release_order(source, release_snapshot(repository, github=github))
    absent_release_and_tag(repository, tag, github=github)
    verify_previous_feed(source, root)
    create_exact_tag(repository, tag, source["sourceCommit"], github=github)
    verify_exact_tag(repository, tag, source["sourceCommit"], github=github)
    notes = root / "release-notes.txt"
    notes.write_text(f"AMPLIFai Phone {source['version']} ({source['channel']})\nReviewed source: {source['sourceCommit']}\n"
                     "Signed/notarized package and matching source. Physical acceptance and website promotion have separate receipts.\n")
    release.command(["gh", "release", "create", tag, "--repo", cli_repository, "--draft", "--verify-tag",
                     "--title", "AMPLIFai Phone " + source["version"], "--notes-file", notes])
    release.command(["gh", "release", "upload", tag, *files, "--repo", cli_repository])
    # Read uploaded draft bytes back before making the release public.
    downloaded = root / "uploaded-readback"
    downloaded.mkdir()
    assets = []
    for path in files:
        release.command(["gh", "release", "download", tag, "--repo", cli_repository, "--pattern", path.name, "--dir", downloaded])
        actual = downloaded / path.name
        if release.digest(actual) != release.digest(path) or actual.stat().st_size != path.stat().st_size:
            raise ValueError("draft uploaded bytes differ; draft remains unpublished")
        assets.append({"name": path.name, "bytes": path.stat().st_size, "sha256": release.digest(path)})
    validate_release_order(source, release_snapshot(repository, github=github), own_draft=tag)
    verify_previous_feed(source, root)
    verify_exact_tag(repository, tag, source["sourceCommit"], github=github)
    intel = source["channel"] == "macos-intel-test"
    release.command(["gh", "release", "edit", tag, "--repo", cli_repository, "--draft=false",
                     "--prerelease=" + str(intel).lower(), "--latest=" + str(not intel).lower()])
    prefix = f"https://github.com/{repository}/releases/download/{tag}/"
    for asset in assets:
        release.download(prefix + asset["name"], output / asset["name"], asset["sha256"], asset["bytes"])
    result = {"schema": 1, "status": "github-published-website-promotion-pending", "sourceCommit": source["sourceCommit"],
              "channel": source["channel"], "version": source["version"], "build": source["build"],
              "releaseUrl": f"https://github.com/{repository}/releases/tag/{tag}", "assets": assets,
              "websitePromoted": False, "physicalAcceptance": False,
              "atomicCrossReleaseCompareAndSwap": False,
              "promotionRequiresCurrentReleaseAndPreviousFeedRecheck": True}
    (output / "publication-receipt.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def website_handoff(source: dict, spec: dict, signed: dict, published: dict, env: dict | None = None,
                    *, local_provenance: dict | None = None) -> dict:
    if (env is None) == (local_provenance is None):
        raise ValueError("exactly one real hosted or local signing provenance required")
    if local_provenance is not None:
        request = signed.get("request", {})
        if (local_provenance.get("kind") != "local-keychain"
                or local_provenance != signed.get("localProvenance")
                or local_provenance.get("controlCommit") != signed.get("controlCommit")
                or local_provenance.get("operator") != signed.get("operator")
                or local_provenance.get("qualification") != {
                    "runId": request.get("runId"), "artifactId": request.get("artifactId"),
                    "sha256": request.get("artifactSha256"), "sourceCommit": source["sourceCommit"]}
                or any(key in local_provenance for key in ("signedArtifact", "runId", "attempt", "artifactId"))):
            raise ValueError("local handoff must preserve verified local and qualification provenance")
        control_commit = local_provenance["controlCommit"]
        execution = {"executionMode": "local-keychain", "localSigning": copy.deepcopy(local_provenance)}
    else:
        control_commit = env["GITHUB_SHA"]
        execution = {"signedArtifact": {"runId": int(env["GITHUB_RUN_ID"]), "attempt": int(env["GITHUB_RUN_ATTEMPT"]),
                                         "artifactId": int(env["SIGNED_ARTIFACT_ID"]), "sha256": env["SIGNED_ARTIFACT_SHA256"]}}
    channel = source["channel"]
    feed = signed["signing"]["feed"]
    rollback = feed["rollback"]
    repository = "JmooreOsirs/amplifai-phone-companion"
    tag = feeds.names(source)["tag"]
    prefix = f"https://github.com/{repository}/releases/download/{tag}/"
    assets = {row["name"]: row for row in published["assets"]}
    if local_provenance is not None:
        local_evidence = {}
        for field, name in (("manifest", "local-signing-manifest.json"),
                            ("signature", "local-signing-manifest.json.sig"),
                            ("delivery", "local-signed-delivery.zip")):
            asset = assets.get(name)
            if (not isinstance(asset, dict) or type(asset.get("bytes")) is not int or asset["bytes"] <= 0
                    or not re.fullmatch(r"[0-9a-f]{64}", asset.get("sha256", ""))):
                raise ValueError("published local provenance manifest and signature plus complete delivery required")
            local_evidence[field] = dict(asset, url=prefix + name)
        execution["localSigningEvidence"] = local_evidence
    feed_name = release.CHANNELS[channel][3]
    if feed_name not in assets or assets[feed_name]["sha256"] != feed["feedSha256"]:
        raise ValueError("published feed bytes differ from signer handoff")
    return {"schema": 1, "status": "website-owner-integration-required", "sourceCommit": source["sourceCommit"],
            "controlCommit": control_commit, "channel": channel, "version": source["version"], "build": source["build"],
            "architecture": release.CHANNELS[channel][0], "bundleId": release.CHANNELS[channel][1],
            "releaseUrl": published["releaseUrl"],
            **execution,
            "feed": {"path": "public/phone/macos/" + feed_name, "url": prefix + feed_name,
                     "bytes": feed["feedBytes"], "sha256": feed["feedSha256"], "signature": "embedded-sparkle-xml-comment"},
            "package": dict(spec["package"], url=spec["installerUrl"]),
            "source": dict(spec["source"], url=prefix + spec["source"]["name"]),
            "expectedPreviousFeed": {"path": "public/phone/macos/" + feed_name, "bytes": rollback["bytes"],
                                     "sha256": rollback["sha256"], "rollbackArtifactPath": "feed/rollback/" + rollback["name"]},
            "requiredWebsiteChanges": ["public/phone/macos/" + feed_name, "src/lib/release/phone-companion.ts",
                "tests/macos-arm-update-feed.test.ts", "tests/macos-intel-update-feed.test.ts",
                "tests/native-download-metadata.test.ts", "tests/companion-update-status-ui.test.ts",
                "src/lib/telemetry/events.ts", "src/app/api/v1/companion-diagnostics/route.ts",
                "tests/telemetry.test.ts", "tests/companion-diagnostics-route.test.ts"],
            "publicReadbackOrigins": ["https://amplifai.satoris.ai", "https://amplifai-database-engine.vercel.app"],
            "requiredLiveChecks": ["candidate is still newest published channel build", "current feed equals expectedPreviousFeed",
                                   "deployment promotion retains expected previous state; reject stale/concurrent promotion"],
            "websitePublisherImplemented": False, "websitePromoted": False, "nativeHasWebsiteCredentials": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy = release.exact_json((release.ROOT / "release_control/policy.json").read_bytes())
    if policy.get("publicationEnabled") is not True:
        parser.error("public publication remains inactive")
    release.validate_context(policy, os.environ)
    if any(os.environ.get(key) for key in ("APPLE_CERTIFICATE_P12_BASE64", "SPARKLE_PRIVATE_KEY", "NOTARY_APP_PASSWORD")):
        parser.error("publication job must not have signing credentials")
    request = {"sourceCommit": os.environ["SOURCE_COMMIT"], "runId": int(os.environ["BUILD_RUN_ID"]),
               "artifactId": int(os.environ["BUILD_ARTIFACT_ID"]), "artifactSha256": os.environ["ARTIFACT_SHA256"],
               "channel": os.environ["RELEASE_CHANNEL"]}
    _, source = release.inspected(policy, request, signed=True)
    publication_policy = copy.deepcopy(policy)
    publication_policy["signingEnvironment"] = policy["publicationEnvironment"]
    repo = policy["repository"]
    environment = release.api(f"{repo}/environments/{policy['publicationEnvironment']}")
    branches = release.api(f"{repo}/environments/{policy['publicationEnvironment']}/deployment-branch-policies")
    control = release.api(f"{repo}/branches/{policy['controlRef'].removeprefix('refs/heads/')}")
    release.validate_protection(publication_policy, environment, branches, control, os.environ)
    artifact = release.api(f"{repo}/actions/artifacts/{os.environ['SIGNED_ARTIFACT_ID']}")
    jobs = release.api(f"{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}/attempts/{os.environ['GITHUB_RUN_ATTEMPT']}/jobs")
    validate_signer_provenance(artifact, jobs, os.environ)
    args.output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="amplifai-publication-", dir=os.environ["RUNNER_TEMP"]) as folder:
        stage = Path(folder)
        archive = stage / "signed.zip"
        release.download(f"https://api.github.com/repos/{repo}/actions/artifacts/{os.environ['SIGNED_ARTIFACT_ID']}/zip", archive,
                         os.environ["SIGNED_ARTIFACT_SHA256"], artifact["size_in_bytes"], authenticated=True)
        delivery = stage / "delivery"
        delivery.mkdir()
        extract_delivery(archive, delivery)
        spec, files = validate_delivery(delivery, source, request, os.environ)
        published = publish(source, files, delivery, args.output, repo)
        handoff = website_handoff(source, spec, release.exact_json((delivery / "release-receipt.json").read_bytes()), published, os.environ)
        (args.output / "website-handoff.json").write_text(json.dumps(handoff, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
