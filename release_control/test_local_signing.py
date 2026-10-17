"""Mocked key-use and packaging boundaries; no real keys or native tools exercised."""
import base64
from contextlib import ExitStack
import json
import os
from pathlib import Path
import plistlib
import shutil
import tempfile
import unittest
from unittest.mock import patch

import feeds
import finalize
import producer
import release


class LocalSigningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private_root = self.root / "private"
        self.private_root.mkdir(mode=0o700)
        self.output = self.root / "output"
        self.output.mkdir()
        self.app = self.root / "AMPLIFai Phone.app"
        self.binary = self.app / "Contents/Frameworks/Nested.framework/Versions/A/Nested"
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b"\xcf\xfa\xed\xfefixture native data")
        self.source = self.root / "source.zip"
        self.source.write_bytes(b"qualified source")
        self.record = {"sourceCommit": "a" * 40, "channel": "macos-arm64",
                       "version": "2026.10.16-rc28", "build": "26101628"}
        self.tools = self.root / "tools"
        self.signer = self.tools / "bin/sign_update"
        self.signer.parent.mkdir(parents=True)
        self.signer.write_bytes(b"fixture signer never executed")
        info = self.tools / "Sparkle.framework/Versions/B/Resources/Info.plist"
        info.parent.mkdir(parents=True)
        info.write_bytes(plistlib.dumps({"CFBundleShortVersionString": "2.10.0"}))
        self.signature = base64.b64encode(b"s" * 64).decode()
        self.notary_id = "12345678-1234-1234-1234-123456789abc"
        self.calls = []
        self.commands = []
        self.identities = '1) ' + release.CERTIFICATE_SHA1 + ' "' + release.IDENTITY + '"'
        self.failure = None

    def command(self, args, **kwargs):
        args = list(map(str, args))
        self.calls.append((args, kwargs))
        self.commands.append(args)
        if self.failure and self.failure(args):
            raise ValueError("mocked external operation refused")
        program = Path(args[0]).name
        if program == "security":
            if args[1] == "find-identity":
                return self.identities
            if args[1] == "create-keychain":
                Path(args[-1]).touch()
            elif args[1] == "delete-keychain":
                Path(args[-1]).unlink()
        elif program == "sign_update":
            probe = Path(args[-1])
            if probe.name == "key-continuity-probe":
                self.assertEqual(probe.parent.stat().st_mode & 0o777, 0o700)
            return self.signature
        elif program == "xcrun" and args[1:3] == ["notarytool", "submit"]:
            return json.dumps({"id": self.notary_id, "status": "Accepted"})
        elif program == "hdiutil":
            Path(args[-1]).write_bytes(b"fixture notarized package")
        elif program == "ditto" and len(args) == 3:
            shutil.copytree(args[1], args[2])
        return ""

    def finalized(self, source, destination, inventory, commit):
        destination.write_bytes(b"finalized source with signed inventory")
        self.assertEqual(commit, self.record["sourceCommit"])
        self.assertEqual(json.loads(inventory.read_text())["files"][0]["sha256"], release.digest(self.binary))
        return {"completeMemberReadback": True}

    def prepared(self, source, package, source_zip, output, tools, openssl, **kwargs):
        provider = kwargs.get("keychain_signer")
        if provider is not None:
            provider(package)
        return {"publicKey": release.PUBLIC_KEY, "feedPrepared": True}

    def backend(self, *, local=True):
        env = {} if local else {
            "APPLE_CERTIFICATE_P12_BASE64": base64.b64encode(b"fixture p12").decode(),
            "APPLE_CERTIFICATE_PASSWORD": "fixture password",
            "NOTARY_APPLE_ID": "fixture@example.invalid", "NOTARY_APP_PASSWORD": "fixture notary",
            "SPARKLE_PRIVATE_KEY": "fixture sparkle", "RUNNER_TEMP": str(self.private_root),
        }
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env, clear=True))
            stack.enter_context(patch.object(release, "SIGN_UPDATE_SHA256", release.digest(self.signer)))
            stack.enter_context(patch.object(release, "signing_openssl", return_value="fixture-openssl"))
            stack.enter_context(patch.object(release, "command", side_effect=self.command))
            validate_app = stack.enter_context(patch.object(release, "validate_app"))
            validate_input = stack.enter_context(patch.object(producer, "validate_signing_input"))
            stack.enter_context(patch.object(finalize, "finalize_source", side_effect=self.finalized))
            stack.enter_context(patch.object(feeds, "prepare", side_effect=self.prepared))
            if local:
                result = release.sign_local_candidate(self.app, self.source, self.output, self.signer,
                                                      self.record, self.private_root)
                validate_app.assert_called_once_with(self.app, self.record, self.record["channel"])
            else:
                result = release.sign_candidate(self.app, self.source, self.output, self.signer, self.record)
            validate_input.assert_called_once_with(self.source, self.app, self.record["sourceCommit"], self.record["channel"])
            return result

    def test_local_uses_only_existing_keys_and_same_packaging_verifications(self):
        result = self.backend()
        self.assertEqual(result["signingMode"], "local-keychain")
        self.assertEqual(result["signingIdentity"]["certificateSha1"], release.CERTIFICATE_SHA1)
        self.assertEqual(result["appNotarization"], {"id": self.notary_id, "status": "Accepted"})
        self.assertEqual(result["dmgNotarization"], result["appNotarization"])
        self.assertTrue(result["sourceFinalization"]["completeMemberReadback"])
        self.assertEqual(list(self.private_root.iterdir()), [])
        self.assertEqual([a[1] for a in self.commands if Path(a[0]).name == "security"], ["find-identity"])
        self.assertFalse(any("--keychain" in a or "--ed-key-file" in a for a in self.commands))
        self.assertTrue(all("input_data" not in kw for _, kw in self.calls))
        signatures = [a for a in self.commands if Path(a[0]).name == "codesign" and "--sign" in a]
        self.assertEqual(signatures[0][-1], str(self.binary))
        self.assertEqual(signatures[-2][-1], str(self.app))
        for args in signatures:
            self.assertEqual(args[0], "/usr/bin/codesign")
            self.assertEqual(args[args.index("--sign") + 1], release.CERTIFICATE_SHA1)
        notary = [a for a in self.commands if a[1:3] == ["notarytool", "submit"]]
        self.assertEqual(len(notary), 2)
        self.assertTrue(all(a[a.index("--keychain-profile") + 1] == release.NOTARY_PROFILE for a in notary))
        for action in ("staple", "validate"):
            self.assertEqual(len([a for a in self.commands if a[1:3] == ["stapler", action]]), 2)
        self.assertEqual(len([a for a in self.commands if a[0] == "spctl"]), 2)
        self.assertTrue(all("--all-architectures" in a for a in self.commands
                            if Path(a[0]).name == "codesign" and "--verify" in a))
        self.assertTrue(all(kw.get("timeout") == 60 for a, kw in self.calls
                            if Path(a[0]).name == "sign_update" or ("--sign" in a)))

    def test_hosted_default_still_imports_scoped_keychain_and_uses_secret_stdin(self):
        result = self.backend(local=False)
        self.assertNotIn("signingMode", result)
        self.assertNotIn("appNotarization", result)
        actions = [a[1] for a in self.commands if a[0] == "security"]
        self.assertIn("import", actions)
        self.assertIn("set-key-partition-list", actions)
        self.assertEqual(actions.count("delete-keychain"), 1)
        for args, kwargs in self.calls:
            if Path(args[0]).name == "sign_update":
                self.assertIn("--ed-key-file", args)
                self.assertEqual(kwargs["input_data"], b"fixture sparkle")
            if args[1:3] == ["notarytool", "submit"]:
                self.assertIn("--keychain", args)
                self.assertEqual(args[args.index("--keychain-profile") + 1], "amplifai-ci")
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_wrong_identity_fails_before_key_use(self):
        for identities in ('1) ' + '0' * 40 + ' "' + release.IDENTITY + '"',
                           '1) ' + release.CERTIFICATE_SHA1 + ' "another identity"'):
            with self.subTest(identities=identities):
                self.identities = identities
                self.commands.clear()
                with self.assertRaisesRegex(ValueError, "Developer ID identity differs"):
                    self.backend()
                self.assertEqual(len(self.commands), 1)
                self.assertEqual(list(self.private_root.iterdir()), [])

    def test_public_key_continuity_failure_stops_before_codesign_and_cleans(self):
        self.failure = lambda args: "pkeyutl" in args
        with self.assertRaisesRegex(ValueError, "refused"):
            self.backend()
        self.assertFalse(any(Path(a[0]).name == "codesign" for a in self.commands))
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_key_use_failure_stops_without_keychain_mutation_and_cleans(self):
        self.failure = lambda args: Path(args[0]).name == "codesign" and "--sign" in args
        with self.assertRaisesRegex(ValueError, "refused"):
            self.backend()
        self.assertEqual([a[1] for a in self.commands if Path(a[0]).name == "security"], ["find-identity"])
        self.assertFalse(any(a[1:3] == ["notarytool", "submit"] for a in self.commands))
        self.assertEqual(list(self.private_root.iterdir()), [])

    def test_notary_rejects_unaccepted_or_missing_submission_id(self):
        for response in ({"id": self.notary_id, "status": "Invalid"},
                         {"id": self.notary_id, "status": "In Progress"},
                         {"status": "Accepted"}, {"id": "invented", "status": "Accepted"}):
            with self.subTest(response=response), patch.object(release, "command", return_value=json.dumps(response)):
                with self.assertRaisesRegex(ValueError, "notarization not accepted"):
                    release.notarize_local(self.source)

    def test_notary_failure_stops_before_feed_and_cleans(self):
        with patch.object(release, "notarize_local", side_effect=ValueError("notarization not accepted")):
            with self.assertRaisesRegex(ValueError, "notarization not accepted"):
                self.backend()
        self.assertEqual(list(self.private_root.iterdir()), [])
        self.assertEqual(list(self.output.iterdir()), [])

    def test_manifest_signing_pins_tool_account_public_key_and_cleans(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(release, "SIGN_UPDATE_SHA256", release.digest(self.signer)), \
                patch.object(release, "command", side_effect=self.command):
            signature = release.sign_local_and_verify(self.source, self.signer, "fixture-openssl", self.private_root)
        self.assertEqual(signature, self.signature)
        self.assertEqual(self.commands[0][self.commands[0].index("--account") + 1], release.SPARKLE_ACCOUNT)
        self.assertEqual(list(self.private_root.iterdir()), [])
        with patch.object(release, "command") as command:
            with self.assertRaisesRegex(ValueError, "pinned Sparkle"):
                release.sign_local_and_verify(self.source, self.signer, "fixture-openssl", self.private_root)
            command.assert_not_called()

    def test_feed_provider_is_explicit_and_hosted_default_uses_stdin(self):
        planned = feeds.names(self.record)
        package = self.output / planned["package"]
        source = self.output / planned["source"]
        package.write_bytes(b"package")
        source.write_bytes(b"source")
        def previous(url, path, digest, limit):
            path.write_bytes(b"previous feed")
        def prepare(spec, package, source, output, signer, pins, previous, openssl):
            return {"signature": signer.sign(package)}
        for local in (False, True):
            with self.subTest(local=local), patch.dict(os.environ, {} if local else {"SPARKLE_PRIVATE_KEY": "fixture"}, clear=True), \
                    patch.object(feeds.existing, "SIGN_UPDATE_SHA256", release.digest(self.signer)), \
                    patch.object(release, "SIGN_UPDATE_SHA256", release.digest(self.signer)), \
                    patch.object(release, "command", side_effect=self.command), \
                    patch.object(release, "download", side_effect=previous), \
                    patch.object(feeds.existing, "validate_appcast"), \
                    patch.object(feeds.existing, "prepare", side_effect=prepare):
                result = feeds.prepare(self.record, package, source, self.output / "feed", self.tools, "fixture-openssl",
                                       keychain_signer=release.KeychainSparkleSigner(self.signer) if local else None)
                self.assertEqual(result["signature"], self.signature)
                args, kwargs = self.calls[-1]
                self.assertEqual("--account" in args, local)
                self.assertEqual("input_data" in kwargs, not local)


if __name__ == "__main__":
    unittest.main()
