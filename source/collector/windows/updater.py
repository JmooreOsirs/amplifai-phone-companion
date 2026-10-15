"""Signed Windows Test updates; download is separate from explicit MSI launch."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import struct
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from amplifai_phone.windows_storage import windows_process_alive

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

VERSION = "2026.10.15-rc19"
BUILD = "26101519"
INSTALLER_VERSION = "1.0.19"
BUNDLE_ID = "ai.satoris.amplifai.phone.windows-candidate"
CHANNEL = "windows-x64-test"
FEED_URL = "https://amplifai-database-engine.vercel.app/phone/windows/update-v1.json"
PUBLIC_KEY = "vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs="
MAX_MANIFEST_BYTES = 16384
MAX_INSTALLER_BYTES = 512 * 1024 * 1024
REQUEST_TIMEOUT = 10
DOWNLOAD_SECONDS = 180
RELEASE_PREFIX = "/JmooreOsirs/amplifai-phone-companion/releases/download/"
ASSET_HOSTS = frozenset({"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"})
FIELDS = frozenset({"schema", "platform", "architecture", "channel", "version", "build", "installerVersion",
                    "installerUrl", "installerBytes", "installerSha256", "installerEdSignature", "installerTrust"})
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class UpdateError(ValueError):
    """Public, bounded failure reason; never includes received content or local paths."""


def signature_bytes(value: object) -> bytes:
    if not isinstance(value, str) or len(value) != 88:
        raise UpdateError("Update signature is missing or malformed.")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise UpdateError("Update signature is missing or malformed.") from error
    if len(decoded) != 64 or base64.b64encode(decoded).decode("ascii") != value:
        raise UpdateError("Update signature is missing or malformed.")
    return decoded


def verify_signature(data: bytes, signature: object, public_key: str = PUBLIC_KEY) -> None:
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key, validate=True))
        key.verify(signature_bytes(signature), data)
    except (InvalidSignature, ValueError, binascii.Error) as error:
        raise UpdateError("Update signature could not be verified.") from error


def installer_url(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 1024:
        raise UpdateError("Update installer URL is invalid.")
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as error:
        raise UpdateError("Update installer URL is invalid.") from error
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment
            or not parsed.path.startswith(RELEASE_PREFIX)):
        raise UpdateError("Update installer URL is outside the official release channel.")
    suffix = parsed.path[len(RELEASE_PREFIX):]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}/[A-Za-z0-9][A-Za-z0-9._-]{0,159}\.msi", suffix):
        raise UpdateError("Update installer must be a fixed tagged MSI asset.")
    if suffix.split("/", 1)[0].lower() == "latest":
        raise UpdateError("Update installer must be a fixed tagged MSI asset.")
    return value


def _unique_fields(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError("Update manifest contains duplicate fields.")
        result[key] = value
    return result


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    build: str
    installer_version: str
    url: str
    size: int
    sha256: str
    signature: str
    trust: str
    signed_bytes: bytes = b""
    signed_signature: bytes = b""


def parse_manifest(data: bytes, signature: bytes, *, public_key: str = PUBLIC_KEY,
                   current_build: str = BUILD, current_installer: str = INSTALLER_VERSION) -> UpdateManifest:
    if not 1 <= len(data) <= MAX_MANIFEST_BYTES:
        raise UpdateError("Update manifest exceeds its size limit.")
    if not 1 <= len(signature) <= 128:
        raise UpdateError("Update signature is missing or exceeds its size limit.")
    try:
        encoded_signature = signature.decode("ascii").strip()
    except UnicodeError as error:
        raise UpdateError("Update signature is malformed.") from error
    verify_signature(data, encoded_signature, public_key)
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_fields)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise UpdateError("Signed update manifest is not valid UTF-8 JSON.") from error
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise UpdateError("Update manifest fields are invalid.")
    if (type(value["schema"]) is not int or value["schema"] != 1 or value["platform"] != "windows"
            or value["architecture"] != "x86_64" or value["channel"] != CHANNEL):
        raise UpdateError("Update is incompatible with this Windows x64 Test channel.")
    if not isinstance(value["version"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}", value["version"]):
        raise UpdateError("Update version is invalid.")
    if not isinstance(value["build"], str) or not re.fullmatch(r"[1-9][0-9]{0,11}", value["build"]):
        raise UpdateError("Update build is invalid.")
    version = value["installerVersion"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,5}", version):
        raise UpdateError("Update MSI version is invalid.")
    parts = tuple(map(int, version.split(".")))
    if parts[0] > 255 or parts[1] > 255 or parts[2] > 65535:
        raise UpdateError("Update MSI version is outside Windows Installer limits.")
    if int(value["build"]) < int(current_build) or parts < tuple(map(int, current_installer.split("."))):
        raise UpdateError("An older update was refused.")
    if ((int(value["build"]) == int(current_build)) != (parts == tuple(map(int, current_installer.split("."))))):
        raise UpdateError("Update build and MSI version do not advance together.")
    if type(value["installerBytes"]) is not int or not 1 <= value["installerBytes"] <= MAX_INSTALLER_BYTES:
        raise UpdateError("Update installer size is invalid.")
    if not isinstance(value["installerSha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["installerSha256"]):
        raise UpdateError("Update installer checksum is malformed.")
    signature_bytes(value["installerEdSignature"])
    if not isinstance(value["installerTrust"], str) or value["installerTrust"] not in {"unsigned-test", "authenticode"}:
        raise UpdateError("Update installer trust label is invalid.")
    return UpdateManifest(value["version"], value["build"], version, installer_url(value["installerUrl"]),
                          value["installerBytes"], value["installerSha256"], value["installerEdSignature"], value["installerTrust"], data, signature)


class RestrictedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, *, assets: bool) -> None:
        super().__init__()
        self.assets = assets

    def redirect_request(self, request, response, code, message, headers, new_url):
        parsed = urllib.parse.urlsplit(new_url)
        if (not self.assets or parsed.scheme != "https" or parsed.hostname not in ASSET_HOSTS
                or parsed.username or parsed.password or parsed.port not in {None, 443} or parsed.fragment):
            raise UpdateError("Update redirect was refused.")
        if parsed.hostname == "github.com":
            installer_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def _read_url(url: str, limit: int, *, assets: bool = False, cancelled: threading.Event | None = None) -> bytes:
    opener = urllib.request.build_opener(RestrictedRedirects(assets=assets))
    request = urllib.request.Request(url, headers={"User-Agent": "AMPLIFaiPhone-Windows-Test/" + VERSION,
                                                 "Accept-Encoding": "identity", "Cache-Control": "no-cache"})
    deadline = time.monotonic() + DOWNLOAD_SECONDS
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                raise UpdateError("Update server returned an unsupported response.")
            length = response.headers.get("Content-Length")
            if length is not None and (not re.fullmatch(r"[0-9]{1,12}", length) or int(length) > limit):
                raise UpdateError("Update download exceeds its size limit.")
            chunks, total = [], 0
            while True:
                if time.monotonic() > deadline or cancelled is not None and cancelled.is_set():
                    raise UpdateError("Update download was cancelled or timed out.")
                chunk = response.read1(min(1024 * 1024, limit + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise UpdateError("Update download exceeds its size limit.")
                chunks.append(chunk)
            if length is not None and total != int(length):
                raise UpdateError("Update download was incomplete.")
            return b"".join(chunks)
    except (OSError, urllib.error.URLError, ValueError) as error:
        if isinstance(error, UpdateError):
            raise
        raise UpdateError("Update could not be downloaded securely. Try again later.") from error


def verify_installer(data: bytes, manifest: UpdateManifest, public_key: str = PUBLIC_KEY) -> None:
    if len(data) != manifest.size or len(data) > MAX_INSTALLER_BYTES:
        raise UpdateError("Update installer byte count does not match.")
    if hashlib.sha256(data).hexdigest() != manifest.sha256:
        raise UpdateError("Update installer checksum does not match.")
    verify_signature(data, manifest.signature, public_key)


def require_matching_cpu() -> None:
    if (struct.calcsize("P") != 8 or platform.machine().lower() not in {"amd64", "x86_64"}
            or os.environ.get("PROCESSOR_ARCHITEW6432", "").lower() == "arm64"):
        raise UpdateError("Automatic update preparation requires a matching Windows x64 PC.")


def check_update(*, cancelled: threading.Event | None = None) -> UpdateManifest:
    require_matching_cpu()
    data = _read_url(FEED_URL, MAX_MANIFEST_BYTES, cancelled=cancelled)
    signature = _read_url(FEED_URL + ".sig", 128, cancelled=cancelled)
    return parse_manifest(data, signature)


class PreparedUpdate:
    def __init__(self, manifest: UpdateManifest, *, cancelled: threading.Event | None = None,
                 public_key: str = PUBLIC_KEY) -> None:
        self.manifest = manifest
        self.public_key = public_key
        self.directory: Path | None = None
        self.owner_pid = os.getpid()
        self.helper_pid: int | None = None
        self.bootstrap_sha256: str | None = None
        data = _read_url(manifest.url, manifest.size, assets=True, cancelled=cancelled)
        verify_installer(data, manifest, self.public_key)
        self.directory = Path(tempfile.mkdtemp(prefix="AmplifaiPhone-update-"))
        self.path = self.directory / "AmplifaiPhone-verified.msi"
        try:
            with self.path.open("xb") as target:
                target.write(data)
            (self.directory / "manifest.json").write_bytes(manifest.signed_bytes)
            (self.directory / "manifest.sig").write_bytes(manifest.signed_signature)
            self._write_marker(installing=False, installer_pid=None)
        except OSError:
            self.cleanup()
            raise UpdateError("Verified update could not be prepared locally.") from None

    def _write_marker(self, *, installing: bool, installer_pid: int | None) -> None:
        if self.directory is None:
            raise UpdateError("Prepared update is no longer available.")
        marker = {"schema": 2, "owner": "amplifai-phone-windows-update-v2", "directory": self.directory.name,
                  "ownerPid": self.owner_pid, "helperPid": self.helper_pid, "installing": installing,
                  "installerPid": installer_pid, "bootstrapSha256": self.bootstrap_sha256}
        temporary = self.directory / "ownership.new"
        with temporary.open("w", encoding="utf-8") as target:
            json.dump(marker, target, separators=(",", ":")); target.flush(); os.fsync(target.fileno())
        os.replace(temporary, self.directory / "ownership.json")

    def verify_local(self) -> None:
        if self.directory is None:
            raise UpdateError("Prepared update is no longer available. Check again.")
        for path in (self.directory, self.path):
            item = path.lstat()
            if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & REPARSE_POINT:
                raise UpdateError("Prepared update path changed. Check again.")
        if not self.path.is_file() or self.path.stat().st_size != self.manifest.size:
            raise UpdateError("Prepared update file changed. Check again.")
        with self.path.open("rb") as source:
            verify_installer(source.read(self.manifest.size + 1), self.manifest, self.public_key)

    def launch(self, helper: Path):
        self.verify_local()
        from update_installer import start_handoff
        return start_handoff(self, helper)

    def cleanup(self) -> None:
        if self.helper_pid is not None and os.name == "nt" and windows_process_alive(self.helper_pid):
            raise UpdateError("The verified updater is still closing. Check again shortly.")
        if self.directory is not None:
            shutil.rmtree(self.directory)
            self.directory = None


def cached_manifest(directory: Path, *, public_key: str = PUBLIC_KEY,
                    current_build: str = BUILD, current_installer: str = INSTALLER_VERSION) -> tuple[dict, UpdateManifest]:
    if not directory.is_absolute() or not re.fullmatch(r"AmplifaiPhone-update-[a-z0-9_]{8}", directory.name):
        raise UpdateError("Update cache path is invalid.")
    if directory.parent.resolve() != Path(tempfile.gettempdir()).resolve():
        raise UpdateError("Update cache is outside its fixed temporary root.")
    for path in (directory, directory / "ownership.json", directory / "manifest.json", directory / "manifest.sig"):
        item = path.lstat()
        if stat.S_ISLNK(item.st_mode) or getattr(item, "st_file_attributes", 0) & REPARSE_POINT:
            raise UpdateError("Update cache path changed.")
    marker_path = directory / "ownership.json"
    if marker_path.stat().st_size > 2048:
        raise UpdateError("Update ownership marker exceeds its limit.")
    marker = json.loads(marker_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields)
    if (not isinstance(marker, dict) or set(marker) != {"schema", "owner", "directory", "ownerPid", "helperPid", "installing", "installerPid", "bootstrapSha256"}
            or type(marker["schema"]) is not int or marker["schema"] != 2
            or marker["owner"] != "amplifai-phone-windows-update-v2" or marker["directory"] != directory.name
            or type(marker["ownerPid"]) is not int or not 1 <= marker["ownerPid"] <= 2**32 - 1
            or type(marker["installing"]) is not bool
            or any(pid is not None and (type(pid) is not int or not 1 <= pid <= 2**32 - 1) for pid in (marker["helperPid"], marker["installerPid"]))
            or marker["bootstrapSha256"] is not None and (not isinstance(marker["bootstrapSha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", marker["bootstrapSha256"]))):
        raise UpdateError("Update ownership marker is invalid.")
    with (directory / "manifest.json").open("rb") as source:
        data = source.read(MAX_MANIFEST_BYTES + 1)
    with (directory / "manifest.sig").open("rb") as source:
        signature = source.read(129)
    return marker, parse_manifest(data, signature, public_key=public_key,
                                  current_build=current_build, current_installer=current_installer)


def cleanup_owned_updates(*, public_key: str = PUBLIC_KEY) -> int:
    """Remove only signed, marked, inactive downloads; never inspect phone data roots."""
    removed = 0
    if os.name != "nt":
        return removed
    from update_installer import read_runtime_inventory, validate_runtime_files
    for index, directory in enumerate(Path(tempfile.gettempdir()).glob("AmplifaiPhone-update-*")):
        if index >= 64:
            break
        try:
            marker, manifest = cached_manifest(directory, public_key=public_key, current_build="1", current_installer="0.0.0")
            if windows_process_alive(marker["ownerPid"]) or any(pid is not None and windows_process_alive(pid) for pid in (marker["helperPid"], marker["installerPid"])):
                continue
            if marker["installing"] and marker["helperPid"] is None and marker["installerPid"] is None:
                continue
            msi_exists = (directory / "AmplifaiPhone-verified.msi").exists()
            expected = {"ownership.json", "manifest.json", "manifest.sig"}
            if msi_exists:
                expected.add("AmplifaiPhone-verified.msi")
            elif marker["installing"] or marker["helperPid"] is None or marker["bootstrapSha256"] is None:
                continue
            if marker["bootstrapSha256"] is not None:
                expected |= {"bootstrap", "bootstrap-inventory.json"}
                records = read_runtime_inventory(directory, marker["bootstrapSha256"])
                validate_runtime_files(directory / "bootstrap", records)
            if {entry.name for entry in directory.iterdir()} != expected:
                continue
            prepared = PreparedUpdate.__new__(PreparedUpdate)
            prepared.manifest, prepared.public_key, prepared.directory = manifest, public_key, directory
            prepared.helper_pid = marker["helperPid"]
            prepared.path = directory / "AmplifaiPhone-verified.msi"
            if msi_exists:
                prepared.verify_local()
            prepared.cleanup()
            removed += 1
        except (OSError, ValueError):
            # Unverified, modified, inaccessible or live directories are never removed.
            continue
    return removed
