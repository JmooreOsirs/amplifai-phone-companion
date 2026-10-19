"""Inspect immutable Mac build artifacts; sign only reviewed, protected candidates.

Only trusted release-control code runs in this process. Candidate executables,
source archives and build scripts are data and are never launched.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import urllib.error
from urllib.parse import quote
import zipfile
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_KEY = "vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs="
IDENTITY = "Developer ID Application: Jeff Moore (WSBR54954W)"
TEAM = "WSBR54954W"
CERTIFICATE_SHA1 = "59300C81CAA8FEDF07F60970E4621524243F83F4"
SPARKLE_ACCOUNT = "ai.satoris.amplifai.phone.candidate"
NOTARY_PROFILE = "AMPLIFai-notary"
RELEASE_OPERATORS = ["JmooreOsirs", "bac1876"]
GITHUB_ACTIONS_APP_ID = 15368
CHANNELS = {
    "macos-arm64": ("arm64", "ai.satoris.amplifai.phone.candidate", "AMPLIFai Phone", "appcast.xml"),
    "macos-intel-test": ("x86_64", "ai.satoris.amplifai.phone.intel-candidate", "AMPLIFai Phone Intel Test", "intel-appcast.xml"),
}
SPARKLE_URL = "https://github.com/sparkle-project/Sparkle/releases/download/2.10.0/Sparkle-2.10.0.tar.xz"
SPARKLE_SHA256 = "c2bf58aa8387266ac179357b1415d6f2635f044da8be41042af32425dae6da0c"
SIGN_UPDATE_SHA256 = "43c249771bafc3aa581228abae00731a012d324691b8292860896635050be76b"
MAX_ARTIFACT = 2 * 1024**3
MAX_APP = 512 * 1024**2
PHONE_RUN_QUALIFICATION = "phone-run-recovery-v1"
# These are the only published sources whose immutable producer predates profiles.
LEGACY_QUALIFICATIONS = frozenset({
    ("8f7ec4d3de4235ab5d36d5175c29121e3a26548a", "macos-arm64", "2026.10.16-rc28", "26101628"),
    ("8f7ec4d3de4235ab5d36d5175c29121e3a26548a", "macos-intel-test", "2026.10.16-rc28-intel1", "261016281"),
})
BASE_QUALIFICATION_CHECKS = ("functional", "architecture", "frozenHelper", "sourceClosure", "legal")
PHONE_RUN_CHECKS = ("collectorModel", "windowLifecycle", "bridgeRunTiming", "bridgeRecovery")


def qualification_profile(source: dict) -> str | None:
    identity = tuple(source.get(key) for key in ("sourceCommit", "channel", "version", "build"))
    if "qualificationProfile" not in source and all(isinstance(value, str) for value in identity) and identity in LEGACY_QUALIFICATIONS:
        return None
    if source.get("qualificationProfile") != PHONE_RUN_QUALIFICATION:
        raise ValueError("new source requires the reviewed phone-run-recovery-v1 qualification profile")
    return PHONE_RUN_QUALIFICATION


def qualification_checks(source: dict) -> dict:
    extra = PHONE_RUN_CHECKS if qualification_profile(source) else ()
    return dict.fromkeys(BASE_QUALIFICATION_CHECKS + extra, True)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def exact_json(data: bytes, *, allow_list: bool = False) -> dict | list:
    def unique(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result
    result = json.loads(data, object_pairs_hook=unique,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    if not isinstance(result, dict) and not (allow_list and isinstance(result, list)):
        raise ValueError("JSON object required")
    return result


def command(args: list, *, input_data: bytes | None = None, timeout: int = 1800) -> str:
    # Commands involving credentials must never appear in logs or exceptions.
    try:
        result = subprocess.run(list(map(str, args)), input=input_data,
                                capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError(f"{Path(args[0]).name} unavailable or timed out; credential arguments suppressed") from None
    if result.returncode:
        raise ValueError(f"{Path(args[0]).name} failed (exit {result.returncode}); no credential output retained")
    return result.stdout.decode().strip()


def api(path: str, *, audit: bool = False, allow_list: bool = False) -> dict | list:
    token = os.environ.get("GH_POLICY_AUDIT_TOKEN" if audit else "GH_TOKEN")
    if not token:
        raise ValueError("read-only repository Administration audit credential required")
    request = urllib.request.Request("https://api.github.com/repos/" + path, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        error.close()
        raise ValueError("GitHub protection/provenance metadata unavailable") from None
    except (OSError, urllib.error.URLError):
        raise ValueError("GitHub protection/provenance metadata unavailable") from None
    if len(data) > 1024 * 1024:
        raise ValueError("oversized GitHub metadata")
    return exact_json(data, allow_list=allow_list)


def validate_release_authority(policy: dict) -> None:
    if (policy.get("releaseAuthority") != "owner-approved-source-release"
            or policy.get("operators") != RELEASE_OPERATORS
            or policy.get("requiredChecks") != ["release-control-checks"]
            or type(policy.get("requiredCheckAppId")) is not int
            or policy["requiredCheckAppId"] != GITHUB_ACTIONS_APP_ID
            or policy.get("codeOwners") != "* @JmooreOsirs @bac1876\n"):
        raise ValueError("exact Jeff-or-Brian release authority and pinned GitHub Actions checks required")


def validate_context(policy: dict, env: dict) -> None:
    validate_release_authority(policy)
    if (env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
            or env.get("GITHUB_REPOSITORY") != policy["repository"]
            or env.get("GITHUB_ACTOR") not in policy["operators"]
            or env.get("GITHUB_TRIGGERING_ACTOR") != env.get("GITHUB_ACTOR")
            or env.get("GITHUB_REF") != policy["controlRef"]
            or env.get("GITHUB_WORKFLOW_REF") != policy["repository"] + "/.github/workflows/macos-protected-release.yml@" + policy["controlRef"]):
        raise ValueError("dispatch must use canonical control ref and original allowed operator")


def validate_provenance(policy: dict, request: dict, run: dict, artifact: dict, comparison: dict) -> None:
    if (run.get("id") != request["runId"] or artifact.get("id") != request["artifactId"]
            or run.get("head_sha") != request["sourceCommit"]
            or run.get("head_branch") != policy["sourceRef"].removeprefix("refs/heads/")
            or run.get("head_repository", {}).get("full_name") != policy["repository"]
            or run.get("repository", {}).get("full_name") != policy["repository"]
            or run.get("path") not in policy["buildWorkflows"]
            or run.get("event") != "workflow_dispatch"
            or run.get("status") != "completed" or run.get("conclusion") != "success"
            or run.get("actor", {}).get("login") not in policy["operators"]
            or run.get("triggering_actor", {}).get("login") != run.get("actor", {}).get("login")
            or artifact.get("workflow_run", {}).get("id") != request["runId"]
            or artifact.get("workflow_run", {}).get("head_sha") != request["sourceCommit"]
            or artifact.get("expired") is not False
            or type(artifact.get("size_in_bytes")) is not int
            or not 0 < artifact["size_in_bytes"] <= MAX_ARTIFACT
            or artifact.get("digest") != "sha256:" + request["artifactSha256"]
            or comparison.get("status") not in {"ahead", "identical"}
            or comparison.get("merge_base_commit", {}).get("sha") != policy["qualifiedAncestor"]):
        raise ValueError("artifact is not a successful immutable build from the qualified native lineage")


def validate_protection(policy: dict, environment: dict, branches: dict, protected: dict, env: dict) -> None:
    validate_release_authority(policy)
    rules = environment.get("protection_rules", [])
    branch_names = {item.get("name") for item in branches.get("branch_policies", [])
                    if item.get("type", "branch") == "branch"}
    if (env.get("GITHUB_REF_PROTECTED") != "true" or not protected.get("protected")
            or environment.get("name") != policy["signingEnvironment"]
            or environment.get("deployment_branch_policy") != {
                "protected_branches": False, "custom_branch_policies": True}
            or branch_names != {policy["controlRef"].removeprefix("refs/heads/")}
            or len(branches.get("branch_policies", [])) != 1
            or any(rule.get("type") != "branch_policy" for rule in rules)):
        raise ValueError("exact protected control branch and no per-run approval rules required")


def validate_branch_custody(policy: dict, protection: dict, owners: str) -> None:
    validate_release_authority(policy)
    reviews = protection.get("required_pull_request_reviews", {})
    if not isinstance(reviews, dict):
        raise ValueError("pull request requirement must remain enabled")
    bypass = reviews.get("bypass_pull_request_allowances", {})
    checks = protection.get("required_status_checks", {})
    if not isinstance(checks, dict):
        raise ValueError("strict pinned required checks unavailable")
    check_rows = checks.get("checks", [])
    pinned_checks = (isinstance(check_rows, list)
                     and all(isinstance(item, dict) for item in check_rows)
                     and all(any(item.get("context") == name
                                 and type(item.get("app_id")) is int
                                 and item["app_id"] == policy["requiredCheckAppId"]
                                 for item in check_rows) for name in policy["requiredChecks"])
                     and all(item.get("app_id") == policy["requiredCheckAppId"]
                             for item in check_rows if item.get("context") in policy["requiredChecks"]))
    if (protection.get("enforce_admins", {}).get("enabled") is not True
            or reviews.get("dismiss_stale_reviews") is not True
            or reviews.get("require_code_owner_reviews") is not False
            or reviews.get("require_last_push_approval") is not False
            or type(reviews.get("required_approving_review_count")) is not int
            or reviews["required_approving_review_count"] != 0
            or not isinstance(bypass, dict)
            or set(bypass) - {"users", "teams", "apps"}
            or any(bypass.get(kind, []) != [] for kind in ("users", "teams", "apps"))
            or protection.get("allow_force_pushes", {}).get("enabled") is not False
            or protection.get("allow_deletions", {}).get("enabled") is not False
            or checks.get("strict") is not True
            or not pinned_checks
            or owners != policy["codeOwners"]):
        raise ValueError("branch custody requires owner-either PR policy, pinned checks, admin enforcement and zero PR bypass grants")


def validate_approved_source(policy: dict, request: dict, run: dict) -> dict:
    approved = next((item for item in policy["approvedSources"]
                     if item.get("sourceCommit") == request["sourceCommit"]
                     and item.get("channel") == request["channel"]), None)
    if (policy.get("activationApproved") is not True or approved is None
            or approved.get("releaseAuthorized") is not True
            or approved.get("buildWorkflow") != run.get("path")):
        raise ValueError("signing inactive or source/workflow not authorized in reviewed control policy")
    if (not isinstance(approved.get("build"), str) or not re.fullmatch(r"[0-9]{1,12}", approved["build"])
            or int(approved["build"]) <= int(policy["releasedBuilds"][request["channel"]])
            or not isinstance(approved.get("version"), str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,63}", approved["version"])):
        raise ValueError("candidate must be newer than the published channel build")
    qualification_profile(approved)
    return approved


def inspected(policy: dict, request: dict, *, signed: bool, custody: bool = True) -> tuple[dict, dict | None]:
    validate_context(policy, os.environ)
    repo = policy["repository"]
    run = api(f"{repo}/actions/runs/{request['runId']}")
    artifact = api(f"{repo}/actions/artifacts/{request['artifactId']}")
    comparison = api(f"{repo}/compare/{policy['qualifiedAncestor']}...{request['sourceCommit']}")
    validate_provenance(policy, request, run, artifact, comparison)
    approved = next((item for item in policy["approvedSources"]
                     if item.get("sourceCommit") == request["sourceCommit"]
                     and item.get("channel") == request["channel"]), None)
    if signed:
        approved = validate_approved_source(policy, request, run)
        environment = api(f"{repo}/environments/{policy['signingEnvironment']}")
        branches = api(f"{repo}/environments/{policy['signingEnvironment']}/deployment-branch-policies")
        protected = api(f"{repo}/branches/{policy['controlRef'].removeprefix('refs/heads/')}")
        validate_protection(policy, environment, branches, protected, os.environ)
        source_branch = api(f"{repo}/branches/{policy['sourceRef'].removeprefix('refs/heads/')}")
        if source_branch.get("protected") is not True:
            raise ValueError("native source ref must also be protected")
        if custody:
            import authority

            def metadata(path):
                return api(path.removeprefix("repos/"), allow_list=True)

            authority.require_approved_merge(metadata, policy, os.environ["GITHUB_SHA"], "main")
            authority.require_approved_merge(metadata, policy, request["sourceCommit"], "release/native")
            for ref in (policy["controlRef"], policy["sourceRef"]):
                branch = quote(ref.removeprefix("refs/heads/"), safe="")
                protection = api(f"{repo}/branches/{branch}/protection", audit=True)
                owners = api(f"{repo}/contents/.github/CODEOWNERS?ref={quote(ref, safe='')}")
                if owners.get("encoding") != "base64":
                    raise ValueError("readable effective CODEOWNERS required")
                validate_branch_custody(policy, protection, base64.b64decode(owners["content"]).decode())
    return {"schema": 1, "request": request, "artifactBytes": artifact["size_in_bytes"],
            "artifactName": artifact["name"], "controlCommit": os.environ["GITHUB_SHA"],
            "operator": os.environ["GITHUB_ACTOR"], "signingApproved": signed and custody,
            "published": False, "physicalAcceptance": False}, approved


def validate_delivery_record(record: dict, source: dict, request: dict) -> dict:
    """Derive artifact eligibility from trusted CI, without a run-specific PR."""
    profile = qualification_profile(source)
    fields = {"schema", "sourceCommit", "channel", "version", "build", "buildRunId",
              "checks", "privateDataUsed", "appArchive", "sourceArchive"}
    if profile:
        fields.add("qualificationProfile")
    if (set(record) != fields or type(record.get("schema")) is not int or record["schema"] != (2 if profile else 1)
            or (profile and record.get("qualificationProfile") != profile)
            or any(record.get(key) != source[key] for key in ("sourceCommit", "channel", "version", "build"))
            or type(record.get("buildRunId")) is not int or record["buildRunId"] != request["runId"]
            or record.get("privateDataUsed") is not False
            or record.get("checks") != qualification_checks(source)):
        raise ValueError("trusted build did not qualify this exact reviewed source and run")
    if any(type(value) is not bool for value in record["checks"].values()):
        raise ValueError("qualification checks must be measured boolean results")
    for key in ("appArchive", "sourceArchive"):
        asset = record[key]
        if (not isinstance(asset, dict) or set(asset) != {"name", "bytes", "sha256"}
                or not isinstance(asset["name"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.zip", asset["name"])
                or type(asset["bytes"]) is not int or not 0 < asset["bytes"] <= MAX_ARTIFACT
                or not isinstance(asset["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])):
            raise ValueError("invalid qualified asset record")
    if len({record["appArchive"]["name"], record["sourceArchive"]["name"], "qualification.json"}) != 3:
        raise ValueError("qualified asset names must be distinct")
    return record


def download(url: str, destination: Path, expected_sha: str, limit: int, *, authenticated: bool = False) -> None:
    headers = {"Authorization": "Bearer " + os.environ["GH_TOKEN"]} if authenticated else {}
    # Follow GitHub's artifact redirect without forwarding the API token.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    request = urllib.request.Request(url, headers=headers)
    try:
        response = opener.open(request, timeout=60)
    except urllib.error.HTTPError as error:
        code, location = error.code, error.headers.get("Location", "")
        error.close()
        if code not in {302, 303, 307, 308}:
            raise ValueError("artifact download request failed") from None
        if not location.startswith("https://"):
            raise ValueError("artifact redirect must use HTTPS")
        try:
            response = urllib.request.urlopen(location, timeout=60)
        except (OSError, urllib.error.URLError):
            raise ValueError("artifact body unavailable") from None
    except (OSError, urllib.error.URLError):
        raise ValueError("download unavailable") from None
    if not response.geturl().startswith("https://"):
        response.close()
        raise ValueError("download must remain on HTTPS")
    count = 0
    with response, destination.open("xb") as target:
        while block := response.read(1024 * 1024):
            count += len(block)
            if count > limit:
                raise ValueError("download exceeds approved byte limit")
            target.write(block)
    if digest(destination) != expected_sha:
        raise ValueError("downloaded bytes differ from approved digest")


def unpack_app(archive: Path, output: Path, app_name: str) -> Path:
    with zipfile.ZipFile(archive) as zipped:
        members = zipped.infolist()
        if len(members) > 30000 or sum(item.file_size for item in members) > MAX_APP:
            raise ValueError("app archive exceeds extraction bounds")
        seen, links = set(), []
        for item in members:
            name = PurePosixPath(item.filename)
            if (name.is_absolute() or ".." in name.parts or "\\" in item.filename or "\0" in item.filename
                    or not name.parts or name.parts[0] != app_name + ".app"
                    or name.as_posix() in seen):
                raise ValueError("unsafe or duplicate app archive path")
            seen.add(name.as_posix())
            path = output.joinpath(*name.parts)
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                if item.file_size > 4096:
                    raise ValueError("oversized symbolic link")
                links.append((path, zipped.read(item).decode()))
            elif item.is_dir():
                path.mkdir(parents=True, exist_ok=True)
            elif stat.S_IFMT(mode) in {0, stat.S_IFREG}:
                path.parent.mkdir(parents=True, exist_ok=True)
                with zipped.open(item) as source, path.open("xb") as target:
                    shutil.copyfileobj(source, target)
                path.chmod(0o755 if mode & 0o111 else 0o644)
            else:
                raise ValueError("unsupported archive member")
        for path, target in links:
            resolved = (path.parent / target).resolve()
            if Path(target).is_absolute() or not resolved.is_relative_to((output / (app_name + ".app")).resolve()):
                raise ValueError("app link escapes bundle")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
    return output / (app_name + ".app")


def validate_app(app: Path, candidate: dict, channel: str) -> None:
    arch, bundle, name, feed = CHANNELS[channel]
    info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    expected = {"CFBundleIdentifier": bundle, "CFBundleName": name,
                "CFBundleVersion": candidate["build"], "CFBundleShortVersionString": candidate["version"],
                "SUPublicEDKey": PUBLIC_KEY, "SUFeedURL": "https://amplifai-database-engine.vercel.app/phone/macos/" + feed,
                "SURequireSignedFeed": True, "SUVerifyUpdateBeforeExtraction": True,
                "SUSignedFeedFailureExpirationInterval": 0, "LSMinimumSystemVersion": "14.0"}
    if any(type(info.get(key)) is not type(value) or info.get(key) != value for key, value in expected.items()):
        raise ValueError("candidate bundle, version, CPU channel or update trust differs")
    for name in ("LICENSE", "COPYRIGHT", "SOURCE-README.md", "THIRD-PARTY-NOTICES.txt", "source-package-file-manifest.json"):
        path = app / "Contents/Resources/Legal" / name
        if path.is_symlink() or not path.is_file() or not path.stat().st_size:
            raise ValueError("qualified in-app legal/source offer missing")
    sys.path.insert(0, str(ROOT / "source/script"))
    from verify_macos_architecture import audit
    if not audit(app, arch, "14.0")["passed"]:
        raise ValueError("native architecture/dependency closure failed")
    for relative in ("Contents/MacOS/AmplifaiPhone", "Contents/Resources/helper/amplifai-agent"):
        if command(["/usr/bin/lipo", "-archs", app / relative]).split() != [arch]:
            raise ValueError("main/helper CPU differs from exact channel")


def notarize(path: Path, keychain: Path) -> None:
    response = exact_json(command(["xcrun", "notarytool", "submit", path, "--keychain-profile", "amplifai-ci",
                                   "--keychain", keychain, "--wait", "--output-format", "json"]).encode())
    if response.get("status") != "Accepted":
        raise ValueError("notarization not accepted")


def sign_and_verify(path: Path, signer: Path, openssl: str, private: Path, *,
                    keychain_signer: Callable[[Path], str] | None = None) -> str:
    signature = (keychain_signer(path) if keychain_signer is not None else
                 command([signer, "--ed-key-file", "-", "-p", path],
                         input_data=os.environ["SPARKLE_PRIVATE_KEY"].encode()))
    public = private / "public.der"
    public.write_bytes(bytes.fromhex("302a300506032b6570032100") + base64.b64decode(PUBLIC_KEY))
    sig = private / "signature"
    sig.write_bytes(base64.b64decode(signature, validate=True))
    if sig.stat().st_size != 64:
        raise ValueError("invalid Sparkle signature")
    command([openssl, "pkeyutl", "-verify", "-rawin", "-pubin", "-keyform", "DER", "-inkey", public,
             "-in", path, "-sigfile", sig])
    return signature


def _sign_package(app: Path, source: Path, output: Path, signer: Path, source_record: dict,
                  openssl: str, private: Path, keychain: Path | None, *,
                  keychain_signer: Callable[[Path], str] | None = None) -> dict:
    codesign = "/usr/bin/codesign" if keychain is None else "codesign"
    identity = CERTIFICATE_SHA1 if keychain is None else IDENTITY
    keychain_args = [] if keychain is None else ["--keychain", keychain]
    architecture_args = ["--all-architectures"] if keychain is None else []
    sign_timeout = 60 if keychain is None else 1800
    import producer
    producer.validate_signing_input(source, app, source_record["sourceCommit"], source_record["channel"])
    from verify_macos_architecture import MAGICS
    nodes = []
    for path in app.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_file():
            with path.open("rb") as stream:
                if stream.read(4) in MAGICS:
                    nodes.append(path)
        elif path.suffix in {".app", ".framework", ".xpc"}:
            nodes.append(path)
    for path in sorted(nodes, key=lambda node: len(node.parts), reverse=True) + [app]:
        command([codesign, "--force", "--sign", identity, *keychain_args,
                 "--timestamp", "--options", "runtime", path], timeout=sign_timeout)
    command([codesign, "--verify", "--deep", "--strict", *architecture_args, app])
    zipped = private / "notary.zip"
    command(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", app, zipped])
    app_notarization = notarize_local(zipped) if keychain is None else notarize(zipped, keychain)
    command(["xcrun", "stapler", "staple", app])
    command(["xcrun", "stapler", "validate", app])
    command(["spctl", "--assess", "--type", "execute", app])
    image_root = private / "image"
    image_root.mkdir()
    command(["ditto", app, image_root / app.name])
    (image_root / "Applications").symlink_to("/Applications")
    import feeds
    import finalize
    names = feeds.names(source_record)
    dmg = output / names["package"]
    command(["hdiutil", "create", "-volname", app.stem, "-srcfolder", image_root, "-format", "UDZO", dmg])
    command([codesign, "--sign", identity, *keychain_args, "--timestamp", dmg], timeout=sign_timeout)
    dmg_notarization = notarize_local(dmg) if keychain is None else notarize(dmg, keychain)
    command(["xcrun", "stapler", "staple", dmg])
    command(["xcrun", "stapler", "validate", dmg])
    command([codesign, "--verify", "--strict", *architecture_args, dmg])
    command(["spctl", "--assess", "--type", "open", "--context", "context:primary-signature", dmg])
    if keychain is not None:
        command(["security", "delete-keychain", keychain])
    signature = sign_and_verify(dmg, signer, openssl, private, keychain_signer=keychain_signer)
    (output / (dmg.name + ".sig")).write_text(signature + "\n")
    inventory = {"files": [], "symlinks": []}
    for path in sorted(app.rglob("*")):
        relative = path.relative_to(app).as_posix()
        if path.is_symlink():
            inventory["symlinks"].append({"path": relative, "target": str(path.readlink())})
        elif path.is_file():
            inventory["files"].append({"path": relative, "bytes": path.stat().st_size, "sha256": digest(path)})
    inventory_path = output / "final-signed-app-inventory.json"
    inventory_path.write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
    source_zip = output / names["source"]
    finalized = finalize.finalize_source(source, source_zip, inventory_path, source_record["sourceCommit"])
    feed = feeds.prepare(source_record, dmg, source_zip, output / "feed", signer.parent.parent, openssl,
                         keychain_signer=keychain_signer)
    receipt = {"packageSha256": digest(dmg), "packageBytes": dmg.stat().st_size,
               "sourceSha256": digest(source_zip), "developerId": IDENTITY, "sparklePublicKey": PUBLIC_KEY,
               "finalSignedAppInventorySha256": digest(inventory_path), "sourceFinalizationRequired": False,
               "sourceFinalization": finalized, "feed": feed,
               "notarized": True, "published": False, "feedPrepared": True}
    if keychain is None:
        receipt.update(signingMode="local-keychain", appNotarization=app_notarization,
                       dmgNotarization=dmg_notarization,
                       signingIdentity={"developerId": IDENTITY, "certificateSha1": CERTIFICATE_SHA1,
                                        "teamId": TEAM, "sparklePublicKey": PUBLIC_KEY,
                                        "sparkleAccount": SPARKLE_ACCOUNT, "notaryProfile": NOTARY_PROFILE})
    return receipt


def signing_openssl() -> str:
    openssl = next((path for path in ("/opt/homebrew/opt/openssl@3/bin/openssl", "/usr/local/opt/openssl@3/bin/openssl")
                    if Path(path).is_file()), None)
    if openssl is None:
        raise ValueError("existing Ed25519 OpenSSL required")
    return openssl


def sign_candidate(app: Path, source: Path, output: Path, signer: Path, source_record: dict) -> dict:
    credential_names = ("APPLE_CERTIFICATE_P12_BASE64", "APPLE_CERTIFICATE_PASSWORD", "NOTARY_APPLE_ID",
                        "NOTARY_APP_PASSWORD", "SPARKLE_PRIVATE_KEY")
    if any(not os.environ.get(name) for name in credential_names):
        raise ValueError("existing authorized signing/notary credential configuration incomplete")
    openssl = signing_openssl()
    if digest(signer) != SIGN_UPDATE_SHA256:
        raise ValueError("pinned signer and existing Ed25519 OpenSSL required")
    with tempfile.TemporaryDirectory(prefix="amplifai-ci-keys-", dir=os.environ["RUNNER_TEMP"]) as folder:
        private = Path(folder)
        private.chmod(0o700)
        keychain = private / "release.keychain-db"
        password = secrets.token_urlsafe(40)
        certificate = private / "certificate.p12"
        certificate.write_bytes(base64.b64decode(os.environ["APPLE_CERTIFICATE_P12_BASE64"], validate=True))
        certificate.chmod(0o600)
        try:
            probe = private / "key-continuity-probe"
            probe.write_bytes(b"AMPLIFai protected-release key-continuity probe\n")
            sign_and_verify(probe, signer, openssl, private)
            command(["security", "create-keychain", "-p", password, keychain])
            command(["security", "set-keychain-settings", "-lut", "3600", keychain])
            command(["security", "unlock-keychain", "-p", password, keychain])
            command(["security", "import", certificate, "-P", os.environ["APPLE_CERTIFICATE_PASSWORD"],
                     "-k", keychain, "-T", "/usr/bin/codesign"])
            certificate.unlink()
            command(["security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:", "-s", "-k", password, keychain])
            identities = command(["security", "find-identity", "-v", "-p", "codesigning", keychain])
            if not re.search(r"\b" + CERTIFICATE_SHA1 + r'\s+"' + re.escape(IDENTITY) + r'"', identities):
                raise ValueError("imported existing Developer ID identity differs")
            command(["xcrun", "notarytool", "store-credentials", "amplifai-ci", "--keychain", keychain,
                     "--apple-id", os.environ["NOTARY_APPLE_ID"], "--password", os.environ["NOTARY_APP_PASSWORD"], "--team-id", TEAM])
            return _sign_package(app, source, output, signer, source_record, openssl, private, keychain)
        finally:
            # Failure must propagate if deletion fails; never silently retain keys.
            if keychain.exists():
                command(["security", "delete-keychain", keychain])


class KeychainSparkleSigner:
    """Use the existing account without exporting or reading its private key."""
    def __init__(self, signer: Path):
        if signer.is_symlink() or digest(signer) != SIGN_UPDATE_SHA256:
            raise ValueError("pinned Sparkle signer required")
        self.signer = signer

    def __call__(self, path: Path) -> str:
        return command([self.signer, "--account", SPARKLE_ACCOUNT,
                        "--disable-signing-warning", "-p", path], timeout=60)


def sign_local_and_verify(path: Path, signer: Path, openssl: str, private_root: Path) -> str:
    keychain_signer = KeychainSparkleSigner(signer)
    with tempfile.TemporaryDirectory(prefix="amplifai-local-signature-", dir=private_root) as folder:
        private = Path(folder)
        private.chmod(0o700)
        return sign_and_verify(path, signer, openssl, private, keychain_signer=keychain_signer)


def notarize_local(path: Path) -> dict:
    response = exact_json(command(["/usr/bin/xcrun", "notarytool", "submit", path,
                                   "--keychain-profile", NOTARY_PROFILE,
                                   "--wait", "--output-format", "json"]).encode())
    if (response.get("status") != "Accepted" or not isinstance(response.get("id"), str)
            or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", response["id"])):
        raise ValueError("local notarization not accepted or submission ID missing")
    return {"id": response["id"], "status": response["status"]}


def sign_local_candidate(app: Path, source_zip: Path, output: Path, signer: Path,
                         source_record: dict, temp_root: Path) -> dict:
    """Sign reviewed data with existing local keys; caller enforces custody and provenance.

    No candidate executable is launched. Temporary files contain only public probes,
    signatures and packaging intermediates; Keychain keys remain in place.
    """
    openssl = signing_openssl()
    keychain_signer = KeychainSparkleSigner(signer)
    identities = command(["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"], timeout=60)
    if not re.search(r"\b" + CERTIFICATE_SHA1 + r'\s+"' + re.escape(IDENTITY) + r'"', identities):
        raise ValueError("existing local Developer ID identity differs")
    validate_app(app, source_record, source_record["channel"])
    with tempfile.TemporaryDirectory(prefix="amplifai-local-signing-", dir=temp_root) as folder:
        private = Path(folder)
        private.chmod(0o700)
        probe = private / "key-continuity-probe"
        probe.write_bytes(b"AMPLIFai protected-release key-continuity probe\n")
        sign_and_verify(probe, signer, openssl, private, keychain_signer=keychain_signer)
        return _sign_package(app, source_zip, output, signer, source_record, openssl, private, None,
                             keychain_signer=keychain_signer)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("dry-run", "eligible", "authorize", "signed-candidate"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy = exact_json((ROOT / "release_control/policy.json").read_bytes())
    request = {"sourceCommit": os.environ["SOURCE_COMMIT"], "runId": int(os.environ["BUILD_RUN_ID"]),
               "artifactId": int(os.environ["BUILD_ARTIFACT_ID"]), "artifactSha256": os.environ["ARTIFACT_SHA256"],
               "channel": os.environ["RELEASE_CHANNEL"]}
    if (not re.fullmatch(r"[0-9a-f]{40}", request["sourceCommit"])
            or not re.fullmatch(r"[0-9a-f]{64}", request["artifactSha256"])
            or request["runId"] <= 0 or request["artifactId"] <= 0 or request["channel"] not in CHANNELS):
        parser.error("exact source, run/artifact IDs, digest and channel required")
    receipt, approved = inspected(policy, request, signed=args.mode != "dry-run", custody=args.mode != "eligible")
    args.output.mkdir(parents=True, exist_ok=False)
    if args.mode in {"dry-run", "signed-candidate"}:
        with tempfile.TemporaryDirectory(prefix="amplifai-ci-input-", dir=os.environ["RUNNER_TEMP"]) as folder:
            stage = Path(folder)
            archive = stage / "artifact.zip"
            download(f"https://api.github.com/repos/{policy['repository']}/actions/artifacts/{request['artifactId']}/zip",
                     archive, request["artifactSha256"], receipt["artifactBytes"], authenticated=True)
            receipt["artifactDigestVerified"] = True
            if args.mode == "signed-candidate":
                if os.uname().machine != CHANNELS[request["channel"]][0]:
                    raise ValueError("host architecture differs from candidate channel")
                with zipfile.ZipFile(archive) as artifact:
                    if (len(artifact.infolist()) != 3 or "qualification.json" not in artifact.namelist()
                            or artifact.getinfo("qualification.json").file_size > 65536):
                        raise ValueError("trusted build qualification record and two payload ZIPs required")
                    approved = validate_delivery_record(exact_json(artifact.read("qualification.json")), approved, request)
                    expected = {record["name"]: record for record in (approved["appArchive"], approved["sourceArchive"])}
                    if set(artifact.namelist()) != set(expected) | {"qualification.json"}:
                        raise ValueError("qualified artifact member set differs")
                    for name, record in expected.items():
                        if Path(name).name != name or artifact.getinfo(name).file_size != record["bytes"]:
                            raise ValueError("artifact member differs from approved bytes")
                        destination = stage / name
                        with artifact.open(name) as source, destination.open("xb") as target:
                            shutil.copyfileobj(source, target)
                        if digest(destination) != record["sha256"]:
                            raise ValueError("artifact member digest differs")
                app = unpack_app(stage / approved["appArchive"]["name"], stage, CHANNELS[request["channel"]][2])
                validate_app(app, approved, request["channel"])
                tools = stage / "tools.tar.xz"
                download(SPARKLE_URL, tools, SPARKLE_SHA256, 16319840)
                command(["tar", "-xf", tools, "-C", stage])
                receipt["signing"] = sign_candidate(app, stage / approved["sourceArchive"]["name"], args.output,
                                                    stage / "bin/sign_update", approved)
    (args.output / "release-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
