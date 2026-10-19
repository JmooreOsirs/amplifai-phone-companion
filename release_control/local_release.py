"""Explicit owner-Mac release path for reviewed, qualified native artifacts.

Run with ``python3 -I -B`` from a clean checkout of current protected main.
The hosted release entrypoints remain separate.
"""
import sys

if __name__ == "__main__" and (not sys.flags.isolated or not sys.dont_write_bytecode):
    raise SystemExit("local release requires python3 -I -B")

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import tempfile
from urllib.parse import quote
import uuid
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "JmooreOsirs/amplifai-phone-companion"
NOTARY_PROFILE = "AMPLIFai-notary"
SPARKLE_ACCOUNT = "ai.satoris.amplifai.phone.candidate"
MAX_METADATA = 4 * 1024 * 1024
MAX_DELIVERY = 4 * 1024**3
MAX_FILES = 64
MANIFEST = "local-signing-manifest.json"
MANIFEST_SIGNATURE = MANIFEST + ".sig"
HOSTED_ENV = ("GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "GITHUB_WORKFLOW_REF", "GITHUB_RUN_ID",
              "GITHUB_SHA", "GITHUB_REF", "GITHUB_REPOSITORY", "RUNNER_TEMP", "GH_TOKEN",
              "GITHUB_TOKEN", "GH_HOST", "GH_REPO", "GH_ENTERPRISE_TOKEN",
              "GITHUB_ENTERPRISE_TOKEN", "GH_POLICY_AUDIT_TOKEN", "APPLE_CERTIFICATE_P12_BASE64",
              "APPLE_CERTIFICATE_PASSWORD", "NOTARY_APPLE_ID", "NOTARY_APP_PASSWORD",
              "SPARKLE_PRIVATE_KEY")
GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_PARAMETERS",
           "GIT_CONFIG_COUNT", "GIT_EXEC_PATH", "GIT_ALTERNATE_OBJECT_DIRECTORIES")


def _safe_environment() -> None:
    if (not sys.flags.isolated or not sys.dont_write_bytecode or "PYTHONPATH" in os.environ
            or "PYTHONHOME" in os.environ or any(name in os.environ for name in HOSTED_ENV + GIT_ENV)):
        raise ValueError("isolated local Python and credential-free non-hosted environment required")


