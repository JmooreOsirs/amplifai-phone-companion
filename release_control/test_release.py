"""Adversarial tests of the release credential and artifact boundaries."""
import copy
import json
from pathlib import Path
import plistlib
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import release


class ReleaseBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((Path(__file__).parent / "policy.json").read_text())
        self.policy["adminBypassDisabledVerified"] = True
        self.env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": self.policy["repository"],
                    "GITHUB_ACTOR": "bac1876", "GITHUB_TRIGGERING_ACTOR": "bac1876",
                    "GITHUB_REF": "refs/heads/main", "GITHUB_REF_PROTECTED": "true",
                    "GITHUB_WORKFLOW_REF": self.policy["repository"] + "/.github/workflows/macos-protected-release.yml@refs/heads/main"}
        self.request = {"runId": 11, "artifactId": 22, "sourceCommit": "a" * 40,
                        "artifactSha256": "b" * 64, "channel": "macos-arm64"}
        self.run = {"id": 11, "head_sha": "a" * 40, "head_branch": "release/native",
                    "head_repository": {"full_name": self.policy["repository"]},
                    "repository": {"full_name": self.policy["repository"]},
                    "actor": {"login": "bac1876"}, "triggering_actor": {"login": "bac1876"},
                    "path": ".github/workflows/macos-arm64-check.yml", "event": "workflow_dispatch",
                    "status": "completed", "conclusion": "success"}
        self.artifact = {"id": 22, "workflow_run": {"id": 11, "head_sha": "a" * 40},
                         "expired": False, "size_in_bytes": 100, "digest": "sha256:" + "b" * 64}
        self.comparison = {"status": "ahead", "merge_base_commit": {"sha": self.policy["qualifiedAncestor"]}}
        self.environment = {"name": "amplifai-macos-signing", "protection_rules": [
            {"type": "required_reviewers", "prevent_self_review": True,
             "reviewers": [{"type": "User", "reviewer": {"login": "independent-reviewer"}}]}],
            "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}
        self.branches = {"branch_policies": [{"name": "main", "type": "branch"}]}

    def provenance(self, **changes):
        run, artifact, comparison = copy.deepcopy((self.run, self.artifact, self.comparison))
        run.update(changes)
        release.validate_provenance(self.policy, self.request, run, artifact, comparison)

    def test_successful_manual_canonical_build(self):
        release.validate_context(self.policy, self.env)
        self.provenance()

    def test_pr_fork_failed_and_wrong_source_rejected(self):
        for change in ({"event": "pull_request"}, {"head_repository": {"full_name": "fork/repo"}},
                       {"conclusion": "failure"}, {"head_sha": "c" * 40},
                       {"head_branch": "unreviewed"}, {"path": ".github/workflows/attacker.yml"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.provenance(**change)

    def test_rerun_by_different_actor_rejected(self):
        with self.assertRaises(ValueError):
            self.provenance(triggering_actor={"login": "JmooreOsirs"})
        self.env["GITHUB_TRIGGERING_ACTOR"] = "JmooreOsirs"
        with self.assertRaises(ValueError):
            release.validate_context(self.policy, self.env)

    def test_branch_and_fork_control_dispatch_rejected(self):
        for key, value in (("GITHUB_REF", "refs/heads/attacker"), ("GITHUB_REPOSITORY", "fork/repo"),
                           ("GITHUB_EVENT_NAME", "pull_request_target"), ("GITHUB_WORKFLOW_REF", "other@refs/heads/main")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                release.validate_context(self.policy, dict(self.env, **{key: value}))

    def test_artifact_tamper_expiry_and_cross_run_rejected(self):
        for key, value in (("digest", "sha256:" + "c" * 64), ("expired", True),
                           ("workflow_run", {"id": 33}), ("size_in_bytes", release.MAX_ARTIFACT + 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                release.validate_provenance(self.policy, self.request, self.run,
                                            dict(self.artifact, **{key: value}), self.comparison)

    def test_rc27_branch_without_later_windows_lineage_rejected(self):
        with self.assertRaises(ValueError):
            release.validate_provenance(self.policy, self.request, self.run, self.artifact,
                                        {"status": "diverged", "merge_base_commit": {"sha": "257748fcb2d6aef758766314f4e3817c8d30c372"}})

    def test_actual_review_gate_and_protected_main_required(self):
        release.validate_protection(self.policy, self.environment, self.branches, {"protected": True}, self.env)
        for mutate in (lambda env: env["protection_rules"].clear(),
                       lambda env: env["protection_rules"][0].update(prevent_self_review=False),
                       lambda env: env["protection_rules"][0].update(reviewers=[])):
            candidate = copy.deepcopy(self.environment)
            mutate(candidate)
            with self.assertRaises(ValueError):
                release.validate_protection(self.policy, candidate, self.branches, {"protected": True}, self.env)
        with self.assertRaises(ValueError):
            release.validate_protection(self.policy, self.environment, self.branches, {"protected": False}, self.env)

    def test_wildcard_or_tag_deployment_policy_rejected(self):
        for item in ({"name": "*", "type": "branch"}, {"name": "main", "type": "tag"}):
            with self.assertRaises(ValueError):
                release.validate_protection(self.policy, self.environment, {"branch_policies": [item]}, {"protected": True}, self.env)

    def test_unverified_admin_bypass_cannot_activate_signing(self):
        self.policy["adminBypassDisabledVerified"] = False
        with self.assertRaises(ValueError):
            release.validate_protection(self.policy, self.environment, self.branches, {"protected": True}, self.env)

    def test_weakened_branch_review_or_direct_push_bypass_rejected(self):
        protection = {"enforce_admins": {"enabled": True},
                      "required_pull_request_reviews": {"dismiss_stale_reviews": True, "require_code_owner_reviews": True,
                          "require_last_push_approval": True, "required_approving_review_count": 1,
                          "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []}},
                      "allow_force_pushes": {"enabled": False}, "allow_deletions": {"enabled": False},
                      "required_status_checks": {"strict": True, "contexts": ["release-control-checks"]}}
        release.validate_branch_custody(self.policy, protection, self.policy["codeOwners"])
        for mutate in (lambda p: p["enforce_admins"].update(enabled=False),
                       lambda p: p["required_pull_request_reviews"].update(required_approving_review_count=0),
                       lambda p: p["required_pull_request_reviews"].update(require_last_push_approval=False),
                       lambda p: p["required_pull_request_reviews"]["bypass_pull_request_allowances"].update(users=["bac1876"]),
                       lambda p: p["required_status_checks"].update(strict=False),
                       lambda p: p["allow_force_pushes"].update(enabled=True)):
            candidate = copy.deepcopy(protection)
            mutate(candidate)
            with self.assertRaises(ValueError):
                release.validate_branch_custody(self.policy, candidate, self.policy["codeOwners"])
        with self.assertRaises(ValueError):
            release.validate_branch_custody(self.policy, protection, "* @attacker\n")

    def test_checked_in_policy_denies_all_signing(self):
        policy = json.loads((Path(__file__).parent / "policy.json").read_text())
        with patch.dict(release.os.environ, self.env), patch.object(release, "api", side_effect=[self.run, self.artifact, self.comparison]):
            with self.assertRaisesRegex(ValueError, "inactive"):
                release.inspected(policy, self.request, signed=True)

    def test_previously_published_build_cannot_be_signed_as_new_candidate(self):
        self.policy["activationApproved"] = True
        self.policy["approvedCandidates"] = [{"request": self.request, "qualificationReviewed": True,
                                               "build": "26101526", "version": "2026.10.15-rc26"}]
        with patch.dict(release.os.environ, self.env), patch.object(release, "api", side_effect=[self.run, self.artifact, self.comparison]):
            with self.assertRaisesRegex(ValueError, "newer"):
                release.inspected(self.policy, self.request, signed=True)

    def test_duplicate_policy_fields_rejected(self):
        with self.assertRaises(ValueError):
            release.exact_json(b'{"activationApproved":false,"activationApproved":true}')

    def test_archive_paths_and_escaping_links_rejected(self):
        for name, value, mode in (("../escape", b"bad", stat.S_IFREG),
                                  ("AMPLIFai Phone.app/link", b"../../escape", stat.S_IFLNK),
                                  ("other.app/file", b"bad", stat.S_IFREG)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                archive = root / "app.zip"
                with zipfile.ZipFile(archive, "w") as zipped:
                    item = zipfile.ZipInfo(name)
                    item.external_attr = mode << 16
                    zipped.writestr(item, value)
                with self.assertRaises(ValueError):
                    release.unpack_app(archive, root, "AMPLIFai Phone")
                self.assertFalse((root.parent / "escape").exists())

    def test_valid_relative_framework_link_and_permissions_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "app.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                for name, value, mode in (("AMPLIFai Phone.app/Versions/A/binary", b"binary", stat.S_IFREG | 0o755),
                                          ("AMPLIFai Phone.app/current", b"Versions/A/binary", stat.S_IFLNK | 0o777)):
                    item = zipfile.ZipInfo(name)
                    item.external_attr = mode << 16
                    zipped.writestr(item, value)
            app = release.unpack_app(archive, root, "AMPLIFai Phone")
            self.assertEqual((app / "current").read_bytes(), b"binary")
            self.assertEqual((app / "Versions/A/binary").stat().st_mode & 0o777, 0o755)

    def test_changed_bundle_key_or_feed_rejected_before_native_tools(self):
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder)
            (app / "Contents").mkdir()
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"SUPublicEDKey": "wrong"}))
            with self.assertRaisesRegex(ValueError, "trust differs"):
                release.validate_app(app, {"version": "2026.10.15-rc26", "build": "26101526"}, "macos-arm64")

    def test_failure_output_never_echoes_credential_arguments(self):
        with self.assertRaises(ValueError) as caught:
            release.command(["/usr/bin/false", "fixture-password-do-not-echo"])
        self.assertNotIn("fixture-password", str(caught.exception))
        with patch.object(release.subprocess, "run", side_effect=subprocess.TimeoutExpired(["security", "fixture-password"], 1)):
            with self.assertRaises(ValueError) as timeout:
                release.command(["security", "fixture-password"])
        self.assertNotIn("fixture-password", str(timeout.exception))


if __name__ == "__main__":
    unittest.main()
