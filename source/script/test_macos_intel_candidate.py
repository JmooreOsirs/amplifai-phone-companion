"""Release source custody and Intel identity checks, without native packaging."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("intel_builder", Path(__file__).with_name("build_macos_intel_candidate.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)

COMMIT = "a" * 40
TREE = "b" * 40


class SourceFreezeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.contents = {"source/worker.py": b"print('synthetic')\n", "docs/contract.json": b"{}\n"}
        self.modes = {name: "100644" for name in self.contents}
        self.blobs = {name: hashlib.sha1(data).hexdigest() for name, data in self.contents.items()}
        self.dirty = b""
        for name, data in self.contents.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o644)
        self.git = patch.object(builder, "_git", side_effect=self.fake_git)
        self.git.start()
        self.addCleanup(self.git.stop)

    def fake_git(self, repo, *args):
        self.assertEqual(repo, self.root)
        if args == ("rev-parse", "HEAD"):
            return (COMMIT + "\n").encode()
        if args == ("rev-parse", COMMIT + "^{tree}"):
            return (TREE + "\n").encode()
        if args == ("status", "--porcelain=v1", "--untracked-files=all"):
            return self.dirty
        if args == ("ls-tree", "-r", "-z", "--full-tree", COMMIT):
            return b"".join(f"{self.modes[name]} blob {self.blobs[name]}\t{name}\0".encode() for name in self.contents)
        if args[:2] == ("cat-file", "blob"):
            return next(data for name, data in self.contents.items() if self.blobs[name] == args[2])
        self.fail(f"Unexpected synthetic Git request: {args}")

    def test_receipt_is_deterministic_and_binds_all_committed_bytes_and_modes(self):
        first = builder.source_freeze(self.root, COMMIT)
        (self.root / "ignored-cache").mkdir()
        (self.root / "ignored-cache/owner.txt").write_text("not committed")
        second = builder.source_freeze(self.root, COMMIT)
        self.assertEqual(builder.freeze_bytes(first), builder.freeze_bytes(second))
        self.assertEqual(first["commit"], COMMIT)
        self.assertEqual(first["tree"], TREE)
        self.assertEqual([record["path"] for record in first["files"]], sorted(self.contents))
        for record in first["files"]:
            self.assertEqual(record["sha256"], hashlib.sha256(self.contents[record["path"]]).hexdigest())
            self.assertEqual(record["bytes"], len(self.contents[record["path"]]))
            self.assertEqual(record["git_mode"], "100644")
        self.assertNotIn(str(self.root).encode(), builder.freeze_bytes(first))

    def test_wrong_commit_and_abbreviated_commit_are_refused(self):
        for commit in ("c" * 40, COMMIT[:7], COMMIT.upper()):
            with self.subTest(commit=commit), self.assertRaises(ValueError):
                builder.source_freeze(self.root, commit)

    def test_tracked_and_untracked_dirty_inputs_are_refused(self):
        for status in (b" M source/worker.py\n", b"?? new-worker.py\n"):
            self.dirty = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                builder.source_freeze(self.root, COMMIT)

    def test_checkout_change_during_snapshot_is_refused(self):
        calls = 0
        def changing_git(repo, *args):
            nonlocal calls
            if args == ("rev-parse", "HEAD"):
                calls += 1
                if calls == 2:
                    return b"c" * 40 + b"\n"
            return self.fake_git(repo, *args)
        with patch.object(builder, "_git", side_effect=changing_git), self.assertRaisesRegex(ValueError, "while freezing"):
            builder.source_freeze(self.root, COMMIT)

    def test_working_byte_tamper_is_refused_even_when_git_reports_clean(self):
        (self.root / "source/worker.py").write_bytes(b"changed same length\n")
        with self.assertRaisesRegex(ValueError, "committed blob"):
            builder.source_freeze(self.root, COMMIT)

    def test_missing_file_and_wrong_executable_mode_are_refused(self):
        path = self.root / "source/worker.py"
        path.chmod(0o755)
        with self.assertRaisesRegex(ValueError, "mode"):
            builder.source_freeze(self.root, COMMIT)
        path.unlink()
        with self.assertRaisesRegex(ValueError, "missing"):
            builder.source_freeze(self.root, COMMIT)

    def test_symlink_substitution_and_git_symlink_mode_are_refused(self):
        path = self.root / "source/worker.py"
        outside = Path(self.temp.name) / "outside.py"
        outside.write_bytes(self.contents["source/worker.py"])
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "link"):
            builder.source_freeze(self.root, COMMIT)
        path.unlink()
        path.write_bytes(outside.read_bytes())
        self.modes["source/worker.py"] = "120000"
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            builder.source_freeze(self.root, COMMIT)

    def test_parent_traversal_entry_is_refused(self):
        self.contents["../escape.py"] = b"escape"
        self.modes["../escape.py"] = "100644"
        self.blobs["../escape.py"] = "d" * 40
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            builder.source_freeze(self.root, COMMIT)

    def test_receipt_digest_and_exact_serialized_bytes_must_both_match(self):
        receipt = builder.source_freeze(self.root, COMMIT)
        data = builder.freeze_bytes(receipt)
        path = Path(self.temp.name) / "freeze.json"
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        with patch.object(builder, "verify_release_policy"):
            self.assertEqual(builder.verify_source_freeze(self.root, path, COMMIT, digest), receipt)
            path.write_bytes(data + b" ")
            with self.assertRaises(ValueError):
                builder.verify_source_freeze(self.root, path, COMMIT, digest)
            path.write_bytes(data)
            for wrong in ("e" * 64, "e" * 63, digest.upper()):
                with self.subTest(digest=wrong), self.assertRaises(ValueError):
                    builder.verify_source_freeze(self.root, path, COMMIT, wrong)
            link = Path(self.temp.name) / "freeze-link.json"
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                builder.verify_source_freeze(self.root, link, COMMIT, digest)

    def test_freeze_only_cli_does_not_require_native_host_or_build(self):
        output = Path(self.temp.name) / "receipt.json"
        argv = ["builder", "--expected-source-commit", COMMIT, "--freeze-source-receipt", str(output)]
        with patch.object(builder, "ROOT", self.root), patch.object(builder, "verify_release_policy"), \
                patch.object(builder, "run", side_effect=AssertionError("must not build")), \
                patch.object(builder.sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(builder.main(), 0)
        self.assertEqual(output.read_bytes(), builder.freeze_bytes(builder.source_freeze(self.root, COMMIT)))
        # A self-containing receipt would dirty the exact checkout it claims to freeze.
        argv[-1] = str(self.root / "self-containing.json")
        with patch.object(builder, "ROOT", self.root), patch.object(builder.sys, "argv", argv), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            builder.main()


class CandidateIsolationTests(unittest.TestCase):
    def test_current_identity_and_unchanged_dependency_recipe_are_pinned(self):
        policy = builder.verify_release_policy()
        self.assertEqual(policy["release"], "macos-2026.10.20-rc30-intel1-test")
        self.assertEqual(policy["historical_dependency_source"]["archive_sha256"],
                         "171c383e06b56f681a627a571fd9ed228ebc4c34a8b59c311112ec4f3b8265c3")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in policy["pinned_inputs"]:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((builder.ROOT / name).read_bytes())
            (root / "docs").mkdir()
            policy_path = root / "docs/INTEL-RC30-BASELINE.json"
            policy_path.write_text(json.dumps(policy))
            self.assertEqual(builder.verify_release_policy(root), policy)
            (root / next(iter(policy["pinned_inputs"]))).write_text("changed recipe")
            with self.assertRaises(ValueError):
                builder.verify_release_policy(root)
            policy["build"] = "261014251"
            policy_path.write_text(json.dumps(policy))
            with self.assertRaises(ValueError):
                builder.verify_release_policy(root)

    def test_qualified_updates_keep_the_intel_channel_separate(self):
        original = {"SUPublicEDKey": "vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs=",
                    "SURequireSignedFeed": True, "SUVerifyUpdateBeforeExtraction": True,
                    "SUFeedURL": "https://example.invalid/arm.xml"}
        info = builder.candidate_plist(original, enable_published_updates=True)
        self.assertEqual(info["SUFeedURL"], "https://amplifai-database-engine.vercel.app/phone/macos/intel-appcast.xml")
        for key in ("AMPLIFaiQualifiedUpdateFeed", "SUEnableAutomaticChecks", "SUAutomaticallyUpdate", "SUAllowsAutomaticUpdates"):
            self.assertTrue(info[key])
        self.assertEqual(original["SUFeedURL"], "https://example.invalid/arm.xml")
        with self.assertRaises(ValueError):
            builder.candidate_plist({"SUPublicEDKey": "wrong-key"}, enable_published_updates=True)

    def test_arm_update_feed_and_identity_are_not_inherited(self):
        original = {"SUFeedURL": "https://example.invalid/arm.xml", "SUEnableAutomaticChecks": True,
                    "CFBundleIdentifier": "original.production", "CFBundleExecutable": "AmplifaiPhone"}
        info = builder.candidate_plist(original)
        self.assertNotIn("SUFeedURL", info)
        for key in ("AMPLIFaiQualifiedUpdateFeed", "SUEnableAutomaticChecks", "SUAutomaticallyUpdate", "SUAllowsAutomaticUpdates"):
            self.assertFalse(info[key])
        self.assertNotEqual(info["CFBundleIdentifier"], original["CFBundleIdentifier"])
        self.assertEqual(info["CFBundleExecutable"], "AmplifaiPhone")
        self.assertEqual(info["LSMinimumSystemVersion"], "14.0")
        self.assertEqual(info["CFBundleShortVersionString"], "2026.10.20-rc30-intel1")
        self.assertEqual(info["CFBundleVersion"], "261020301")
        self.assertTrue(original["SUEnableAutomaticChecks"])
        self.assertIn("SUFeedURL", original)


if __name__ == "__main__":
    unittest.main()
