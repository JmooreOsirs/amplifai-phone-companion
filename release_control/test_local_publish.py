"""Local publication keeps remote failure, provenance and byte boundaries explicit."""
import copy
import hashlib
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import Mock, patch

import publish
import release


class LocalPublicationTests(unittest.TestCase):
    def fixture(self):
        source = {"sourceCommit": "a" * 40, "channel": "macos-arm64", "version": "2026.10.16-rc28", "build": "26101628"}
        request = {"sourceCommit": source["sourceCommit"], "runId": 11, "artifactId": 22,
                   "artifactSha256": "b" * 64, "channel": source["channel"]}
        provenance = {"kind": "local-keychain", "controlCommit": "c" * 40, "operator": "JmooreOsirs",
                      "executionId": "552ce9bc-c2b3-41d9-9298-34b85dcdb5cb",
                      "qualification": {"runId": 11, "artifactId": 22, "sha256": "b" * 64,
                                        "sourceCommit": source["sourceCommit"]}}
        signed = {"request": request, "controlCommit": "c" * 40, "operator": "JmooreOsirs",
                  "localProvenance": copy.deepcopy(provenance), "signing": {"feed": {
                      "feedSha256": "d" * 64, "feedBytes": 100,
                      "rollback": {"name": "previous-appcast.xml", "bytes": 90, "sha256": "e" * 64}}}}
        spec = {"package": {"name": "app.dmg", "bytes": 10, "sha256": "f" * 64},
                "source": {"name": "source.zip", "bytes": 20, "sha256": "1" * 64}, "installerUrl": "https://example.invalid/app.dmg"}
        published = {"releaseUrl": "https://github.com/JmooreOsirs/amplifai-phone-companion/releases/tag/test", "assets": [
            {"name": "appcast.xml", "bytes": 100, "sha256": "d" * 64},
            {"name": "local-signing-manifest.json", "bytes": 700, "sha256": "2" * 64},
            {"name": "local-signing-manifest.json.sig", "bytes": 89, "sha256": "3" * 64},
            {"name": "local-signed-delivery.zip", "bytes": 900, "sha256": "4" * 64}]}
        return source, spec, signed, published, provenance

    def test_local_handoff_has_real_qualification_and_public_manifest_without_hosted_signer_ids(self):
        source, spec, signed, published, provenance = self.fixture()
        handoff = publish.website_handoff(source, spec, signed, published, local_provenance=provenance)
        self.assertNotIn("signedArtifact", handoff)
        self.assertEqual(handoff["executionMode"], "local-keychain")
        self.assertEqual(handoff["localSigning"]["qualification"]["runId"], 11)
        self.assertEqual(handoff["localSigningEvidence"]["manifest"]["sha256"], "2" * 64)
        self.assertEqual(handoff["localSigningEvidence"]["delivery"]["name"], "local-signed-delivery.zip")
        self.assertFalse(handoff["websitePromoted"])

    def test_local_handoff_rejects_tampered_or_synthetic_hosted_provenance(self):
        for change in ({"controlCommit": "f" * 40}, {"operator": "other"}, {"runId": 500},
                       {"qualification": {"runId": 12}}, {"kind": "workflow_dispatch"}):
            source, spec, signed, published, provenance = self.fixture()
            provenance.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                publish.website_handoff(source, spec, signed, published, local_provenance=provenance)

    def test_local_handoff_requires_public_manifest_and_mutually_exclusive_context(self):
        source, spec, signed, published, provenance = self.fixture()
        published["assets"].pop()
        with self.assertRaisesRegex(ValueError, "manifest and signature"):
            publish.website_handoff(source, spec, signed, published, local_provenance=provenance)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            publish.website_handoff(source, spec, signed, published, {}, local_provenance=provenance)

    def test_local_remote_denial_is_not_absence(self):
        client = Mock()
        client.get_optional.side_effect = ValueError("remote access denied")
        with self.assertRaisesRegex(ValueError, "remote access denied"):
            publish.absent_release_and_tag("fixture/repo", "v1", github=client)
        client.get_optional.side_effect = [None, {"ref": "refs/tags/v1"}]
        with self.assertRaisesRegex(ValueError, "already exists"):
            publish.absent_release_and_tag("fixture/repo", "v1", github=client)

    def test_local_exact_tag_creation_rejects_remote_wrong_source(self):
        client = Mock()
        client.request_json.return_value = {"ref": "refs/tags/v1", "object": {"sha": "f" * 40, "type": "commit"}}
        with self.assertRaises(ValueError):
            publish.create_exact_tag("fixture/repo", "v1", "a" * 40, github=client)
        self.assertEqual(client.request_json.call_args.kwargs["payload"]["sha"], "a" * 40)

    def test_local_order_inventory_is_bounded_and_requires_real_list(self):
        client = Mock()
        client.request_json.return_value = {"message": "denied"}
        with self.assertRaises(ValueError):
            publish.release_snapshot("fixture/repo", github=client)
        client.request_json.return_value = [{"tag_name": "v1"}] * 100
        with self.assertRaisesRegex(ValueError, "bounded"):
            publish.release_snapshot("fixture/repo", github=client)

    def test_mounted_app_inventory_is_verified_and_image_detaches_on_tamper(self):
        for tampered, extra_mount in ((False, False), (True, False), (False, True)):
            with self.subTest(tampered=tampered, extra_mount=extra_mount), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source = self.fixture()[0]
                certificate = b"public certificate fixture"
                expected = {"files": [{"path": "Contents/MacOS/AmplifaiPhone", "bytes": 4,
                                       "sha256": hashlib.sha256(b"good").hexdigest()}], "symlinks": []}
                (root / "final-signed-app-inventory.json").write_text(json.dumps(expected))
                calls = []
                def command(args):
                    calls.append(list(map(str, args)))
                    if args[:2] == ["codesign", "--display"]:
                        self.assertEqual(len(args), 4)
                        self.assertTrue(args[2].startswith("--extract-certificates="))
                        prefix = args[2].split("=", 1)[1]
                        Path(str(prefix) + "0").write_bytes(certificate)
                    if args[:2] == ["hdiutil", "attach"]:
                        mount = Path(args[args.index("-mountpoint") + 1])
                        binary = mount / "AMPLIFai Phone.app/Contents/MacOS/AmplifaiPhone"
                        binary.parent.mkdir(parents=True)
                        binary.write_bytes(b"evil" if tampered else b"good")
                        entities = [
                            {"dev-entry": "/dev/disk900", "content-hint": "GUID_partition_scheme"},
                            {"dev-entry": "/dev/disk900s1", "content-hint": "Apple_APFS"},
                            {"dev-entry": "/dev/disk901s1", "volume-kind": "apfs", "mount-point": str(mount)},
                            {"dev-entry": "/dev/disk901", "content-hint": "EF57347C-0000-11AA-AA11-00306543ECAC"}]
                        if extra_mount:
                            entities.append({"dev-entry": "/dev/disk901s2", "mount-point": "/Volumes/unexpected"})
                        return plistlib.dumps({"system-entities": entities}).decode()
                    return ""
                with patch.object(release, "command", side_effect=command), \
                        patch.object(release, "CERTIFICATE_SHA1", hashlib.sha1(certificate).hexdigest().upper()), \
                        patch.object(release, "validate_app") as app_validation:
                    if extra_mount:
                        with self.assertRaisesRegex(ValueError, "single private verification"):
                            publish.verify_local_package(root, source, temp_root=root)
                    elif tampered:
                        with self.assertRaisesRegex(ValueError, "mounted app differs"):
                            publish.verify_local_package(root, source, temp_root=root)
                    else:
                        receipt = publish.verify_local_package(root, source, temp_root=root)
                        self.assertTrue(receipt["mountedInventoryMatched"])
                        self.assertFalse(receipt["targetCodeExecuted"])
                    if extra_mount:
                        app_validation.assert_not_called()
                    else:
                        app_validation.assert_called_once()
                self.assertEqual(calls[-1], ["hdiutil", "detach", "/dev/disk900"])
                self.assertFalse(list(root.glob("amplifai-local-verify-*")))
                self.assertTrue(all("--all-architectures" in call for call in calls if call[:2] == ["codesign", "--verify"]))

    def test_wrong_package_certificate_stops_before_mount(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            calls = []
            def command(args):
                calls.append(args)
                if args[:2] == ["codesign", "--display"]:
                    self.assertEqual(len(args), 4)
                    self.assertTrue(args[2].startswith("--extract-certificates="))
                    Path(args[2].split("=", 1)[1] + "0").write_bytes(b"wrong public certificate")
                return ""
            with patch.object(release, "command", side_effect=command):
                with self.assertRaisesRegex(ValueError, "certificate differs"):
                    publish.verify_local_package(root, self.fixture()[0], temp_root=root)
            self.assertFalse(any(call[:2] == ["hdiutil", "attach"] for call in calls))
            self.assertFalse(list(root.glob("amplifai-local-verify-*")))


if __name__ == "__main__":
    unittest.main()
