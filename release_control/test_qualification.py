"""Profile downgrade, missing-check and producer failure boundaries; no device or AppKit launch."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import producer
import release


class QualificationProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = {"sourceCommit": "a" * 40, "channel": "macos-arm64", "version": "2026.10.19-rc29",
                       "build": "26101929", "qualificationProfile": "phone-run-recovery-v1"}
        self.app = self.root / "app.zip"
        self.archive = self.root / "source.zip"
        self.app.write_bytes(b"synthetic app archive")
        self.archive.write_bytes(b"synthetic source archive")

    def record(self, source=None):
        return producer.qualification_record(source or self.source, 11, self.app, self.archive)

    def files(self):
        paths = [self.root / "source/collector/tests" / (name + ".py")
                 for name in ("test_bridge_run_timing", "test_bridge_recovery", "test_bridge_refresh",
                              "test_call_accounting", "test_source_diagnostic_flow", "test_nonfinite_duration_pipeline",
                              "test_sqlite_encrypted_size_compat", "test_sqlite_messages_size_compat")]
        paths += [self.root / "source/collector/macos/Tests" / name
                  for name in ("check_model.sh", "check_window_lifecycle.sh")]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# synthetic test fixture, execution is intercepted\n")
        return paths

    def test_existing_rc28_records_remain_exact_schema_one_for_both_channels(self):
        sources = [
            {"sourceCommit": "8f7ec4d3de4235ab5d36d5175c29121e3a26548a", "channel": "macos-arm64",
             "version": "2026.10.16-rc28", "build": "26101628"},
            {"sourceCommit": "8f7ec4d3de4235ab5d36d5175c29121e3a26548a", "channel": "macos-intel-test",
             "version": "2026.10.16-rc28-intel1", "build": "261016281"},
        ]
        for source in sources:
            with self.subTest(channel=source["channel"]):
                record = self.record(source)
                self.assertEqual(record["schema"], 1)
                self.assertNotIn("qualificationProfile", record)
                self.assertEqual(record["checks"], {"functional": True, "architecture": True, "frozenHelper": True,
                                                   "sourceClosure": True, "legal": True})
                release.validate_delivery_record(record, source, {"runId": 11})
                for key, value in (("sourceCommit", "b" * 40), ("channel", "other"),
                                   ("build", "26101929"), ("version", "2026.10.19-rc29")):
                    with self.assertRaisesRegex(ValueError, "qualification profile"):
                        release.qualification_profile(dict(source, **{key: value}))

    def test_new_source_requires_explicit_known_profile_before_policy_approval(self):
        policy = {"activationApproved": True, "approvedSources": [], "releasedBuilds": {"macos-arm64": "26101628"}}
        for profile in (None, "", "legacy", "phone-run-v1", "phone-run-recovery-v2", True):
            source = dict(self.source, releaseAuthorized=True, buildWorkflow="native.yml")
            if profile is None:
                source.pop("qualificationProfile")
            else:
                source["qualificationProfile"] = profile
            policy["approvedSources"] = [source]
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, "qualification profile"):
                release.validate_approved_source(policy, {"sourceCommit": "a" * 40, "channel": "macos-arm64"}, {"path": "native.yml"})

    def test_new_record_binds_profile_and_each_additional_boolean(self):
        record = self.record()
        self.assertEqual(record["schema"], 2)
        self.assertEqual(record["qualificationProfile"], "phone-run-recovery-v1")
        expected = {"functional", "architecture", "frozenHelper", "sourceClosure", "legal",
                    "collectorModel", "windowLifecycle", "bridgeRunTiming", "bridgeRecovery"}
        self.assertEqual(set(record["checks"]), expected)
        release.validate_delivery_record(record, self.source, {"runId": 11})
        mutations = [lambda r: r.update(schema=1), lambda r: r.pop("qualificationProfile"),
                     lambda r: r.update(qualificationProfile="phone-run-v1"),
                     lambda r: r["checks"].update(unreviewedCheck=True)]
        for name in expected:
            mutations.extend((lambda r, name=name: r["checks"].pop(name),
                              lambda r, name=name: r["checks"].update({name: False}),
                              lambda r, name=name: r["checks"].update({name: 1})))
        for mutate in mutations:
            candidate = copy.deepcopy(record)
            mutate(candidate)
            with self.assertRaises(ValueError):
                release.validate_delivery_record(candidate, self.source, {"runId": 11})

    def test_legacy_record_cannot_be_relabelled_as_new_source(self):
        record = self.record()
        record["schema"] = 1
        record.pop("qualificationProfile")
        record["checks"] = dict.fromkeys(release.BASE_QUALIFICATION_CHECKS, True)
        with self.assertRaisesRegex(ValueError, "trusted build"):
            release.validate_delivery_record(record, self.source, {"runId": 11})

    def test_missing_required_file_fails_before_any_test_is_started(self):
        for missing in self.files():
            missing.unlink()
            with self.subTest(missing=missing.name), patch.object(subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "required phone-run qualification file"):
                    producer.run_phone_run_checks(self.root)
                run.assert_not_called()
            missing.write_text("# fixture\n")

    def test_symlink_or_escaping_test_directory_is_not_accepted(self):
        paths = self.files()
        target = self.root / "other.py"
        target.write_text("# fixture\n")
        paths[0].unlink()
        paths[0].symlink_to(target)
        with patch.object(subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "unsafe"):
                producer.run_phone_run_checks(self.root)
            run.assert_not_called()
        paths[0].unlink()
        paths[0].write_text("# fixture\n")
        with tempfile.TemporaryDirectory() as external:
            directory = paths[0].parent
            for path in (entry for entry in paths if entry.parent == directory):
                path.unlink()
                (Path(external) / path.name).write_text("# fixture\n")
            directory.rmdir()
            directory.symlink_to(external, target_is_directory=True)
            with patch.object(subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    producer.run_phone_run_checks(self.root)
                run.assert_not_called()

    def test_shared_runner_executes_bridge_modules_model_and_appkit(self):
        self.files()
        with patch.dict(producer.os.environ, {"AMPLIFAI_TEST_PYTHON": "/untrusted/python"}), \
                patch.object(subprocess, "run") as run:
            producer.run_phone_run_checks(self.root)
        calls = run.call_args_list
        self.assertEqual(calls[0].args[0], [sys.executable, "-m", "unittest", "test_bridge_run_timing",
                                          "test_bridge_recovery", "test_bridge_refresh", "test_call_accounting",
                                          "test_source_diagnostic_flow", "test_nonfinite_duration_pipeline",
                                          "test_sqlite_encrypted_size_compat", "test_sqlite_messages_size_compat", "-v"])
        self.assertEqual([Path(call.args[0][-1]).name for call in calls[1:]], ["check_model.sh", "check_window_lifecycle.sh"])
        for call in calls:
            self.assertTrue(call.kwargs["check"])
            self.assertEqual(call.kwargs["cwd"], self.root)
            self.assertEqual(call.kwargs["env"]["PYTHONNOUSERSITE"], "1")
            self.assertEqual(call.kwargs["env"]["AMPLIFAI_TEST_PYTHON"], sys.executable)

    def test_each_command_failure_propagates_without_running_later_commands(self):
        self.files()
        for failed in range(3):
            replies = [subprocess.CompletedProcess([], 0)] * failed + [subprocess.CalledProcessError(1, ["synthetic"])]
            with patch.object(subprocess, "run", side_effect=replies) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    producer.run_phone_run_checks(self.root)
                self.assertEqual(run.call_count, failed + 1)

    def test_profile_runner_refuses_product_credentials(self):
        self.files()
        with patch.dict(producer.os.environ, {"SPARKLE_PRIVATE_KEY": "synthetic-not-a-key"}), patch.object(subprocess, "run") as run:
            with self.assertRaisesRegex(ValueError, "must not receive product secrets"):
                producer.run_phone_run_checks(self.root)
            run.assert_not_called()

    def test_producer_profile_failure_prevents_delivery_and_legacy_does_not_run_new_checks(self):
        legacy = dict(zip(("sourceCommit", "channel", "version", "build"),
                          next(row for row in release.LEGACY_QUALIFICATIONS if row[1] == "macos-arm64")))
        app = self.root / "AMPLIFai Phone.app"
        app.mkdir()
        (app / "fixture").write_bytes(b"synthetic app payload")
        for source in (self.source, legacy):
            output = self.root / ("new-delivery" if source is self.source else "legacy-delivery")
            with patch.object(release, "validate_app"), patch.object(producer, "validate_source"), \
                    patch("verify_macos_architecture.audit", return_value={"passed": True}), \
                    patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                    patch.object(producer, "run_phone_run_checks", side_effect=ValueError("required check failed")) as checks:
                if source is self.source:
                    with self.assertRaisesRegex(ValueError, "required check failed"):
                        producer.qualify(source, self.root, app, self.archive, output, 11)
                    checks.assert_called_once_with(self.root)
                    self.assertFalse(output.exists())
                else:
                    producer.qualify(source, self.root, app, self.archive, output, 11)
                    checks.assert_not_called()
                    record = json.loads((output / "qualification.json").read_text())
                    release.validate_delivery_record(record, legacy, {"runId": 11})
                    self.assertEqual(record["schema"], 1)


if __name__ == "__main__":
    unittest.main()
