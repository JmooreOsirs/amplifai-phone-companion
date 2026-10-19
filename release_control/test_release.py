"""Adversarial tests of the release credential and artifact boundaries."""
import copy
import base64
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
        self.env = {"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": self.policy["repository"],
                    "GITHUB_ACTOR": "bac1876", "GITHUB_TRIGGERING_ACTOR": "bac1876",
                    "GITHUB_REF": "refs/heads/main", "GITHUB_REF_PROTECTED": "true", "GITHUB_SHA": "d" * 40,
                    "GITHUB_WORKFLOW_REF": self.policy["repository"] + "/.github/workflows/macos-protected-release.yml@refs/heads/main"}
        self.request = {"runId": 11, "artifactId": 22, "sourceCommit": "a" * 40,
                        "artifactSha256": "b" * 64, "channel": "macos-arm64"}
        self.run = {"id": 11, "head_sha": "a" * 40, "head_branch": "release/native",
                    "head_repository": {"full_name": self.policy["repository"]},
                    "repository": {"full_name": self.policy["repository"]},
                    "actor": {"login": "bac1876"}, "triggering_actor": {"login": "bac1876"},
                    "path": ".github/workflows/macos-arm64-check.yml", "event": "workflow_dispatch",
                    "status": "completed", "conclusion": "success"}
        self.artifact = {"id": 22, "name": "qualified-macos-fixture", "workflow_run": {"id": 11, "head_sha": "a" * 40},
                         "expired": False, "size_in_bytes": 100, "digest": "sha256:" + "b" * 64}
        self.comparison = {"status": "ahead", "merge_base_commit": {"sha": self.policy["qualifiedAncestor"]}}
        self.environment = {"name": "amplifai-macos-signing", "protection_rules": [{"type": "branch_policy"}],
            "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}
        self.branches = {"branch_policies": [{"name": "main", "type": "branch"}]}
        self.source = {"sourceCommit": "a" * 40, "channel": "macos-arm64", "releaseAuthorized": True,
                       "buildWorkflow": ".github/workflows/macos-arm64-check.yml",
                       "version": "2026.10.19-rc29", "build": "26101929",
                       "qualificationProfile": release.PHONE_RUN_QUALIFICATION}
        self.protection = {"enforce_admins": {"enabled": True},
                           "required_pull_request_reviews": {"dismiss_stale_reviews": True, "require_code_owner_reviews": False,
                               "require_last_push_approval": False, "required_approving_review_count": 0,
                               "bypass_pull_request_allowances": {"users": [], "teams": [], "apps": []}},
                           "allow_force_pushes": {"enabled": False}, "allow_deletions": {"enabled": False},
                           "required_status_checks": {"strict": True, "contexts": ["release-control-checks"],
                               "checks": [{"context": "release-control-checks", "app_id": 15368}]}}

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

    def test_no_per_run_approval_and_protected_main_required(self):
        release.validate_protection(self.policy, self.environment, self.branches, {"protected": True}, self.env)
        for rule in ({"type": "required_reviewers"}, {"type": "wait_timer"}, {"type": "custom_deployment_protection_rule"}):
            candidate = copy.deepcopy(self.environment)
            candidate["protection_rules"].append(rule)
            with self.assertRaises(ValueError):
                release.validate_protection(self.policy, candidate, self.branches, {"protected": True}, self.env)
        with self.assertRaises(ValueError):
            release.validate_protection(self.policy, self.environment, self.branches, {"protected": False}, self.env)

    def test_wildcard_or_tag_deployment_policy_rejected(self):
        for item in ({"name": "*", "type": "branch"}, {"name": "main", "type": "tag"}):
            with self.assertRaises(ValueError):
                release.validate_protection(self.policy, self.environment, {"branch_policies": [item]}, {"protected": True}, self.env)

    def test_owner_either_policy_requires_exact_custody_and_no_bypass(self):
        release.validate_branch_custody(self.policy, self.protection, self.policy["codeOwners"])
        # GitHub omits bypass allowances on this personal repository.
        omitted = copy.deepcopy(self.protection)
        del omitted["required_pull_request_reviews"]["bypass_pull_request_allowances"]
        release.validate_branch_custody(self.policy, omitted, self.policy["codeOwners"])
        for mutate in (lambda p: p["enforce_admins"].update(enabled=False),
                       lambda p: p.update(required_pull_request_reviews=None),
                       lambda p: p["required_pull_request_reviews"].update(required_approving_review_count=1),
                       lambda p: p["required_pull_request_reviews"].update(required_approving_review_count=False),
                       lambda p: p["required_pull_request_reviews"].update(require_code_owner_reviews=True),
                       lambda p: p["required_pull_request_reviews"].update(require_last_push_approval=True),
                       lambda p: p["required_pull_request_reviews"].update(dismiss_stale_reviews=False),
                       lambda p: p["required_status_checks"].update(strict=False),
                       lambda p: p["required_status_checks"].update(checks=[]),
                       lambda p: p["required_status_checks"]["checks"][0].update(app_id=-1),
                       lambda p: p["required_status_checks"]["checks"][0].update(app_id=123),
                       lambda p: p["required_status_checks"]["checks"].append({"context": "release-control-checks", "app_id": -1}),
                       lambda p: p["allow_force_pushes"].update(enabled=True),
                       lambda p: p["allow_deletions"].update(enabled=True)):
            candidate = copy.deepcopy(self.protection)
            mutate(candidate)
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                release.validate_branch_custody(self.policy, candidate, self.policy["codeOwners"])
        for bypass in (None, [], {"users": None}, {"users": ["bac1876"]},
                       {"users": [{"login": "JmooreOsirs"}]}, {"teams": [1]},
                       {"apps": [15368]}, {"unknown": []}):
            candidate = copy.deepcopy(self.protection)
            candidate["required_pull_request_reviews"]["bypass_pull_request_allowances"] = bypass
            with self.subTest(bypass=bypass), self.assertRaises(ValueError):
                release.validate_branch_custody(self.policy, candidate, self.policy["codeOwners"])
        with self.assertRaises(ValueError):
            release.validate_branch_custody(self.policy, self.protection, "* @attacker\n")

    def test_release_authority_cannot_expand_actors_or_unpin_required_ci(self):
        for change in ({"operators": ["JmooreOsirs", "bac1876", "attacker"]},
                       {"operators": ["JmooreOsirs"]}, {"requiredChecks": []},
                       {"requiredCheckAppId": -1}, {"requiredCheckAppId": "15368"},
                       {"releaseAuthority": "reviewed-source-independent-release"},
                       {"codeOwners": "* @attacker\n"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                release.validate_release_authority(dict(self.policy, **change))

    def test_unapproved_source_cannot_sign(self):
        policy = json.loads((Path(__file__).parent / "policy.json").read_text())
        policy["activationApproved"] = True
        with patch.dict(release.os.environ, self.env), patch.object(release, "api", side_effect=[self.run, self.artifact, self.comparison]):
            with self.assertRaisesRegex(ValueError, "source/workflow not authorized"):
                release.inspected(policy, self.request, signed=True)

    def test_previously_published_build_cannot_be_signed_as_new_candidate(self):
        self.policy["activationApproved"] = True
        self.policy["approvedSources"] = [dict(self.source, build="26101526", version="2026.10.15-rc26")]
        with patch.dict(release.os.environ, self.env), patch.object(release, "api", side_effect=[self.run, self.artifact, self.comparison]):
            with self.assertRaisesRegex(ValueError, "newer"):
                release.inspected(self.policy, self.request, signed=True)

    def authorize(self, request=None, run=None, artifact=None, protection=None):
        owners = {"encoding": "base64", "content": base64.b64encode(self.policy["codeOwners"].encode()).decode()}
        responses = [run or self.run, artifact or self.artifact, self.comparison, self.environment,
                     self.branches, {"protected": True}, {"protected": True},
                     protection or self.protection, owners, self.protection, owners]
        with patch.dict(release.os.environ, self.env), patch.object(release, "api", side_effect=responses), \
                patch("authority.require_approved_merge") as approval:
            result = release.inspected(self.policy, request or self.request, signed=True)
            self.assertEqual([call.args[2:] for call in approval.call_args_list],
                             [(self.env["GITHUB_SHA"], "main"),
                              ((request or self.request)["sourceCommit"], "release/native")])
            return result

    def test_brian_can_release_two_ci_runs_of_same_reviewed_source_without_new_policy_approval(self):
        self.policy["activationApproved"] = True
        self.policy["approvedSources"] = [self.source]
        unchanged = copy.deepcopy(self.policy)
        receipt, approved = self.authorize()
        self.assertTrue(receipt["signingApproved"])
        self.assertEqual(approved, self.source)
        request = dict(self.request, runId=33, artifactId=44, artifactSha256="e" * 64)
        run = dict(self.run, id=33)
        artifact = dict(self.artifact, id=44, workflow_run={"id": 33, "head_sha": "a" * 40}, digest="sha256:" + "e" * 64)
        receipt, approved = self.authorize(request, run, artifact)
        self.assertTrue(receipt["signingApproved"])
        self.assertEqual(self.policy, unchanged)

    def test_owner_release_rejects_unapproved_source_actor_and_weak_live_custody(self):
        self.policy["activationApproved"] = True
        self.policy["approvedSources"] = [self.source]
        with self.assertRaises(ValueError):
            self.authorize(dict(self.request, sourceCommit="f" * 40), dict(self.run, head_sha="f" * 40),
                           dict(self.artifact, workflow_run={"id": 11, "head_sha": "f" * 40}))
        self.env["GITHUB_ACTOR"] = self.env["GITHUB_TRIGGERING_ACTOR"] = "unapproved-user"
        with self.assertRaises(ValueError):
            self.authorize()
        self.env["GITHUB_ACTOR"] = self.env["GITHUB_TRIGGERING_ACTOR"] = "bac1876"
        weak = copy.deepcopy(self.protection)
        weak["required_status_checks"]["strict"] = False
        with self.assertRaises(ValueError):
            self.authorize(protection=weak)

    def test_ci_qualification_binds_exact_run_source_and_all_required_checks(self):
        record = {"schema": 2, "sourceCommit": "a" * 40, "channel": "macos-arm64", "version": self.source["version"],
                  "build": self.source["build"], "buildRunId": 11, "privateDataUsed": False,
                  "qualificationProfile": release.PHONE_RUN_QUALIFICATION,
                  "checks": release.qualification_checks(self.source),
                  "appArchive": {"name": "app.zip", "bytes": 100, "sha256": "c" * 64},
                  "sourceArchive": {"name": "source.zip", "bytes": 200, "sha256": "d" * 64}}
        release.validate_delivery_record(record, self.source, self.request)
        for mutate in (lambda r: r.update(buildRunId=33), lambda r: r.update(sourceCommit="f" * 40),
                       lambda r: r["checks"].update(sourceClosure=False), lambda r: r["checks"].update(legal=1),
                       lambda r: r["appArchive"].update(name="../app.zip"),
                       lambda r: r.update(privateDataUsed=True)):
            candidate = copy.deepcopy(record)
            mutate(candidate)
            with self.assertRaises(ValueError):
                release.validate_delivery_record(candidate, self.source, self.request)

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
