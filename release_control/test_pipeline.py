"""Filesystem and external-boundary evidence for producer/finalizer/publisher."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

import feeds
import finalize
import producer
import publish
import release


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = {"sourceCommit": "a" * 40, "channel": "macos-arm64", "version": "2026.10.16-rc28", "build": "26101628"}

    def fixture_source(self):
        checkout = self.root / "checkout"
        checkout.mkdir()
        (checkout / "example.py").write_text("VALUE = 1\n")
        subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], check=True)
        subprocess.run(["git", "-C", str(checkout), "add", "example.py"], check=True)
        subprocess.run(["git", "-C", str(checkout), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                        "-c", "user.name=Release tests", "-c", "user.email=release-tests@example.invalid", "commit", "-qm", "fixture"], check=True)
        commit = release.command(["git", "-C", checkout, "rev-parse", "HEAD"])
        app = self.root / "AMPLIFai Phone.app"
        legal = app / "Contents/Resources/Legal"
        legal.mkdir(parents=True)
        payload = {"authored/example.py": (checkout / "example.py").read_bytes(), "third-party/source.tar": b"source",
                   "third-party/NOTICE": b"notice"}
        mapping = {"schema": 1, "sourceCommit": commit, "files": [], "authored": {"example.py": "authored/example.py"},
                   "native": {"Contents/MacOS/AmplifaiPhone": {"preSignSha256": "b" * 64,
                       "sourceInputs": ["authored/example.py", "third-party/source.tar"], "noticeInputs": ["third-party/NOTICE"]}},
                   "legal": {}, "appendPaths": producer.APPEND_PATHS}
        for name in ("LICENSE", "COPYRIGHT", "SOURCE-README.md", "THIRD-PARTY-NOTICES.txt", "source-package-file-manifest.json"):
            data = ("fixture " + name).encode()
            (legal / name).write_bytes(data)
            payload["legal/" + name] = data
            mapping["legal"][name] = "legal/" + name
        mapping["files"] = [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                            for name, data in payload.items()]
        archive = self.root / "source.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            for name, data in payload.items():
                zipped.writestr(name, data)
            zipped.writestr("release-source-map.json", json.dumps(mapping))
        return checkout, app, archive, commit

    def test_source_payload_matches_real_git_checkout_and_native_legal_coverage(self):
        checkout, app, archive, commit = self.fixture_source()
        native = {"files": [{"path": "Contents/MacOS/AmplifaiPhone", "sha256": "b" * 64}]}
        receipt = producer.validate_source(archive, checkout, app, commit, native)
        self.assertEqual(receipt["authoredFiles"], 1)
        self.assertEqual(receipt["nativeFiles"], 1)
        (checkout / "example.py").write_text("VALUE = 2\n")
        with self.assertRaisesRegex(ValueError, "checkout changed"):
            producer.validate_source(archive, checkout, app, commit, native)

    def test_missing_actual_native_coverage_rejected(self):
        checkout, app, archive, commit = self.fixture_source()
        with self.assertRaisesRegex(ValueError, "native source mapping"):
            producer.validate_source(archive, checkout, app, commit, {"files": []})

    def test_finalization_preserves_payload_and_binds_post_signature_inventory(self):
        _, _, archive, commit = self.fixture_source()
        inventory = self.root / "signed.json"
        inventory.write_text(json.dumps({"files": [{"path": "Contents/MacOS/AmplifaiPhone", "sha256": "c" * 64, "bytes": 123}], "symlinks": []}))
        output = self.root / "final-source.zip"
        receipt = finalize.finalize_source(archive, output, inventory, commit)
        self.assertTrue(receipt["completeMemberReadback"])
        read = finalize.verify_final_source(output, commit)
        self.assertEqual(read["nativeBindings"][0]["postSignSha256"], "c" * 64)
        with zipfile.ZipFile(archive) as before, zipfile.ZipFile(output) as after:
            for name in before.namelist():
                self.assertEqual(before.read(name), after.read(name))
        corrupt = self.root / "corrupt.zip"
        with zipfile.ZipFile(output) as before, zipfile.ZipFile(corrupt, "w") as after:
            for item in before.infolist():
                after.writestr(item, b"tampered" if item.filename == "authored/example.py" else before.read(item))
        with self.assertRaisesRegex(ValueError, "readback differs"):
            finalize.verify_final_source(corrupt, commit)

    def test_producer_refuses_misconfigured_credential_bearing_job(self):
        with patch.dict(release.os.environ, {"SPARKLE_PRIVATE_KEY": "fixture-only"}):
            with self.assertRaisesRegex(ValueError, "must not receive product secrets"):
                producer.qualify(self.source, self.root, self.root / "absent.app", self.root / "absent.zip", self.root / "output", 1)

    def test_qualification_mutation_rejects_stale_source_mapping_before_artifact_output(self):
        checkout, app, original_zip, commit = self.fixture_source()
        binary = app / "Contents/MacOS/AmplifaiPhone"
        binary.parent.mkdir()
        binary.write_bytes(b"before qualification")
        source_zip = self.root / "mapped-source.zip"
        with zipfile.ZipFile(original_zip) as before, zipfile.ZipFile(source_zip, "w") as after:
            for name in before.namelist():
                data = before.read(name)
                if name == "release-source-map.json":
                    mapping = json.loads(data)
                    mapping["native"]["Contents/MacOS/AmplifaiPhone"]["preSignSha256"] = release.digest(binary)
                    data = json.dumps(mapping).encode()
                after.writestr(name, data)
        source = dict(self.source, sourceCommit=commit, qualificationProfile=release.PHONE_RUN_QUALIFICATION)
        actual_run = subprocess.run
        def qualification(args, **kwargs):
            if str(args[0]) == "git":
                return actual_run(args, **kwargs)
            if any("verify_frozen_companion_status.py" in str(arg) for arg in args):
                binary.write_bytes(b"mutated during executable qualification")
            return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")
        def audit(*args):
            return {"passed": True, "files": [{"path": "Contents/MacOS/AmplifaiPhone", "sha256": release.digest(binary)}]}
        with patch.object(release, "validate_app"), patch("verify_macos_architecture.audit", side_effect=audit), \
                patch.object(producer, "run_phone_run_checks"), \
                patch.object(subprocess, "run", side_effect=qualification):
            with self.assertRaisesRegex(ValueError, "mapping differs"):
                producer.qualify(source, checkout, app, source_zip, self.root / "delivery", 1)
        self.assertFalse((self.root / "delivery").exists())
        with patch("verify_macos_architecture.audit", side_effect=audit):
            with self.assertRaisesRegex(ValueError, "fresh signing-input bytes"):
                producer.validate_signing_input(source_zip, app, commit, source["channel"])

    def test_signer_artifact_requires_same_run_attempt_and_successful_job(self):
        env = {"SIGNED_ARTIFACT_ID": "22", "SIGNED_ARTIFACT_SHA256": "b" * 64, "GITHUB_RUN_ID": "11", "GITHUB_SHA": "c" * 40}
        artifact = {"id": 22, "digest": "sha256:" + "b" * 64, "expired": False, "size_in_bytes": 100,
                    "workflow_run": {"id": 11, "head_sha": "c" * 40}, "created_at": "2026-10-16T14:01:00Z"}
        jobs = {"jobs": [{"name": "macos-sign-and-notarize", "conclusion": "success", "started_at": "2026-10-16T14:00:00Z",
                           "completed_at": "2026-10-16T14:02:00Z"}]}
        publish.validate_signer_provenance(artifact, jobs, env)
        for change in ({"workflow_run": {"id": 12, "head_sha": "c" * 40}}, {"digest": "sha256:" + "d" * 64},
                       {"created_at": "2026-10-16T13:00:00Z"}):
            with self.assertRaises(ValueError):
                publish.validate_signer_provenance(dict(artifact, **change), jobs, env)

    def test_duplicate_conflicting_newer_and_concurrent_releases_rejected(self):
        publish.validate_release_order(self.source, [{"tag_name": "macos-2026.10.15-rc26-release", "draft": False}])
        for tag, draft in (("macos-2026.10.16-rc28-release", False), ("macos-2026.10.16-rc28-release", True),
                           ("macos-2026.10.16-rc29-release", False), ("macos-2026.10.16-rc29-release", True)):
            with self.subTest(tag=tag, draft=draft), self.assertRaises(ValueError):
                publish.validate_release_order(self.source, [{"tag_name": tag, "draft": draft}])
        own = feeds.names(self.source)["tag"]
        publish.validate_release_order(self.source, [{"tag_name": own, "draft": True}], own_draft=own)
        with self.assertRaises(ValueError):
            publish.validate_release_order(self.source, [{"tag_name": own, "draft": True},
                                                         {"tag_name": "macos-2026.10.16-rc29-release", "draft": False}], own_draft=own)

    def test_failed_remote_lookup_never_means_absent_release(self):
        with patch.dict(release.os.environ, {"GH_TOKEN": "fixture"}):
            with patch.object(publish.urllib.request, "urlopen", side_effect=urllib.error.HTTPError("https://api.github.com", 403, "denied", {}, None)):
                with self.assertRaises(ValueError):
                    publish.absent_release_and_tag("fixture/repo", "v1")

    def test_uploaded_byte_mismatch_stops_before_publication(self):
        package = self.root / "package.dmg"
        package.write_bytes(b"qualified-package")
        calls = []
        def gh(args, **kwargs):
            calls.append(args)
            if args[:3] == ["gh", "release", "download"]:
                destination = Path(args[args.index("--dir") + 1]) / package.name
                destination.write_bytes(b"tampered-upload")
            return ""
        output = self.root / "public"
        output.mkdir()
        with patch.object(publish, "release_snapshot", return_value=[]), patch.object(publish, "absent_release_and_tag"), \
                patch.object(publish, "verify_previous_feed"), patch.object(publish, "create_exact_tag"), \
                patch.object(publish, "verify_exact_tag"), patch.object(release, "command", side_effect=gh):
            with self.assertRaisesRegex(ValueError, "draft uploaded bytes differ"):
                publish.publish(self.source, [package], self.root, output, "fixture/repo")
        self.assertFalse(any(args[:3] == ["gh", "release", "edit"] for args in calls))

    def test_tag_created_in_race_or_moved_to_wrong_source_rejected(self):
        with patch.dict(release.os.environ, {"GH_TOKEN": "fixture"}), patch.object(publish.urllib.request, "urlopen",
                side_effect=urllib.error.HTTPError("https://api.github.com", 422, "exists", {}, None)):
            with self.assertRaisesRegex(ValueError, "atomic tag creation rejected"):
                publish.create_exact_tag("fixture/repo", "tag", self.source["sourceCommit"])
        publish.validate_tag({"ref": "refs/tags/tag", "object": {"sha": self.source["sourceCommit"], "type": "commit"}},
                             "tag", self.source["sourceCommit"])
        with self.assertRaises(ValueError):
            publish.validate_tag({"ref": "refs/tags/tag", "object": {"sha": "f" * 40, "type": "commit"}},
                                 "tag", self.source["sourceCommit"])

    def test_public_and_authenticated_redirects_keep_hash_binding_without_forwarding_token(self):
        class Body(io.BytesIO):
            def geturl(self):
                return "https://assets.example.invalid/package"
        blob = b"immutable artifact bytes"
        for authenticated in (False, True):
            error = urllib.error.HTTPError("https://api.github.com", 302, "redirect",
                                           {"Location": "https://assets.example.invalid/package"}, None)
            with patch.dict(release.os.environ, {"GH_TOKEN": "fixture-private-token"}), \
                    patch.object(release.urllib.request, "build_opener") as opener, \
                    patch.object(release.urllib.request, "urlopen", return_value=Body(blob)) as final:
                opener.return_value.open.side_effect = error
                release.download("https://api.github.com/fixture", self.root / (str(authenticated) + ".zip"),
                                 hashlib.sha256(blob).hexdigest(), len(blob), authenticated=authenticated)
                self.assertEqual(final.call_args.args[0], "https://assets.example.invalid/package")

    def test_publication_verifies_tag_and_draft_bytes_before_public_edit(self):
        for moved in (False, True):
            root = self.root / str(moved)
            root.mkdir()
            output = root / "public"
            output.mkdir()
            package = root / "package.dmg"
            package.write_bytes(b"qualified-package")
            calls = []
            def gh(args, **kwargs):
                calls.append(args)
                if args[:3] == ["gh", "release", "download"]:
                    (Path(args[args.index("--dir") + 1]) / package.name).write_bytes(package.read_bytes())
                return ""
            def public_readback(url, target, checksum, limit, **kwargs):
                target.write_bytes(package.read_bytes())
                self.assertEqual(release.digest(target), checksum)
            guard = [None, ValueError("tag moved")] if moved else [None, None]
            with patch.object(publish, "release_snapshot", return_value=[]), patch.object(publish, "absent_release_and_tag"), \
                    patch.object(publish, "verify_previous_feed"), patch.object(publish, "create_exact_tag"), \
                    patch.object(publish, "verify_exact_tag", side_effect=guard), patch.object(release, "command", side_effect=gh), \
                    patch.object(release, "download", side_effect=public_readback):
                if moved:
                    with self.assertRaisesRegex(ValueError, "tag moved"):
                        publish.publish(self.source, [package], root, output, "fixture/repo")
                else:
                    receipt = publish.publish(self.source, [package], root, output, "fixture/repo")
                    self.assertFalse(receipt["websitePromoted"])
                    self.assertEqual(receipt["status"], "github-published-website-promotion-pending")
            create = next(args for args in calls if args[:3] == ["gh", "release", "create"])
            self.assertIn("--verify-tag", create)
            self.assertNotIn("--target", create)
            for args in calls:
                if args[:2] == ["gh", "release"]:
                    self.assertEqual(args[args.index("--repo") + 1], "github.com/fixture/repo")
            self.assertEqual(any(args[:3] == ["gh", "release", "edit"] for args in calls), not moved)

    def test_website_packet_binds_exact_previous_feed_without_claiming_promotion(self):
        feed_sha = "b" * 64
        spec = {"package": {"name": "app.dmg", "bytes": 10, "sha256": "c" * 64},
                "source": {"name": "source.zip", "bytes": 20, "sha256": "d" * 64},
                "installerUrl": "https://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/test/app.dmg"}
        signed = {"signing": {"feed": {"feedSha256": feed_sha, "feedBytes": 100,
                  "rollback": {"name": "previous-appcast.xml", "bytes": 90, "sha256": "e" * 64}}}}
        published = {"releaseUrl": "https://github.com/JmooreOsirs/amplifai-phone-companion/releases/tag/test",
                     "assets": [{"name": "appcast.xml", "sha256": feed_sha}]}
        env = {"GITHUB_SHA": "f" * 40, "GITHUB_RUN_ID": "1", "GITHUB_RUN_ATTEMPT": "2",
               "SIGNED_ARTIFACT_ID": "3", "SIGNED_ARTIFACT_SHA256": "a" * 64}
        packet = publish.website_handoff(self.source, spec, signed, published, env)
        self.assertFalse(packet["websitePromoted"])
        self.assertFalse(packet["nativeHasWebsiteCredentials"])
        self.assertEqual(packet["expectedPreviousFeed"]["sha256"], "e" * 64)
        self.assertEqual(packet["feed"]["signature"], "embedded-sparkle-xml-comment")
        self.assertIn("src/app/api/v1/companion-diagnostics/route.ts", packet["requiredWebsiteChanges"])


if __name__ == "__main__":
    unittest.main()
