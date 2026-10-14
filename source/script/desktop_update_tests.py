"""Offline signature and release-boundary tests; no production Keychain access.

The signing seed below is the public RFC 8032 test vector, never a product key.
It is written only into a temporary test directory and never printed.
"""
from __future__ import annotations

import base64
import copy
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import desktop_update_release as release

TEST_PUBLIC = base64.b64encode(bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")).decode()
TEST_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")


class UpdateReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.openssl = shutil.which("openssl")
        if not cls.openssl:
            raise unittest.SkipTest("existing OpenSSL with Ed25519 support required")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="desktop-update-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.key = self.root / "rfc8032-test-key.der"
        self.key.write_bytes(bytes.fromhex("302e020100300506032b657004220420") + TEST_SEED)
        self.pins = json.loads(release.PINS.read_text())

    def sign(self, data):
        (self.root / "message").write_bytes(data)
        subprocess.run([self.openssl, "pkeyutl", "-sign", "-rawin", "-keyform", "DER",
                        "-inkey", str(self.key), "-in", str(self.root / "message"),
                        "-out", str(self.root / "signature")], check=True, capture_output=True)
        return base64.b64encode((self.root / "signature").read_bytes()).decode()

    def manifest(self):
        return {"schema": 1, "platform": "windows", "architecture": "x86_64", "channel": "windows-x64-test",
                "version": "2026.10.14-rc18", "build": "26101418", "installerVersion": "1.0.18",
                "installerUrl": release.RELEASE_PREFIX + "windows-rc18-fixture/test.msi", "installerBytes": 42,
                "installerSha256": "0" * 64, "installerEdSignature": self.sign(b"fixture installer"),
                "installerTrust": "unsigned-test"}

    def windows(self, item=None, current="26101317"):
        data = json.dumps(item or self.manifest(), sort_keys=True, separators=(",", ":")).encode() + b"\n"
        return release.validate_windows(data, self.sign(data), current, TEST_PUBLIC, self.openssl)

    def spec(self, channel="macos-arm64"):
        platform, architecture, bundle, _, _ = release.FEEDS[channel]
        result = {"schema": 1, "platform": platform, "architecture": architecture, "channel": channel,
                  **self.pins["plannedBuilds"][channel], "installerUrl": release.RELEASE_PREFIX + "fixture/test.zip",
                  "sourceBaseCommit": self.pins["macosBaseCommit"],
                  "baselineSourceArchiveSha256": self.pins["macosArmSourceArchiveSha256" if architecture == "arm64" else "macosIntelSourceArchiveSha256"],
                  "package": {"name": "test.zip", "bytes": 42, "sha256": "1" * 64},
                  "source": {"name": "test-source.zip", "bytes": 43, "sha256": "2" * 64}}
        return result

    def signed_feed(self, spec=None):
        content = release.canonical_mac(spec or self.spec(), self.sign(b"fixture package"))
        return content + f"<!-- sparkle-signatures:\nedSignature: {self.sign(content)}\nlength: {len(content)}\n-->\n".encode()

    def test_exact_windows_bytes_and_signature(self):
        self.assertEqual(self.windows()["build"], "26101418")
        data = json.dumps(self.manifest()).encode()
        signature = self.sign(data)
        with self.assertRaisesRegex(ValueError, "failed"):
            release.validate_windows(data + b" ", signature, "26101317", TEST_PUBLIC, self.openssl)

    def test_exact_installer_signature_hash_and_size(self):
        path = self.root / "fixture.msi"; path.write_bytes(b"fixture installer")
        item = self.manifest()
        item.update(installerBytes=path.stat().st_size, installerSha256=release.sha256(path),
                    installerEdSignature=self.sign(path.read_bytes()))
        release.verify_installer(path, item, TEST_PUBLIC, self.openssl)
        for key, value in (("installerBytes", 0), ("installerSha256", "0" * 64),
                           ("installerEdSignature", self.sign(b"other installer"))):
            changed = dict(item); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                release.verify_installer(path, changed, TEST_PUBLIC, self.openssl)

    def test_authenticate_before_json_parse(self):
        with patch.object(release, "exact_json", side_effect=AssertionError("parser reached")):
            with self.assertRaises(ValueError):
                release.validate_windows(b"not JSON", self.sign(b"different bytes"), "26101317", TEST_PUBLIC, self.openssl)

    def test_windows_rejects_wrong_arch_channel_and_platform(self):
        for key, value in (("architecture", "arm64"), ("channel", "macos-arm64"), ("platform", "macos")):
            item = self.manifest(); item[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "wrong"):
                self.windows(item)

    def test_wrong_or_nonincreasing_build_and_msi(self):
        for key, value in (("build", "26101317"), ("build", "26101316"), ("build", 26101418),
                           ("build", "bad"), ("installerVersion", "1.0.17"), ("installerVersion", "1.0.65536")):
            item = self.manifest(); item[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.windows(item)

    def test_strict_manifest_schema_types_and_limits(self):
        for key, value in (("schema", True), ("installerBytes", True), ("installerBytes", 0),
                           ("installerBytes", release.MAX_INSTALLER + 1), ("installerSha256", "zz"),
                           ("installerEdSignature", "bad"), ("installerTrust", "trusted")):
            item = self.manifest(); item[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.windows(item)
        item = self.manifest(); item["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "schema"):
            self.windows(item)
        with self.assertRaisesRegex(ValueError, "byte limit"):
            release.validate_windows(b"x" * (release.MAX_MANIFEST + 1), self.sign(b"data"), "0", TEST_PUBLIC, self.openssl)

    def test_duplicate_keys_and_utf8(self):
        data = b'{"schema":1,"schema":1}'
        with self.assertRaisesRegex(ValueError, "duplicate"):
            release.validate_windows(data, self.sign(data), "0", TEST_PUBLIC, self.openssl)
        with self.assertRaises(ValueError):
            release.exact_json(b'\xff')

    def test_asset_url_boundary(self):
        urls = ["http://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/v/a.msi",
                release.RELEASE_PREFIX + "../a.msi", release.RELEASE_PREFIX + "v/%2e%2e.msi",
                release.RELEASE_PREFIX + "v/a.msi?x=1", release.RELEASE_PREFIX + "v/a.msi#x",
                release.RELEASE_PREFIX + "v/a.msi/b", release.RELEASE_PREFIX.replace("github.com", "github.com.evil") + "v/a.msi"]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                release.release_url(url)

    def test_canonical_mac_determinism_and_channels(self):
        for channel in ("macos-arm64", "macos-intel-test"):
            spec = self.spec(channel)
            self.assertEqual(release.canonical_mac(spec, self.sign(b"package")), release.canonical_mac(spec, self.sign(b"package")))
            item = release.validate_appcast(self.signed_feed(spec), channel, release.FEEDS[channel][4], TEST_PUBLIC, self.openssl)
            self.assertEqual(item["architecture"], release.FEEDS[channel][1])
            root = release.verified_xml(self.signed_feed(spec), TEST_PUBLIC, self.openssl)
            channels = root.findall(f"channel/item/{{{release.SPARKLE}}}channel")
            self.assertEqual([node.text for node in channels], [] if channel == "macos-arm64" else [channel])

    def test_mac_tamper_wrong_cpu_channel_bundle_and_build(self):
        data = self.signed_feed()
        with self.assertRaises(ValueError):
            release.validate_appcast(data.replace(b"2026.10.14", b"2026.10.15"), "macos-arm64", "26101324", TEST_PUBLIC, self.openssl)
        for key, value in (("architecture", "x86_64"), ("channel", "macos-intel-test"),
                           ("bundleId", "ai.wrong"), ("build", "26101324")):
            spec = self.spec(); spec[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                release.validate_appcast(self.signed_feed(spec), "macos-arm64", "26101324", TEST_PUBLIC, self.openssl)

    def test_signature_block_boundaries(self):
        data = self.signed_feed()
        for changed in (data + b"tail", data.replace(b"length: ", b"length: 9"), data + data,
                        data.replace(b"edSignature: ", b"edSignature: \n"), data.split(b"<!-- sparkle-signatures:")[0]):
            with self.subTest(length=len(changed)), self.assertRaises(ValueError):
                release.feed_content(changed)

    def test_wrong_public_key_and_signature_encoding(self):
        signature = self.sign(b"data")
        with self.assertRaises(ValueError):
            release.verify_signature(b"data", signature, release.PUBLIC_KEY, self.openssl)
        for signature in ("", "AAAA", signature + "\n", signature.rstrip("=")):
            with self.assertRaises(ValueError):
                release.signature_bytes(signature)

    def test_descriptor_exact_source_asset_and_pinned_identity(self):
        spec = self.spec()
        package = self.root / "test.zip"; package.write_bytes(b"package")
        source = self.root / "test-source.zip"; source.write_bytes(b"source")
        for path, key in ((package, "package"), (source, "source")):
            spec[key] = {"name": path.name, "bytes": path.stat().st_size, "sha256": release.sha256(path)}
        release.check_descriptor(spec, package, source, self.pins)
        for key, value in (("sourceBaseCommit", "0" * 40), ("baselineSourceArchiveSha256", "0" * 64),
                           ("architecture", "x86_64"), ("build", "26101324"), ("channel", "windows-x64-test"), ("schema", True)):
            changed = copy.deepcopy(spec); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                release.check_descriptor(changed, package, source, self.pins)
        source.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "exact asset"):
            release.check_descriptor(spec, package, source, self.pins)

    def test_mac_archive_bounds_metadata_before_parse(self):
        package = self.root / "oversized.zip"
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Fixture.app/Contents/Info.plist", b"x" * (release.MAX_FEED + 1))
        with self.assertRaisesRegex(ValueError, "oversized bundle metadata"):
            release.check_mac_archive(package, self.spec())

    def test_windows_preparation_preserves_exact_rollback_and_output(self):
        package = self.root / "fixture.msi"; package.write_bytes(b"synthetic installer")
        source = self.root / "fixture-source.zip"; source.write_bytes(b"synthetic source")
        previous = self.root / "update-v1.json"; previous.write_bytes(b"retained original feed bytes\n")
        previous.with_name(previous.name + ".sig").write_bytes(b"retained original sidecar\n")
        def record(path):
            return {"name": path.name, "bytes": path.stat().st_size, "sha256": release.sha256(path)}
        spec = {"schema": 1, "platform": "windows", "architecture": "x86_64", "channel": "windows-x64-test",
                **self.pins["plannedBuilds"]["windows-x64-test"], "installerTrust": "unsigned-test",
                "installerUrl": release.RELEASE_PREFIX + "fixture-only/fixture.msi", "package": record(package),
                "source": record(source), "sourceBaseCommit": self.pins["windowsBaseCommit"],
                "baselineSourceArchiveSha256": self.pins["windowsSourceArchiveSha256"]}
        class TestSigner:
            def sign(inner, path):
                return self.sign(path.read_bytes())
        original = release.verify_signature
        def test_verify(data, signature, public_key=TEST_PUBLIC, openssl=self.openssl):
            return original(data, signature, TEST_PUBLIC, self.openssl)
        output = self.root / "prepared"
        with patch.object(release, "verify_signature", side_effect=test_verify):
            receipt = release.prepare(spec, package, source, output, TestSigner(), self.pins, previous)
            self.assertEqual((output / "rollback/update-v1.json").read_bytes(), previous.read_bytes())
            self.assertEqual((output / "rollback/update-v1.json.sig").read_bytes(), previous.with_name(previous.name + ".sig").read_bytes())
            self.assertEqual(receipt["rollback"]["sha256"], release.sha256(previous))
            before = (output / "release-receipt.json").read_bytes()
            with self.assertRaisesRegex(ValueError, "immutable"):
                release.prepare(spec, package, source, output, TestSigner(), self.pins, previous)
            self.assertEqual((output / "release-receipt.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