def _git(*args: str) -> bytes:
    try:
        result = subprocess.run(["/usr/bin/git", *args], cwd=ROOT, capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("local Git proof unavailable") from None
    if result.returncode:
        raise ValueError("local Git proof failed")
    return result.stdout.strip()


class GitHubClient:
    """Use the existing gh login without extracting or printing its token."""

    def __init__(self, repository: str):
        self.repository = repository

    def _endpoint(self, path: str) -> str:
        endpoint = path.lstrip("/")
        if (endpoint != "user" and not endpoint.startswith("repos/" + self.repository + "/")
                or any(part in {"", ".", ".."} for part in endpoint.split("/"))
                or any(char in endpoint for char in "\r\n#")):
            raise ValueError("GitHub API path outside exact repository")
        return endpoint

    def _request(self, path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, bytes]:
        if method not in {"GET", "POST"} or (method == "GET" and payload is not None):
            raise ValueError("unsupported GitHub API request")
        args = ["gh", "api", "--hostname", "github.com", "--include", "--method", method,
                self._endpoint(path)]
        if payload is not None:
            args += ["--input", "-"]
        body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
        with tempfile.TemporaryFile() as capture:
            try:
                result = subprocess.run(args, input=body, stdout=capture, stderr=subprocess.DEVNULL,
                                        cwd=ROOT, timeout=60)
            except (OSError, subprocess.TimeoutExpired):
                raise ValueError("authenticated GitHub API unavailable") from None
            if capture.tell() > MAX_METADATA:
                raise ValueError("GitHub API response exceeds metadata bound")
            capture.seek(0)
            response = capture.read()
        header, separator, content = response.partition(b"\r\n\r\n")
        if not separator:
            header, separator, content = response.partition(b"\n\n")
        first = header.splitlines()[0] if header else b""
        match = re.fullmatch(rb"HTTP/[^ ]+ ([0-9]{3})(?: .*)?", first)
        if not separator or not match:
            raise ValueError("authenticated GitHub API status unavailable")
        status = int(match.group(1))
        if result.returncode and status != 404:
            raise ValueError("authenticated GitHub API request denied or failed")
        return status, content

    def request_json(self, path: str, method: str = "GET", payload: dict | None = None) -> dict | list:
        status, content = self._request(path, method, payload)
        if status < 200 or status >= 300:
            raise ValueError("authenticated GitHub API request denied or failed")
        if len(content) > MAX_METADATA:
            raise ValueError("GitHub API JSON exceeds metadata bound")
        try:
            parsed = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("GitHub API JSON invalid") from None
        if not isinstance(parsed, (dict, list)):
            raise ValueError("GitHub API JSON object or array required")
        return parsed

    def get_optional(self, path: str) -> dict | list | None:
        status, content = self._request(path)
        if status == 404:
            return None
        if status < 200 or status >= 300:
            raise ValueError("authenticated GitHub API request denied or failed")
        try:
            parsed = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("GitHub API JSON invalid") from None
        if not isinstance(parsed, (dict, list)):
            raise ValueError("GitHub API JSON object or array required")
        return parsed

    def download_artifact(self, artifact_id: int, destination: Path, expected_sha: str, expected_bytes: int,
                          digest_function) -> None:
        if type(artifact_id) is not int or artifact_id <= 0 or not 0 < expected_bytes <= 2 * 1024**3:
            raise ValueError("invalid qualified artifact bounds")
        endpoint = self._endpoint(f"repos/{self.repository}/actions/artifacts/{artifact_id}/zip")
        try:
            with destination.open("xb") as target:
                result = subprocess.run(["gh", "api", "--hostname", "github.com", endpoint],
                                        stdout=target, stderr=subprocess.DEVNULL, cwd=ROOT, timeout=1800)
        except (OSError, subprocess.TimeoutExpired):
            destination.unlink(missing_ok=True)
            raise ValueError("qualified artifact download unavailable") from None
        if result.returncode or destination.stat().st_size != expected_bytes or digest_function(destination) != expected_sha:
            destination.unlink(missing_ok=True)
            raise ValueError("qualified artifact bytes differ from GitHub digest")


def verify_control_checkout(github: GitHubClient) -> str:
    if Path(os.fsdecode(_git("rev-parse", "--show-toplevel"))).resolve() != ROOT:
        raise ValueError("local control checkout root differs")
    if _git("symbolic-ref", "--quiet", "HEAD") != b"refs/heads/main":
        raise ValueError("local release requires checked-out protected main")
    if _git("status", "--porcelain=v1", "--untracked-files=all"):
        raise ValueError("local control checkout is dirty or has untracked files")
    if _git("ls-files", "--others", "--ignored", "--exclude-standard"):
        raise ValueError("ignored files could override reviewed release code")
    if any(line[:1] != b"H" for line in _git("ls-files", "-v").splitlines()):
        raise ValueError("assume-unchanged or skip-worktree control files prohibited")
    if any(line.startswith((b"120000", b"160000")) for line in _git("ls-files", "--stage").splitlines()):
        raise ValueError("control checkout must not contain linked files or submodules")
    head = _git("rev-parse", "HEAD").decode()
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("invalid control commit")
    branch = github.request_json(f"repos/{REPOSITORY}/branches/main")
    if (not isinstance(branch, dict) or branch.get("protected") is not True
            or branch.get("commit", {}).get("sha") != head):
        raise ValueError("checkout is not the current protected main commit")
    return head


def _require_reviewed_main(github: GitHubClient, policy: dict, head: str) -> None:
    from authority import require_approved_merge
    require_approved_merge(github.request_json, policy, head, "main")


def _protected_refs(github: GitHubClient, policy: dict, release_module) -> None:
    for ref in (policy["controlRef"], policy["sourceRef"]):
        branch = quote(ref.removeprefix("refs/heads/"), safe="")
        metadata = github.request_json(f"repos/{REPOSITORY}/branches/{branch}")
        protection = github.request_json(f"repos/{REPOSITORY}/branches/{branch}/protection")
        owners = github.request_json(f"repos/{REPOSITORY}/contents/.github/CODEOWNERS?ref={quote(ref, safe='')}")
        if (not isinstance(metadata, dict) or not isinstance(owners, dict)
                or metadata.get("protected") is not True or owners.get("encoding") != "base64"):
            raise ValueError("both refs need active protection and readable CODEOWNERS")
        try:
            content = base64.b64decode(owners["content"].replace("\n", ""), validate=True).decode()
        except (ValueError, KeyError, UnicodeDecodeError):
            raise ValueError("effective CODEOWNERS invalid") from None
        release_module.validate_branch_custody(policy, protection, content)


def _qualified_runner(github: GitHubClient, request: dict) -> str:
    jobs = github.request_json(f"repos/{REPOSITORY}/actions/runs/{request['runId']}/jobs?per_page=100")
    rows = jobs.get("jobs", []) if isinstance(jobs, dict) else []
    channel = request["channel"]
    expected_name, expected_label = (("arm64", "macos-15") if channel == "macos-arm64"
                                     else ("intel", "macos-15-intel"))
    if (not isinstance(jobs, dict) or jobs.get("total_count") != 1 or len(rows) != 1
            or rows[0].get("name") != expected_name or rows[0].get("conclusion") != "success"
            or rows[0].get("status") != "completed" or rows[0].get("head_sha") != request["sourceCommit"]
            or rows[0].get("labels") != [expected_label]):
        raise ValueError("trusted qualification job did not complete on exact channel runner")
    return expected_label


def _host_architecture(policy: dict, request: dict, qualified_runner: str, release_module) -> tuple[str, str]:
    if platform.system() != "Darwin":
        raise ValueError("local Keychain release requires a Darwin host")
    host = platform.machine()
    candidate = release_module.CHANNELS[request["channel"]][0]
    if host == candidate:
        return host, candidate
    expected = {"signerHost": "arm64", "candidate": "x86_64", "qualificationRunner": "macos-15-intel"}
    local = policy["localRelease"]
    if (request["channel"] != "macos-intel-test" or host != "arm64" or candidate != "x86_64"
            or qualified_runner != "macos-15-intel" or local.get("crossSigning") != expected):
        raise ValueError("local cross-architecture signing is not explicitly reviewed")
    return host, candidate


def validate_request(request: dict, release_module) -> None:
    if (set(request) != {"sourceCommit", "runId", "artifactId", "artifactSha256", "channel"}
            or not isinstance(request["sourceCommit"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", request["sourceCommit"])
            or type(request["runId"]) is not int or request["runId"] <= 0
            or type(request["artifactId"]) is not int or request["artifactId"] <= 0
            or not isinstance(request["artifactSha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", request["artifactSha256"])
            or request["channel"] not in release_module.CHANNELS):
        raise ValueError("exact qualified source, run, artifact, digest and channel required")


def authorize_local(github: GitHubClient, policy: dict, request: dict, control_commit: str,
                    release_module, *, publishing: bool = False) -> tuple[dict, dict]:
    validate_request(request, release_module)
    release_module.validate_release_authority(policy)
    local = policy.get("localRelease")
    if (policy.get("repository") != REPOSITORY or policy.get("controlRef") != "refs/heads/main"
            or policy.get("sourceRef") != "refs/heads/release/native"
            or policy.get("activationApproved") is not True or not isinstance(local, dict)
            or local.get("enabled") is not True or local.get("operators") != ["JmooreOsirs"]
            or local.get("notaryProfile") != NOTARY_PROFILE or local.get("sparkleAccount") != SPARKLE_ACCOUNT
            or publishing and policy.get("publicationEnabled") is not True):
        raise ValueError("explicit reviewed local release policy is inactive or differs")
    user = github.request_json("user")
    operator = user.get("login") if isinstance(user, dict) else None
    if operator not in local["operators"] or operator not in policy["operators"]:
        raise ValueError("authenticated GitHub operator is not authorized for local release")
    _require_reviewed_main(github, policy, control_commit)
    _protected_refs(github, policy, release_module)
    run = github.request_json(f"repos/{REPOSITORY}/actions/runs/{request['runId']}")
    artifact = github.request_json(f"repos/{REPOSITORY}/actions/artifacts/{request['artifactId']}")
    comparison = github.request_json(
        f"repos/{REPOSITORY}/compare/{policy['qualifiedAncestor']}...{request['sourceCommit']}")
    release_module.validate_provenance(policy, request, run, artifact, comparison)
    source = release_module.validate_approved_source(policy, request, run)
    from authority import require_approved_merge
    require_approved_merge(github.request_json, policy, request["sourceCommit"], "release/native")
    runner = _qualified_runner(github, request)
    host, candidate = _host_architecture(policy, request, runner, release_module)
    return source, {"operator": operator, "controlCommit": control_commit,
                    "artifactBytes": artifact["size_in_bytes"], "artifactName": artifact["name"],
                    "qualificationRunner": runner, "signerHostArchitecture": host,
                    "candidateArchitecture": candidate}


def _owner_directory(path: Path, *, create: bool = False) -> Path:
    if not path.is_absolute() or path.is_symlink() or path.resolve().is_relative_to(ROOT):
        raise ValueError("owner release directory must be absolute, separate from checkout, and not linked")
    if create:
        path.mkdir(mode=0o700)
    details = path.stat()
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid() or details.st_mode & 0o077:
        raise ValueError("owner release directory must be owned by current user with mode 0700")
    return path


def load_qualified_input(github: GitHubClient, request: dict, source: dict, artifact_bytes: int,
                         stage: Path, release_module, producer_module) -> tuple[Path, Path]:
    archive = stage / "qualified-artifact.zip"
    github.download_artifact(request["artifactId"], archive, request["artifactSha256"],
                             artifact_bytes, release_module.digest)
    with zipfile.ZipFile(archive) as zipped:
        if (len(zipped.infolist()) != 3 or "qualification.json" not in zipped.namelist()
                or zipped.getinfo("qualification.json").file_size > 65536):
            raise ValueError("trusted build qualification and two payload ZIPs required")
        approved = release_module.validate_delivery_record(
            release_module.exact_json(zipped.read("qualification.json")), source, request)
        expected = {row["name"]: row for row in (approved["appArchive"], approved["sourceArchive"])}
        if set(zipped.namelist()) != set(expected) | {"qualification.json"}:
            raise ValueError("qualified artifact member set differs")
        for name, record in expected.items():
            if Path(name).name != name or zipped.getinfo(name).file_size != record["bytes"]:
                raise ValueError("qualified artifact member length or name differs")
            destination = stage / name
            with zipped.open(name) as source_file, destination.open("xb") as target:
                shutil.copyfileobj(source_file, target)
            if release_module.digest(destination) != record["sha256"]:
                raise ValueError("qualified payload digest differs")
    app = release_module.unpack_app(stage / approved["appArchive"]["name"], stage,
                                    release_module.CHANNELS[request["channel"]][2])
    release_module.validate_app(app, approved, request["channel"])
    source_zip = stage / approved["sourceArchive"]["name"]
    producer_module.validate_signing_input(source_zip, app, request["sourceCommit"], request["channel"])
    return app, source_zip


def local_provenance(request: dict, source: dict, context: dict, signing: dict,
                     release_module) -> dict:
    notary = {"app": signing.get("appNotarization"), "dmg": signing.get("dmgNotarization")}
    identity = signing.get("signingIdentity")
    expected_identity = {"developerId": release_module.IDENTITY, "certificateSha1": release_module.CERTIFICATE_SHA1,
                         "teamId": release_module.TEAM, "sparklePublicKey": release_module.PUBLIC_KEY,
                         "sparkleAccount": SPARKLE_ACCOUNT, "notaryProfile": NOTARY_PROFILE}
    if (signing.get("signingMode") != "local-keychain" or identity != expected_identity
            or any(not isinstance(item, dict) or not isinstance(item.get("id"), str)
                   or not item["id"] or item.get("status") != "Accepted" for item in notary.values())
            or signing.get("notarized") is not True):
        raise ValueError("local signing identity and accepted notary receipts required")
    return {"kind": "local-keychain", "controlCommit": context["controlCommit"],
            "operator": context["operator"], "executionId": str(uuid.uuid4()),
            "recordedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "qualification": {"runId": request["runId"], "artifactId": request["artifactId"],
                              "sha256": request["artifactSha256"], "sourceCommit": request["sourceCommit"]},
            "channel": request["channel"], "version": source["version"], "build": source["build"],
            "signerHostArchitecture": context["signerHostArchitecture"],
            "candidateArchitecture": context["candidateArchitecture"],
            "qualificationRunner": context["qualificationRunner"],
            "signingIdentity": identity, "notary": notary}


def _canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()


def _delivery_files(root: Path, release_module) -> list[dict]:
    rows = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("signed delivery contains a linked file")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("signed delivery contains an unsupported node")
        relative = path.relative_to(root).as_posix()
        if relative in {MANIFEST, MANIFEST_SIGNATURE}:
            continue
        size = path.stat().st_size
        total += size
        if not 0 < size <= 2 * 1024**3 or total > MAX_DELIVERY or len(rows) >= MAX_FILES:
            raise ValueError("signed delivery exceeds manifest bounds")
        rows.append({"path": relative, "bytes": size, "sha256": release_module.digest(path)})
    return rows


def write_signed_manifest(root: Path, provenance: dict, signer: Path, temp_root: Path,
                          release_module) -> None:
    manifest = root / MANIFEST
    signature = root / MANIFEST_SIGNATURE
    if manifest.exists() or signature.exists():
        raise ValueError("signed delivery manifest already exists")
    body = {"schema": 1, "kind": "local-keychain-signed-delivery", "localProvenance": provenance,
            "files": _delivery_files(root, release_module)}
    if not body["files"] or "release-receipt.json" not in {row["path"] for row in body["files"]}:
        raise ValueError("local release receipt missing from signed manifest")
    manifest.write_bytes(_canonical(body))
    signature.write_text(release_module.sign_local_and_verify(
        manifest, signer, release_module.signing_openssl(), temp_root) + "\n")


def _verify_manifest_signature(manifest: Path, signature: Path, temp_root: Path, release_module) -> None:
    try:
        value = base64.b64decode(signature.read_text().strip(), validate=True)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("local signed manifest signature invalid") from None
    if len(value) != 64:
        raise ValueError("local signed manifest signature invalid")
    with tempfile.TemporaryDirectory(prefix="amplifai-local-manifest-", dir=temp_root) as folder:
        private = Path(folder)
        private.chmod(0o700)
        public = private / "public.der"
        public.write_bytes(bytes.fromhex("302a300506032b6570032100") + base64.b64decode(release_module.PUBLIC_KEY))
        sig = private / "signature"
        sig.write_bytes(value)
        release_module.command([release_module.signing_openssl(), "pkeyutl", "-verify", "-rawin", "-pubin",
                                "-keyform", "DER", "-inkey", public, "-in", manifest, "-sigfile", sig])


def validate_local_provenance(provenance: dict, receipt: dict, source: dict, request: dict,
                              context: dict, release_module) -> None:
    expected = {"kind", "controlCommit", "operator", "executionId", "recordedAt", "qualification",
                "channel", "version", "build", "signerHostArchitecture", "candidateArchitecture",
                "qualificationRunner", "signingIdentity", "notary"}
    qualification = {"runId": request["runId"], "artifactId": request["artifactId"],
                     "sha256": request["artifactSha256"], "sourceCommit": request["sourceCommit"]}
    identity = {"developerId": release_module.IDENTITY, "certificateSha1": release_module.CERTIFICATE_SHA1,
                "teamId": release_module.TEAM, "sparklePublicKey": release_module.PUBLIC_KEY,
                "sparkleAccount": SPARKLE_ACCOUNT, "notaryProfile": NOTARY_PROFILE}
    if (not isinstance(provenance, dict) or set(provenance) != expected
            or provenance["kind"] != "local-keychain"
            or provenance["controlCommit"] != context["controlCommit"]
            or provenance["operator"] != context["operator"]
            or provenance["qualification"] != qualification
            or any(provenance[key] != source[key] for key in ("channel", "version", "build"))
            or provenance["signerHostArchitecture"] != context["signerHostArchitecture"]
            or provenance["candidateArchitecture"] != context["candidateArchitecture"]
            or provenance["qualificationRunner"] != context["qualificationRunner"]
            or provenance["signingIdentity"] != identity
            or not isinstance(provenance["notary"], dict)
            or provenance["signingIdentity"] != receipt.get("signing", {}).get("signingIdentity")
            or provenance["notary"] != {"app": receipt.get("signing", {}).get("appNotarization"),
                                         "dmg": receipt.get("signing", {}).get("dmgNotarization")}
            or receipt.get("executionMode") != "local-keychain"
            or receipt.get("localProvenance") != provenance or receipt.get("request") != request
            or receipt.get("controlCommit") != context["controlCommit"]
            or receipt.get("operator") != context["operator"]
            or receipt.get("signingApproved") is not True):
        raise ValueError("local receipt differs from exact reviewed source and signer")
    try:
        execution_id = uuid.UUID(provenance["executionId"])
        if execution_id.version != 4 or str(execution_id) != provenance["executionId"]:
            raise ValueError("local execution ID must be UUIDv4")
        stamp = datetime.fromisoformat(provenance["recordedAt"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError("local execution time must be UTC")
    except (TypeError, AttributeError, ValueError):
        raise ValueError("local execution metadata invalid") from None
    for row in provenance["notary"].values():
        if (not isinstance(row, dict) or set(row) != {"id", "status"}
                or not isinstance(row["id"], str)
                or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", row["id"])
                or row["status"] != "Accepted"):
            raise ValueError("local app and DMG notary acceptance IDs required")


def verify_signed_manifest(root: Path, source: dict, request: dict, context: dict,
                           temp_root: Path, release_module) -> dict:
    manifest = root / MANIFEST
    signature = root / MANIFEST_SIGNATURE
    if not manifest.is_file() or manifest.is_symlink() or not signature.is_file() or signature.is_symlink():
        raise ValueError("local signed-delivery manifest and signature required")
    if manifest.stat().st_size > MAX_METADATA or signature.stat().st_size > 256:
        raise ValueError("local signed-delivery manifest exceeds bounds")
    body = release_module.exact_json(manifest.read_bytes())
    if (set(body) != {"schema", "kind", "localProvenance", "files"} or body["schema"] != 1
            or body["kind"] != "local-keychain-signed-delivery" or manifest.read_bytes() != _canonical(body)
            or not isinstance(body["files"], list) or not body["files"]):
        raise ValueError("canonical local signed-delivery manifest required")
    receipt = release_module.exact_json((root / "release-receipt.json").read_bytes())
    validate_local_provenance(body["localProvenance"], receipt, source, request, context, release_module)
    listed = body["files"]
    if (len(listed) > MAX_FILES or any(not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}
                                       for row in listed) or listed != _delivery_files(root, release_module)):
        raise ValueError("signed-delivery file inventory differs")
    _verify_manifest_signature(manifest, signature, temp_root, release_module)
    return body["localProvenance"]


def write_delivery_archive(delivery: Path, destination: Path, release_module) -> None:
    """Preserve the complete sealed directory for website intake without a hash cycle."""
    if destination.exists() or destination.resolve().is_relative_to(delivery.resolve()):
        raise ValueError("complete signed-delivery archive must be new and outside sealed delivery")
    rows = _delivery_files(delivery, release_module)
    names = [row["path"] for row in rows] + [MANIFEST, MANIFEST_SIGNATURE]
    if len(names) > MAX_FILES + 2:
        raise ValueError("complete signed-delivery archive exceeds file bound")
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for name in sorted(names):
            path = delivery / name
            if path.is_symlink() or not path.is_file():
                raise ValueError("complete signed-delivery archive contains an unsupported node")
            entry = zipfile.ZipInfo(name)
            entry.compress_type = zipfile.ZIP_STORED
            entry.external_attr = 0o100600 << 16
            with path.open("rb") as source_file, archive.open(entry, "w", force_zip64=True) as target:
                shutil.copyfileobj(source_file, target, length=1024 * 1024)


def _trusted_modules():
    sys.path.insert(0, str(ROOT / "release_control"))
    import release
    import producer
    import publish
    return release, producer, publish


def _inspect_or_sign(mode: str, github: GitHubClient, request: dict, context: dict,
                     source: dict, output: Path, temp_root: Path, release_module, producer_module) -> None:
    _owner_directory(output, create=True)
    completed = False
    try:
        with tempfile.TemporaryDirectory(prefix="amplifai-local-input-", dir=temp_root) as folder:
            stage = Path(folder)
            stage.chmod(0o700)
            app, source_zip = load_qualified_input(github, request, source, context["artifactBytes"],
                                                   stage, release_module, producer_module)
            receipt = {"schema": 1, "request": request, "artifactBytes": context["artifactBytes"],
                       "artifactName": context["artifactName"], "controlCommit": context["controlCommit"],
                       "operator": context["operator"], "artifactDigestVerified": True,
                       "qualifiedInputVerified": True, "published": False, "physicalAcceptance": False}
            if mode == "inspect":
                receipt.update(status="qualified-local-inspection-no-signing", signingApproved=False,
                               executionMode="local-keychain", signerHostArchitecture=context["signerHostArchitecture"],
                               candidateArchitecture=context["candidateArchitecture"],
                               qualificationRunner=context["qualificationRunner"])
                (output / "local-inspection.json").write_bytes(_canonical(receipt))
            else:
                tools = stage / "tools.tar.xz"
                release_module.download(release_module.SPARKLE_URL, tools, release_module.SPARKLE_SHA256, 16319840)
                release_module.command(["tar", "-xf", tools, "-C", stage])
                signer = stage / "bin/sign_update"
                signing = release_module.sign_local_candidate(app, source_zip, output, signer, source, temp_root)
                provenance = local_provenance(request, source, context, signing, release_module)
                receipt.update(status="signed-local-candidate-publication-pending", signingApproved=True,
                               executionMode="local-keychain", localProvenance=provenance, signing=signing)
                (output / "release-receipt.json").write_bytes(_canonical(receipt))
                write_signed_manifest(output, provenance, signer, temp_root, release_module)
        completed = True
    finally:
        if not completed:
            shutil.rmtree(output)


def _publish_local(github: GitHubClient, request: dict, context: dict, source: dict, delivery: Path,
                   output: Path, temp_root: Path, release_module, publish_module) -> None:
    _owner_directory(delivery)
    sealed_root = delivery.resolve()
    if output.resolve().is_relative_to(sealed_root) or temp_root.resolve().is_relative_to(sealed_root):
        raise ValueError("publication output and scratch must be outside sealed signed delivery")
    provenance = verify_signed_manifest(delivery, source, request, context, temp_root, release_module)
    spec, files = publish_module.validate_delivery(delivery, source, request, control_commit=context["controlCommit"])
    package_verification = publish_module.verify_local_package(delivery, source, temp_root=temp_root)
    for name in (MANIFEST, MANIFEST_SIGNATURE):
        files.append(delivery / name)
    _owner_directory(output, create=True)
    (output / "mounted-package-verification.json").write_bytes(_canonical({
        "schema": 1, "sourceCommit": source["sourceCommit"], "channel": source["channel"],
        "controlCommit": context["controlCommit"], "packageSha256": spec["package"]["sha256"],
        "verification": package_verification}))
    with tempfile.TemporaryDirectory(prefix="amplifai-local-publication-", dir=temp_root) as folder:
        work = Path(folder)
        work.chmod(0o700)
        (work / "feed").mkdir(mode=0o700)
        shutil.copyfile(delivery / "feed/release-receipt.json", work / "feed/release-receipt.json")
        archive = work / "local-signed-delivery.zip"
        write_delivery_archive(delivery, archive, release_module)
        files.append(archive)
        published = publish_module.publish(source, files, work, output, REPOSITORY, github=github)
    receipt = release_module.exact_json((delivery / "release-receipt.json").read_bytes())
    handoff = publish_module.website_handoff(source, spec, receipt, published, local_provenance=provenance)
    (output / "website-handoff.json").write_bytes(_canonical(handoff))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "sign", "publish"))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--build-run-id", type=int, required=True)
    parser.add_argument("--artifact-id", type=int, required=True)
    parser.add_argument("--artifact-sha256", required=True)
    parser.add_argument("--channel", choices=("macos-arm64", "macos-intel-test"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    parser.add_argument("--delivery", type=Path)
    args = parser.parse_args()
    try:
        _safe_environment()
        if (args.mode == "publish") != (args.delivery is not None):
            raise ValueError("--delivery is required only for local publication")
        temp_root = _owner_directory(args.temp_root)
        if args.output.exists():
            raise ValueError("new output directory must not already exist")
        if not args.output.is_absolute() or args.output.resolve().is_relative_to(ROOT):
            raise ValueError("output must be an absolute path outside control checkout")
        github = GitHubClient(REPOSITORY)
        control_commit = verify_control_checkout(github)
        release_module, producer_module, publish_module = _trusted_modules()
        policy = release_module.exact_json((ROOT / "release_control/policy.json").read_bytes())
        request = {"sourceCommit": args.source_commit, "runId": args.build_run_id,
                   "artifactId": args.artifact_id, "artifactSha256": args.artifact_sha256,
                   "channel": args.channel}
        source, context = authorize_local(github, policy, request, control_commit, release_module,
                                          publishing=args.mode == "publish")
        if args.mode == "publish":
            _publish_local(github, request, context, source, args.delivery, args.output, temp_root,
                           release_module, publish_module)
        else:
            _inspect_or_sign(args.mode, github, request, context, source, args.output,
                             temp_root, release_module, producer_module)
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
