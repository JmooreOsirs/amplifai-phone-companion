"""Fail-closed Android release boundaries; no real signing key or publication."""
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import android_publish
import android_release as android


class AndroidReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.policy = android.read_json(android.ROOT / "android_policy.json")
        self.env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": android.REPOSITORY,
                    "GITHUB_REF": "refs/heads/main", "GITHUB_REF_PROTECTED": "true",
                    "GITHUB_ACTOR": "bac1876", "GITHUB_TRIGGERING_ACTOR": "bac1876",
                    "GITHUB_WORKFLOW_REF": android.REPOSITORY + "/" + android.WORKFLOW + "@refs/heads/main"}

    def test_only_original_authorized_actor_on_protected_main_can_sign(self):
        android.context(self.policy, self.env)
        for key, value in (("GITHUB_REF", "refs/heads/release/native"), ("GITHUB_ACTOR", "untrusted"),
                           ("GITHUB_TRIGGERING_ACTOR", "JmooreOsirs"), ("GITHUB_EVENT_NAME", "pull_request"),
                           ("GITHUB_REF_PROTECTED", "false"), ("GITHUB_REPOSITORY", "Osirs-io/amplifai-phone-companion"),
                           ("GITHUB_WORKFLOW_REF", "candidate-controlled-workflow")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                android.context(self.policy, {**self.env, key: value})

    def test_unadmitted_versions_and_unqualified_publication_are_rejected(self):
        with self.assertRaises(ValueError):
            android.specification(self.policy, "2026.10.24-rc13")
        for version in ("../../key", "2026.10.24-rc13\n", "2026.10.24-rc0"):
            with self.assertRaises(ValueError):
                android.specification(self.policy, version)
        for value in ({}, {"publication": {}}, {"publication": {"runtimeChecks": {}}}):
            with self.assertRaises(ValueError):
                android_publish.publication_spec(value)

    def test_artifact_must_match_successful_allowed_build_and_digest(self):
        spec = {"buildArtifact": {"runId": 10, "id": 20, "controlCommit": "a" * 40, "sha256": "b" * 64}}
        run = {"id": 10, "head_sha": "a" * 40, "head_branch": "main", "path": android.BUILD_WORKFLOW,
               "event": "workflow_dispatch", "status": "completed", "conclusion": "success",
               "repository": {"full_name": android.REPOSITORY}, "head_repository": {"full_name": android.REPOSITORY},
               "actor": {"login": "bac1876"}, "triggering_actor": {"login": "bac1876"}}
        artifact = {"id": 20, "expired": False, "digest": "sha256:" + "b" * 64, "size_in_bytes": 100,
                    "workflow_run": {"id": 10, "head_sha": "a" * 40}}
        android.validate_build(spec, run, artifact)
        for key, value in (("head_branch", "release/native"), ("head_sha", "c" * 40),
                           ("path", "candidate.yml"), ("conclusion", "failure"), ("event", "pull_request")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                android.validate_build(spec, {**run, key: value}, artifact)
        for key, value in (("expired", True), ("digest", "sha256:" + "c" * 64),
                           ("size_in_bytes", 0), ("workflow_run", {"id": 11, "head_sha": "a" * 40})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                android.validate_build(spec, run, {**artifact, key: value})

    def test_archives_reject_duplicates_links_nested_paths_and_extra_members(self):
        cases = [[("input", stat.S_IFREG)], [("input", stat.S_IFLNK)],
                 [("../input", stat.S_IFREG)], [("input", stat.S_IFREG), ("extra", stat.S_IFREG)]]
        for index, rows in enumerate(cases):
            archive = self.root / f"{index}.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                for name, mode in rows:
                    info = zipfile.ZipInfo(name); info.external_attr = (mode | 0o600) << 16
                    zipped.writestr(info, b"bounded fixture")
            out = self.root / f"out-{index}"; out.mkdir()
            if index == 0:
                android.extract_flat(archive, out, {"input"})
                self.assertEqual((out / "input").read_bytes(), b"bounded fixture")
            else:
                with self.assertRaises(ValueError):
                    android.extract_flat(archive, out, {"input"})
        self.assertFalse((self.root / "input").exists())

    def test_tools_receive_password_only_when_signing_and_never_encoded_key_or_api_tokens(self):
        values = {"ANDROID_RELEASE_PASSWORD": "fixture-password", "ANDROID_RELEASE_PKCS12_BASE64": "fixture-encoded-key",
                  "GH_TOKEN": "fixture-api-token", "GH_POLICY_AUDIT_TOKEN": "fixture-audit-token"}
        with patch.dict(os.environ, values), patch.object(subprocess, "run") as command:
            command.return_value = subprocess.CompletedProcess([], 0, stdout=b"safe output")
            for signing in (False, True):
                android.tool(["trusted-tool"], signing=signing)
                env = command.call_args.kwargs["env"]
                self.assertNotIn("ANDROID_RELEASE_PKCS12_BASE64", env)
                self.assertNotIn("GH_TOKEN", env)
                self.assertNotIn("GH_POLICY_AUDIT_TOKEN", env)
                self.assertEqual(env.get("ANDROID_RELEASE_PASSWORD"), "fixture-password" if signing else None)

    def test_tool_errors_do_not_expose_private_output(self):
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout=b"private", stderr=b"secret")):
            with self.assertRaises(ValueError) as error:
                android.tool(["trusted-tool"])
        self.assertNotIn("private", str(error.exception).replace("private output suppressed", ""))
        self.assertNotIn("secret", str(error.exception))

    def test_signer_artifact_requires_exact_run_control_and_successful_signer_job(self):
        approved = {"signingRunId": 10, "signingControlCommit": "a" * 40,
                    "signedArtifactId": 20, "signedArtifactSha256": "b" * 64}
        run = {"id": 10, "head_sha": "a" * 40, "head_branch": "main", "path": android.WORKFLOW,
               "event": "workflow_dispatch", "status": "completed", "conclusion": "success",
               "actor": {"login": "JmooreOsirs"}, "triggering_actor": {"login": "JmooreOsirs"},
               "repository": {"full_name": android.REPOSITORY}, "head_repository": {"full_name": android.REPOSITORY}}
        artifact = {"id": 20, "expired": False, "digest": "sha256:" + "b" * 64, "size_in_bytes": 100,
                    "workflow_run": {"id": 10, "head_sha": "a" * 40}, "created_at": "2026-10-24T20:01:00Z"}
        jobs = {"jobs": [{"name": "android-sign", "conclusion": "success", "started_at": "2026-10-24T20:00:00Z",
                           "completed_at": "2026-10-24T20:02:00Z"}]}
        android_publish.signer_provenance(approved, artifact, run, jobs)
        for key, value in (("created_at", "2026-10-24T19:00:00Z"), ("digest", "sha256:" + "c" * 64),
                           ("workflow_run", {"id": 11, "head_sha": "a" * 40})):
            with self.subTest(key=key), self.assertRaises(ValueError):
                android_publish.signer_provenance(approved, {**artifact, key: value}, run, jobs)
        with self.assertRaises(ValueError):
            android_publish.signer_provenance(approved, artifact, run, {"jobs": []})

    def test_same_or_newer_android_release_prevents_publication(self):
        spec = {"build": 26102413}
        with patch.object(android_publish.publish, "release_snapshot", return_value=[{"tag_name": "android-2026.10.23-rc12", "draft": False}]):
            android_publish.release_order(spec)
        for tag in ("android-2026.10.24-rc13", "android-2026.10.24-rc14"):
            with patch.object(android_publish.publish, "release_snapshot", return_value=[{"tag_name": tag, "draft": False}]):
                with self.assertRaises(ValueError):
                    android_publish.release_order(spec)


if __name__ == "__main__":
    unittest.main()
