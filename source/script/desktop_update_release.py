#!/usr/bin/env python3
"""Prepare desktop update files locally. Never upload assets or modify a website.

Production signing delegates exclusively to Sparkle 2.10's Keychain signer.
Verification uses an existing OpenSSL Ed25519 implementation and public keys only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
PINS = ROOT / "docs/DESKTOP-UPDATE-SOURCE-PINS.json"
PUBLIC_KEY = "vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs="
KEYCHAIN_ACCOUNT = "ai.satoris.amplifai.phone.candidate"
SIGN_UPDATE_SHA256 = "43c249771bafc3aa581228abae00731a012d324691b8292860896635050be76b"
SPARKLE = "http://www.andymatuschak.org/xml-namespaces/sparkle"
IDENTITY = "https://satoris.ai/amplifai/desktop-update/v1"
RELEASE_PREFIX = "https://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/"
FEEDS = {
    "macos-arm64": ("macos", "arm64", "ai.satoris.amplifai.phone.candidate", "/phone/macos/appcast.xml", "26101324"),
    "macos-intel-test": ("macos", "x86_64", "ai.satoris.amplifai.phone.intel-candidate", "/phone/macos/intel-appcast.xml", "261013241"),
    "windows-x64-test": ("windows", "x86_64", None, "/phone/windows/update-v1.json", "26101317"),
}
MAX_FEED = 65536
MAX_MANIFEST = 16384
MAX_INSTALLER = 256 * 1024 * 1024
WINDOWS_FIELDS = {
    "schema", "platform", "architecture", "channel", "version", "build", "installerVersion",
    "installerUrl", "installerBytes", "installerSha256", "installerEdSignature", "installerTrust",
}
ET.register_namespace("sparkle", SPARKLE)
ET.register_namespace("amplifai", IDENTITY)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def exact_json(data: bytes) -> dict:
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    if not 0 < len(data) <= MAX_FEED:
        raise ValueError("JSON exceeds byte limit")
    result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    if not isinstance(result, dict):
        raise ValueError("JSON object required")
    return result


def build_number(value: object) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,12}", value):
        raise ValueError("invalid build")
    return int(value)


def release_url(value: object, filename: str | None = None) -> str:
    if not isinstance(value, str) or not value.startswith(RELEASE_PREFIX):
        raise ValueError("installer URL must be an immutable official release asset")
    parsed = urlsplit(value)
    rest = value[len(RELEASE_PREFIX):]
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment
            or not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", rest)
            or any(part in (".", "..") for part in rest.split("/"))):
        raise ValueError("invalid immutable asset URL")
    if filename is not None and rest.split("/")[-1] != filename:
        raise ValueError("asset URL filename differs from exact package")
    return value


def signature_bytes(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("signature must be base64")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("invalid base64 signature") from error
    if len(decoded) != 64 or base64.b64encode(decoded).decode() != value:
        raise ValueError("invalid signature length or encoding")
    return decoded


def run(command: list[str], *, timeout: int = 120) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise ValueError("tool timed out; signing may require human Keychain interaction") from error
    if result.returncode:
        # Only public-input tools are accepted here; never pass a private key option.
        raise ValueError(f"{Path(command[0]).name} failed: {(result.stderr or result.stdout).strip()[:1000]}")
    return result.stdout.strip()


def verify_signature(data: bytes, signature: str, public_key: str = PUBLIC_KEY,
                     openssl: str = "openssl") -> None:
    decoded = signature_bytes(signature)
    try:
        public = base64.b64decode(public_key, validate=True)
    except ValueError as error:
        raise ValueError("invalid public key") from error
    if len(public) != 32:
        raise ValueError("invalid public key length")
    with tempfile.TemporaryDirectory(prefix="amplifai-public-verify-") as folder:
        root = Path(folder)
        (root / "public.der").write_bytes(bytes.fromhex("302a300506032b6570032100") + public)
        (root / "data").write_bytes(data)
        (root / "signature").write_bytes(decoded)
        run([openssl, "pkeyutl", "-verify", "-rawin", "-pubin", "-keyform", "DER",
             "-inkey", str(root / "public.der"), "-in", str(root / "data"),
             "-sigfile", str(root / "signature")], timeout=30)


def feed_content(data: bytes) -> tuple[bytes, str]:
    """Strict subset of Sparkle 2.10 SPUExtractSignedFeed.m, rejecting unsigned tails."""
    if not 0 < len(data) <= MAX_FEED:
        raise ValueError("feed exceeds byte limit")
    prefix = b"<!-- sparkle-signatures:\n"
    if data.count(prefix) != 1:
        raise ValueError("exactly one official Sparkle signing block required")
    content, block = data.split(prefix)
    match = re.fullmatch(rb"edSignature: ([A-Za-z0-9+/]{86}==)\nlength: ([0-9]{1,10})\n-->\n", block)
    if not match or int(match[2]) != len(content):
        raise ValueError("invalid Sparkle signature block or signed length")
    return content, match[1].decode("ascii")


def verified_xml(data: bytes, public_key: str = PUBLIC_KEY, openssl: str = "openssl") -> ET.Element:
    content, signature = feed_content(data)
    verify_signature(content, signature, public_key, openssl)
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("XML declarations are forbidden")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise ValueError("malformed appcast") from error
    if root.tag != "rss" or root.get("version") != "2.0" or len(root.findall("channel")) != 1:
        raise ValueError("invalid RSS feed")
    return root


def validate_appcast(data: bytes, channel: str, current_build: str,
                     public_key: str = PUBLIC_KEY, openssl: str = "openssl") -> dict:
    if channel not in FEEDS or FEEDS[channel][0] != "macos":
        raise ValueError("invalid Mac channel")
    root = verified_xml(data, public_key, openssl)
    items = root.findall("channel/item")
    if len(items) != 1:
        raise ValueError("one qualified update item required")
    item = items[0]
    def field(namespace, name):
        nodes = item.findall(f"{{{namespace}}}{name}")
        if len(nodes) != 1 or not nodes[0].text:
            raise ValueError(f"missing or duplicate {name}")
        return nodes[0].text
    platform, architecture, bundle, _, _ = FEEDS[channel]
    build = field(SPARKLE, "version")
    if build_number(build) <= build_number(current_build):
        raise ValueError("update build must be greater than installed build")
    version = field(SPARKLE, "shortVersionString")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", version):
        raise ValueError("invalid version label")
    sparkle_channels = item.findall(f"{{{SPARKLE}}}channel")
    expected_channels = [] if channel == "macos-arm64" else [channel]
    if ([node.text for node in sparkle_channels] != expected_channels
            or field(IDENTITY, "channel") != channel
            or field(SPARKLE, "hardwareRequirements") != architecture):
        raise ValueError("wrong channel or architecture")
    if field(IDENTITY, "bundleId") != bundle or field(IDENTITY, "platform") != platform:
        raise ValueError("wrong bundle or platform")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", field(SPARKLE, "minimumSystemVersion")):
        raise ValueError("invalid OS constraint")
    for key, pattern in (("sourceSha256", r"[0-9a-f]{64}"), ("sourceBaseCommit", r"[0-9a-f]{40}")):
        if not re.fullmatch(pattern, field(IDENTITY, key)):
            raise ValueError("invalid source identity")
    enclosures = item.findall("enclosure")
    if len(enclosures) != 1:
        raise ValueError("one enclosure required")
    enclosure = enclosures[0]
    release_url(enclosure.get("url"))
    signature_bytes(enclosure.get(f"{{{SPARKLE}}}edSignature"))
    if not re.fullmatch(r"[0-9]{1,12}", enclosure.get("length", "")) or int(enclosure.get("length")) <= 0:
        raise ValueError("invalid enclosure length")
    return {"channel": channel, "architecture": architecture, "bundleId": bundle,
            "build": build, "version": version, "installerUrl": enclosure.get("url"),
            "installerBytes": int(enclosure.get("length")),
            "installerEdSignature": enclosure.get(f"{{{SPARKLE}}}edSignature")}


def validate_windows(data: bytes, signature: str, current_build: str,
                     public_key: str = PUBLIC_KEY, openssl: str = "openssl") -> dict:
    if not 0 < len(data) <= MAX_MANIFEST:
        raise ValueError("manifest exceeds byte limit")
    verify_signature(data, signature, public_key, openssl)  # Authenticate exact bytes before parsing.
    item = exact_json(data)
    if set(item) != WINDOWS_FIELDS or type(item.get("schema")) is not int or item["schema"] != 1:
        raise ValueError("invalid manifest schema")
    if (item["platform"], item["architecture"], item["channel"]) != ("windows", "x86_64", "windows-x64-test"):
        raise ValueError("wrong platform, architecture or channel")
    if build_number(item["build"]) <= build_number(current_build):
        raise ValueError("update build must be greater than installed build")
    if not isinstance(item["version"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,63}", item["version"]):
        raise ValueError("invalid version label")
    if not isinstance(item["installerVersion"], str) or not re.fullmatch(r"[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,5}", item["installerVersion"]):
        raise ValueError("invalid MSI version")
    if any(value > limit for value, limit in zip(map(int, item["installerVersion"].split(".")), (255, 255, 65535))):
        raise ValueError("MSI version exceeds Windows Installer limits")
    if tuple(map(int, item["installerVersion"].split("."))) <= (1, 0, 17):
        raise ValueError("MSI version must exceed released version")
    release_url(item["installerUrl"])
    if not item["installerUrl"].endswith(".msi"):
        raise ValueError("MSI asset required")
    if type(item["installerBytes"]) is not int or not 0 < item["installerBytes"] <= MAX_INSTALLER:
        raise ValueError("invalid installer byte count")
    if not isinstance(item["installerSha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["installerSha256"]):
        raise ValueError("invalid installer hash")
    signature_bytes(item["installerEdSignature"])
    if item["installerTrust"] not in ("unsigned-test", "authenticode"):
        raise ValueError("invalid installer trust")
    return item


def verify_installer(path: Path, item: dict, public_key: str = PUBLIC_KEY,
                     openssl: str = "openssl") -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != item["installerBytes"]:
        raise ValueError("installer byte count mismatch")
    if not 0 < path.stat().st_size <= MAX_INSTALLER or sha256(path) != item["installerSha256"]:
        raise ValueError("installer hash or size mismatch")
    verify_signature(path.read_bytes(), item["installerEdSignature"], public_key, openssl)


def check_asset(path: Path, record: dict) -> None:
    if (path.is_symlink() or not path.is_file() or set(record) != {"name", "bytes", "sha256"}
            or path.name != record["name"] or type(record["bytes"]) is not int
            or path.stat().st_size != record["bytes"] or sha256(path) != record["sha256"]):
        raise ValueError("exact asset does not match release descriptor")


def check_descriptor(spec: dict, package: Path, source: Path, pins: dict) -> tuple:
    channel = spec.get("channel")
    if channel not in FEEDS or type(spec.get("schema")) is not int or spec.get("schema") != 1:
        raise ValueError("invalid release descriptor channel/schema")
    identity = FEEDS[channel]
    planned = pins["plannedBuilds"][channel]
    if (spec.get("platform"), spec.get("architecture")) != identity[:2]:
        raise ValueError("wrong release architecture/platform")
    for key in ("version", "build", "bundleId", "installerVersion"):
        if spec.get(key) != planned.get(key):
            raise ValueError(f"release {key} differs from approved source pins")
    if identity[0] == "windows" and spec.get("installerTrust") != "unsigned-test":
        raise ValueError("approved Windows Test scope requires unsigned-test trust")
    if build_number(spec["build"]) <= build_number(identity[4]):
        raise ValueError("release build must exceed baseline")
    prefix = "windows" if identity[0] == "windows" else "macos"
    archive_key = prefix + ("SourceArchiveSha256" if prefix == "windows" else ("Arm" if identity[1] == "arm64" else "Intel") + "SourceArchiveSha256")
    if spec.get("sourceBaseCommit") != pins[prefix + "BaseCommit"] or spec.get("baselineSourceArchiveSha256") != pins[archive_key]:
        raise ValueError("wrong pinned baseline source identity")
    check_asset(package, spec.get("package", {}))
    check_asset(source, spec.get("source", {}))
    if not 0 < package.stat().st_size <= MAX_INSTALLER:
        raise ValueError("package exceeds approved byte limit")
    release_url(spec.get("installerUrl"), package.name)
    return identity


def check_mac_archive(package: Path, spec: dict) -> None:
    def inspect(info: dict, executable: Path):
        expected = {"CFBundleIdentifier": spec["bundleId"], "CFBundleVersion": spec["build"],
                    "CFBundleShortVersionString": spec["version"], "SUPublicEDKey": PUBLIC_KEY,
                    "SUFeedURL": "https://amplifai-database-engine.vercel.app" + FEEDS[spec["channel"]][3],
                    "SURequireSignedFeed": True, "SUVerifyUpdateBeforeExtraction": True,
                    "SUSignedFeedFailureExpirationInterval": 0}
        if any(info.get(key) != value for key, value in expected.items()):
            raise ValueError("archive app identity or signed-feed settings mismatch")
        if run(["lipo", "-archs", str(executable)]).split() != [spec["architecture"]]:
            raise ValueError("archive CPU differs from channel")
        if info.get("LSMinimumSystemVersion") != "14.0":
            raise ValueError("archive OS minimum differs from approved release")
    if package.suffix == ".zip":
        with zipfile.ZipFile(package) as archive, tempfile.TemporaryDirectory(prefix="amplifai-executable-") as folder:
            names = [name for name in archive.namelist() if name.endswith(".app/Contents/Info.plist") and name.count(".app/") == 1]
            if len(names) != 1:
                raise ValueError("one main app required in ZIP")
            if archive.getinfo(names[0]).file_size > MAX_FEED:
                raise ValueError("oversized bundle metadata")
            info = plistlib.loads(archive.read(names[0]))
            executable_name = info.get("CFBundleExecutable", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", executable_name):
                raise ValueError("invalid bundle executable")
            entry = names[0].removesuffix("Info.plist") + "MacOS/" + executable_name
            if archive.getinfo(entry).file_size > 64 * 1024 * 1024:
                raise ValueError("oversized main executable")
            executable = Path(folder) / "main"
            executable.write_bytes(archive.read(entry))
            inspect(info, executable)
    elif package.suffix == ".dmg":
        with tempfile.TemporaryDirectory(prefix="amplifai-readonly-dmg-") as folder:
            mount = Path(folder) / "mounted"
            run(["hdiutil", "attach", "-readonly", "-nobrowse", "-noautoopen", "-mountpoint", str(mount), str(package)])
            try:
                apps = list(mount.glob("*.app"))
                if len(apps) != 1:
                    raise ValueError("one app required in DMG")
                metadata = apps[0] / "Contents/Info.plist"
                if apps[0].is_symlink() or metadata.is_symlink() or metadata.stat().st_size > MAX_FEED:
                    raise ValueError("linked app or oversized bundle metadata")
                info = plistlib.loads(metadata.read_bytes())
                executable_name = info.get("CFBundleExecutable", "")
                if not re.fullmatch(r"[A-Za-z0-9_-]+", executable_name):
                    raise ValueError("invalid bundle executable")
                executable = apps[0] / "Contents/MacOS" / executable_name
                if executable.is_symlink() or not executable.resolve().is_relative_to(mount.resolve()):
                    raise ValueError("main executable escapes mounted package")
                inspect(info, executable)
            finally:
                run(["hdiutil", "detach", str(mount)])
    else:
        raise ValueError("qualified Mac package must be ZIP or DMG")


class SparkleSigner:
    def __init__(self, directory: Path):
        self.directory = directory
        info = plistlib.loads((directory / "Sparkle.framework/Versions/B/Resources/Info.plist").read_bytes())
        if info.get("CFBundleShortVersionString") != "2.10.0":
            raise ValueError("pinned Sparkle 2.10.0 tools required")
        if sha256(directory / "bin/sign_update") != SIGN_UPDATE_SHA256:
            raise ValueError("sign_update differs from verified official 2.10.0 archive")

    def sign(self, path: Path) -> str:
        return run([str(self.directory / "bin/sign_update"), "--account", KEYCHAIN_ACCOUNT,
                    "--disable-signing-warning", "-p", str(path)], timeout=30)

def canonical_mac(spec: dict, signature: str) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "AMPLIFai Phone " + spec["channel"]
    ET.SubElement(channel, "link").text = "https://amplifai.satoris.ai/phone"
    item = ET.SubElement(channel, "item")
    ET.SubElement(item, "title").text = "AMPLIFai Phone " + spec["version"]
    for key, value in (("version", spec["build"]), ("shortVersionString", spec["version"]),
                       ("hardwareRequirements", spec["architecture"]),
                       ("minimumSystemVersion", "14.0.0")):
        ET.SubElement(item, f"{{{SPARKLE}}}{key}").text = value
    # The released ARM host only accepts Sparkle's default channel. A named
    # Sparkle channel would hide this migration update from existing installs.
    if spec["channel"] != "macos-arm64":
        ET.SubElement(item, f"{{{SPARKLE}}}channel").text = spec["channel"]
    for key, value in (("bundleId", spec["bundleId"]), ("platform", "macos"),
                       ("channel", spec["channel"]),
                       ("sourceSha256", spec["source"]["sha256"]), ("sourceBaseCommit", spec["sourceBaseCommit"])):
        ET.SubElement(item, f"{{{IDENTITY}}}{key}").text = value
    ET.SubElement(item, "enclosure", {"url": spec["installerUrl"], "length": str(spec["package"]["bytes"]),
                  "type": "application/octet-stream", f"{{{SPARKLE}}}edSignature": signature})
    ET.indent(rss, space="  ")
    return ET.tostring(rss, encoding="utf-8", xml_declaration=True) + b"\n"


def prepare(spec: dict, package: Path, source: Path, output: Path, signer: SparkleSigner,
            pins: dict, previous: Path | None = None, openssl: str = "openssl") -> dict:
    identity = check_descriptor(spec, package, source, pins)
    if output.exists():
        raise ValueError("new output directory required; previous outputs are immutable")
    if identity[0] == "macos":
        check_mac_archive(package, spec)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".desktop-update-", dir=output.parent) as folder:
        stage = Path(folder)
        feed = stage / Path(identity[3]).name
        if identity[0] == "macos":
            signature = signer.sign(package)
            verify_signature(package.read_bytes(), signature, openssl=openssl)
            feed.write_bytes(canonical_mac(spec, signature))
            signer.sign(feed)  # Official Sparkle writes its own signature block.
            parsed = validate_appcast(feed.read_bytes(), spec["channel"], identity[4], openssl=openssl)
        else:
            if package.stat().st_size > MAX_INSTALLER or package.suffix != ".msi":
                raise ValueError("Windows package exceeds limit or is not MSI")
            signature = signer.sign(package)
            verify_signature(package.read_bytes(), signature, openssl=openssl)
            manifest = {key: spec[key] for key in ("platform", "architecture", "channel", "version", "build", "installerVersion", "installerUrl", "installerTrust")}
            manifest.update(schema=1, installerBytes=spec["package"]["bytes"],
                            installerSha256=spec["package"]["sha256"], installerEdSignature=signature)
            feed.write_bytes(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8") + b"\n")
            detached = signer.sign(feed)
            (stage / (feed.name + ".sig")).write_text(detached + "\n", encoding="ascii")
            parsed = validate_windows(feed.read_bytes(), detached, identity[4], openssl=openssl)
            verify_installer(package, parsed, openssl=openssl)
        rollback = None
        if previous:
            if previous.is_symlink() or not previous.is_file() or previous.stat().st_size > MAX_FEED:
                raise ValueError("invalid previous feed")
            rollback_dir = stage / "rollback"
            rollback_dir.mkdir()
            shutil.copyfile(previous, rollback_dir / previous.name)
            rollback = {"name": previous.name, "sha256": sha256(previous), "bytes": previous.stat().st_size}
            if identity[0] == "windows":
                previous_signature = previous.with_name(previous.name + ".sig")
                if previous_signature.exists():
                    if previous_signature.is_symlink() or not 0 < previous_signature.stat().st_size <= 128:
                        raise ValueError("invalid previous detached signature")
                    shutil.copyfile(previous_signature, rollback_dir / previous_signature.name)
                    rollback["signatureSha256"] = sha256(previous_signature)
        receipt = {"schema": 1, "status": "locally-prepared-not-published", "channel": spec["channel"],
                   "publicKey": PUBLIC_KEY, "feedPath": identity[3], "package": spec["package"],
                   "source": spec["source"], "sourceBaseCommit": spec["sourceBaseCommit"],
                   "baselineSourceArchiveSha256": spec["baselineSourceArchiveSha256"],
                   "feedSha256": sha256(feed), "feedBytes": feed.stat().st_size,
                   "parsed": parsed, "rollback": rollback}
        (stage / "release-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        # Write the complete receipt last so partial output cannot look complete.
        check_asset(package, spec["package"])
        check_asset(source, spec["source"])
        deliverables = [feed.name, feed.name + ".sig", "rollback", "release-receipt.json"]
        output.mkdir()
        for name in deliverables:
            path = stage / name
            if path.is_dir():
                shutil.copytree(path, output / name)
            elif path.exists():
                shutil.copyfile(path, output / name)
        return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sparkle-tools", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous-feed", type=Path)
    parser.add_argument("--openssl", default="openssl")
    args = parser.parse_args()
    try:
        result = prepare(exact_json(args.spec.read_bytes()), args.package, args.source, args.output,
                         SparkleSigner(args.sparkle_tools), exact_json(PINS.read_bytes()), args.previous_feed, args.openssl)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f"release preparation rejected: {error}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
