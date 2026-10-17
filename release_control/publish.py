"""Isolated immutable GitHub publication; never write website feeds or use keys."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path, PurePosixPath
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


def release_snapshot(repository: str) -> list:
    result = []
    for page in range(1, 11):
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


def validate_delivery(root: Path, source: dict, request: dict, env: dict) -> tuple[dict, list[Path]]:
    receipt = release.exact_json((root / "release-receipt.json").read_bytes())
    signed = receipt.get("signing", {})
    if (receipt.get("request") != request or receipt.get("controlCommit") != env["GITHUB_SHA"]
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


def absent_release_and_tag(repository: str, tag: str) -> None:
    # Only an explicit 404 establishes absence; auth/outage errors never do.
    for path in (f"releases/tags/{tag}", f"git/ref/tags/{tag}"):
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


def create_exact_tag(repository: str, tag: str, commit: str) -> None:
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


def verify_exact_tag(repository: str, tag: str, commit: str) -> None:
    validate_tag(release.api(f"{repository}/git/ref/tags/{tag}"), tag, commit)


def publish(source: dict, files: list[Path], root: Path, output: Path, repository: str) -> dict:
    names = feeds.names(source)
    tag = names["tag"]
    validate_release_order(source, release_snapshot(repository))
    absent_release_and_tag(repository, tag)
    verify_previous_feed(source, root)
    create_exact_tag(repository, tag, source["sourceCommit"])
    verify_exact_tag(repository, tag, source["sourceCommit"])
    notes = root / "release-notes.txt"
    notes.write_text(f"AMPLIFai Phone {source['version']} ({source['channel']})\nReviewed source: {source['sourceCommit']}\n"
                     "Signed/notarized package and matching source. Physical acceptance and website promotion have separate receipts.\n")
    release.command(["gh", "release", "create", tag, "--repo", repository, "--draft", "--verify-tag",
                     "--title", "AMPLIFai Phone " + source["version"], "--notes-file", notes])
    release.command(["gh", "release", "upload", tag, *files, "--repo", repository])
    # Read uploaded draft bytes back before making the release public.
    downloaded = root / "uploaded-readback"
    downloaded.mkdir()
    assets = []
    for path in files:
        release.command(["gh", "release", "download", tag, "--repo", repository, "--pattern", path.name, "--dir", downloaded])
        actual = downloaded / path.name
        if release.digest(actual) != release.digest(path) or actual.stat().st_size != path.stat().st_size:
            raise ValueError("draft uploaded bytes differ; draft remains unpublished")
        assets.append({"name": path.name, "bytes": path.stat().st_size, "sha256": release.digest(path)})
    validate_release_order(source, release_snapshot(repository), own_draft=tag)
    verify_previous_feed(source, root)
    verify_exact_tag(repository, tag, source["sourceCommit"])
    intel = source["channel"] == "macos-intel-test"
    release.command(["gh", "release", "edit", tag, "--repo", repository, "--draft=false",
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


def website_handoff(source: dict, spec: dict, signed: dict, published: dict, env: dict) -> dict:
    channel = source["channel"]
    feed = signed["signing"]["feed"]
    rollback = feed["rollback"]
    repository = "JmooreOsirs/amplifai-phone-companion"
    tag = feeds.names(source)["tag"]
    prefix = f"https://github.com/{repository}/releases/download/{tag}/"
    assets = {row["name"]: row for row in published["assets"]}
    feed_name = release.CHANNELS[channel][3]
    if feed_name not in assets or assets[feed_name]["sha256"] != feed["feedSha256"]:
        raise ValueError("published feed bytes differ from signer handoff")
    return {"schema": 1, "status": "website-owner-integration-required", "sourceCommit": source["sourceCommit"],
            "controlCommit": env["GITHUB_SHA"], "channel": channel, "version": source["version"], "build": source["build"],
            "architecture": release.CHANNELS[channel][0], "bundleId": release.CHANNELS[channel][1],
            "releaseUrl": published["releaseUrl"],
            "signedArtifact": {"runId": int(env["GITHUB_RUN_ID"]), "attempt": int(env["GITHUB_RUN_ATTEMPT"]),
                               "artifactId": int(env["SIGNED_ARTIFACT_ID"]), "sha256": env["SIGNED_ARTIFACT_SHA256"]},
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
