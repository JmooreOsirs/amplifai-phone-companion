"""Failure-path tests for reviewed local release provenance and signed delivery."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import local_release as local
import release


SOURCE = "8f7ec4d3de4235ab5d36d5175c29121e3a26548a"
CONTROL = "9fb2fa913292a5737310c2a2fe2381724211709f"
NOTARY_ID = "12345678-1234-1234-1234-123456789abc"


class Client:
    def __init__(self, responses):
        self.responses = responses

    def request_json(self, path, method="GET", payload=None):
        return copy.deepcopy(self.responses[path])


class LocalReleaseTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((Path(__file__).parent / "policy.json").read_text())
        self.source = next(row for row in self.policy["approvedSources"] if row["channel"] == "macos-arm64")
        self.request = {"sourceCommit": SOURCE, "runId": 39046812410, "artifactId": 12106277218,
                        "artifactSha256": "97dcc3423ff27a50f4e558f7c35ad065cad8a5ea3a8ca1670af0b2e1ad74d05b",
                        "channel": "macos-arm64"}
        self.context = {"operator": "JmooreOsirs", "controlCommit": CONTROL,
                        "artifactBytes": 378752848, "artifactName": "macos-arm64-release-" + SOURCE,
                        "qualificationRunner": "macos-15", "signerHostArchitecture": "arm64",
                        "candidateArchitecture": "arm64"}
        self.run = {"id": self.request["runId"], "head_sha": SOURCE, "head_branch": "release/native",
                    "head_repository": {"full_name": local.REPOSITORY}, "repository": {"full_name": local.REPOSITORY},
                    "actor": {"login": "JmooreOsirs"}, "triggering_actor": {"login": "JmooreOsirs"},
                    "path": self.source["buildWorkflow"], "event": "workflow_dispatch",
                    "status": "completed", "conclusion": "success"}
        self.artifact = {"id": self.request["artifactId"], "name": self.context["artifactName"],
                         "workflow_run": {"id": self.request["runId"], "head_sha": SOURCE},
                         "expired": False, "size_in_bytes": self.context["artifactBytes"],
                         "digest": "sha256:" + self.request["artifactSha256"]}
        self.comparison = {"status": "ahead", "merge_base_commit": {"sha": self.policy["qualifiedAncestor"]}}
        self.responses = {"user": {"login": "JmooreOsirs"},
                          f"repos/{local.REPOSITORY}/actions/runs/{self.request['runId']}": self.run,
                          f"repos/{local.REPOSITORY}/actions/artifacts/{self.request['artifactId']}": self.artifact,
                          f"repos/{local.REPOSITORY}/compare/{self.policy['qualifiedAncestor']}...{SOURCE}": self.comparison,
                          f"repos/{local.REPOSITORY}/actions/runs/{self.request['runId']}/jobs?per_page=100":
                          {"total_count": 1, "jobs": [{"name": "arm64", "status": "completed",
                                                       "conclusion": "success", "head_sha": SOURCE,
                                                       "labels": ["macos-15"]}]}}

    def authorize(self):
        with patch.object(local, "_require_reviewed_main"), patch.object(local, "_protected_refs"), \
                patch.object(local.platform, "machine", return_value="arm64"), \
                patch.object(local.platform, "system", return_value="Darwin"):
            return local.authorize_local(Client(self.responses), self.policy, self.request, CONTROL, release)

    def signing(self):
        identity = {"developerId": release.IDENTITY, "certificateSha1": release.CERTIFICATE_SHA1,
                    "teamId": release.TEAM, "sparklePublicKey": release.PUBLIC_KEY,
                    "sparkleAccount": local.SPARKLE_ACCOUNT, "notaryProfile": local.NOTARY_PROFILE}
        notary = {"id": NOTARY_ID, "status": "Accepted"}
        return {"signingMode": "local-keychain", "signingIdentity": identity,
                "appNotarization": notary, "dmgNotarization": notary,
                "notarized": True}

    def receipt_and_provenance(self):
        signing = self.signing()
        provenance = local.local_provenance(self.request, self.source, self.context, signing, release)
        receipt = {"schema": 1, "request": self.request, "controlCommit": CONTROL,
                   "operator": "JmooreOsirs", "executionMode": "local-keychain",
                   "signingApproved": True, "localProvenance": provenance, "signing": signing}
        return receipt, provenance

    def test_exact_qualified_source_and_runner_authorized(self):
        source, context = self.authorize()
        self.assertEqual(source, self.source)
        self.assertEqual(context["qualificationRunner"], "macos-15")
        self.assertEqual(context["artifactBytes"], 378752848)

    def test_local_policy_operator_and_immutable_build_failure_closed(self):
        for mutation in (lambda: self.policy["localRelease"].update(enabled=False),
                         lambda: self.responses["user"].update(login="bac1876"),
                         lambda: self.responses[f"repos/{local.REPOSITORY}/actions/runs/{self.request['runId']}"].update(head_sha="f" * 40),
                         lambda: self.responses[f"repos/{local.REPOSITORY}/actions/artifacts/{self.request['artifactId']}"].update(digest="sha256:" + "f" * 64)):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                saved = copy.deepcopy((self.policy, self.responses))
                mutation()
                try:
                    self.authorize()
                finally:
                    self.policy, self.responses = saved

    def test_runner_and_reviewed_cross_sign_rule(self):
        self.responses[f"repos/{local.REPOSITORY}/actions/runs/{self.request['runId']}/jobs?per_page=100"]["jobs"][0]["labels"] = ["macos-14"]
        with self.assertRaisesRegex(ValueError, "exact channel runner"):
            self.authorize()
        intel = dict(self.request, channel="macos-intel-test")
        with patch.object(local.platform, "machine", return_value="arm64"), \
                patch.object(local.platform, "system", return_value="Darwin"):
            self.assertEqual(local._host_architecture(self.policy, intel, "macos-15-intel", release),
                             ("arm64", "x86_64"))
            changed = copy.deepcopy(self.policy)
            changed["localRelease"]["crossSigning"]["qualificationRunner"] = "macos-15"
            with self.assertRaisesRegex(ValueError, "explicitly reviewed"):
                local._host_architecture(changed, intel, "macos-15-intel", release)
        with patch.object(local.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(ValueError, "Darwin"):
                local._host_architecture(self.policy, intel, "macos-15-intel", release)

    def test_protected_main_requires_exact_head_approval_and_green_check(self):
        pr = {"merged_at": "2026-10-17T15:50:05Z", "merge_commit_sha": CONTROL, "base": {"ref": "main"},
              "head": {"sha": "a" * 40}, "number": 6, "user": {"login": "JmooreOsirs"}}
        review = {"user": {"login": "bac1876"}, "state": "APPROVED", "commit_id": "a" * 40,
                  "submitted_at": "2026-10-17T15:48:29Z"}
        prefix = f"repos/{local.REPOSITORY}"
        data = {f"{prefix}/commits/{CONTROL}/pulls?per_page=100": [pr],
                f"{prefix}/pulls/6/reviews?per_page=100": [review],
                f"{prefix}/commits/{'a' * 40}/check-runs?per_page=100":
                {"total_count": 1, "check_runs": [{"name": "release-control-checks", "conclusion": "success"}]}}
        local._require_reviewed_main(Client(data), self.policy, CONTROL)
        data[f"{prefix}/pulls/6/reviews?per_page=100"][0]["commit_id"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "exact PR head"):
            local._require_reviewed_main(Client(data), self.policy, CONTROL)

    def test_clean_control_checkout_rejects_dirty_and_stale_main(self):
        branch = {"protected": True, "commit": {"sha": CONTROL}}
        github = Client({f"repos/{local.REPOSITORY}/branches/main": branch})
        proof = {("rev-parse", "--show-toplevel"): str(local.ROOT).encode(),
                 ("symbolic-ref", "--quiet", "HEAD"): b"refs/heads/main",
                 ("status", "--porcelain=v1", "--untracked-files=all"): b"",
                 ("ls-files", "--others", "--ignored", "--exclude-standard"): b"",
                 ("ls-files", "-v"): b"H release_control/local_release.py",
                 ("ls-files", "--stage"): b"100644 abc 0\trelease_control/local_release.py",
                 ("rev-parse", "HEAD"): CONTROL.encode()}
        with patch.object(local, "_git", side_effect=lambda *args: proof[args]):
            self.assertEqual(local.verify_control_checkout(github), CONTROL)
            proof[("status", "--porcelain=v1", "--untracked-files=all")] = b"?? override.py"
            with self.assertRaisesRegex(ValueError, "dirty"):
                local.verify_control_checkout(github)
            proof[("status", "--porcelain=v1", "--untracked-files=all")] = b""
            branch["commit"]["sha"] = "f" * 40
            with self.assertRaisesRegex(ValueError, "current protected main"):
                local.verify_control_checkout(github)

    def test_local_receipt_rejects_hosted_spoof_and_wrong_notary(self):
        receipt, provenance = self.receipt_and_provenance()
        local.validate_local_provenance(provenance, receipt, self.source, self.request, self.context, release)
        for mutate in (lambda p, r: p.update(signedArtifact={"runId": 1}),
                       lambda p, r: p["qualification"].update(artifactId=999),
                       lambda p, r: p["notary"]["dmg"].update(status="Rejected")):
            candidate = copy.deepcopy(provenance)
            signed = copy.deepcopy(receipt)
            signed["localProvenance"] = candidate
            mutate(candidate, signed)
            with self.assertRaises(ValueError):
                local.validate_local_provenance(candidate, signed, self.source, self.request, self.context, release)

    def test_signed_manifest_catches_changed_delivery_before_publication(self):
        receipt, provenance = self.receipt_and_provenance()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            root.chmod(0o700)
            (root / "release-receipt.json").write_bytes(local._canonical(receipt))
            (root / "package.dmg").write_bytes(b"signed-package-fixture")
            manifest = {"schema": 1, "kind": "local-keychain-signed-delivery", "localProvenance": provenance,
                        "files": local._delivery_files(root, release)}
            (root / local.MANIFEST).write_bytes(local._canonical(manifest))
            (root / local.MANIFEST_SIGNATURE).write_text("placeholder")
            with patch.object(local, "_verify_manifest_signature"):
                self.assertEqual(local.verify_signed_manifest(root, self.source, self.request,
                                                              self.context, root, release), provenance)
                (root / "package.dmg").write_bytes(b"changed-package-fixture")
                with self.assertRaisesRegex(ValueError, "inventory differs"):
                    local.verify_signed_manifest(root, self.source, self.request, self.context, root, release)

    def test_complete_archive_preserves_signed_manifest_and_feed_receipts(self):
        with tempfile.TemporaryDirectory() as folder:
            private = Path(folder)
            delivery = private / "delivery"
            delivery.mkdir()
            (delivery / "feed/rollback").mkdir(parents=True)
            members = {"release-receipt.json": b"receipt", "feed/release-receipt.json": b"feed receipt",
                       "feed/rollback/previous-appcast.xml": b"rollback", local.MANIFEST: b"manifest",
                       local.MANIFEST_SIGNATURE: b"signature"}
            for name, data in members.items():
                (delivery / name).write_bytes(data)
            archive = private / "local-signed-delivery.zip"
            local.write_delivery_archive(delivery, archive, release)
            with local.zipfile.ZipFile(archive) as zipped:
                self.assertEqual(set(zipped.namelist()), set(members))
                for name, data in members.items():
                    self.assertEqual(zipped.read(name), data)
            with self.assertRaisesRegex(ValueError, "outside sealed delivery"):
                local.write_delivery_archive(delivery, delivery / "second.zip", release)

    def test_publication_uses_scratch_and_leaves_signed_delivery_sealed(self):
        receipt, provenance = self.receipt_and_provenance()
        with tempfile.TemporaryDirectory() as folder:
            private = Path(folder)
            private.chmod(0o700)
            delivery = private / "delivery"
            delivery.mkdir(mode=0o700)
            (delivery / "feed").mkdir()
            (delivery / "release-receipt.json").write_bytes(local._canonical(receipt))
            (delivery / "feed/release-receipt.json").write_bytes(b'{}')
            (delivery / "package.dmg").write_bytes(b"signed-package")
            (delivery / local.MANIFEST).write_bytes(b"manifest")
            (delivery / local.MANIFEST_SIGNATURE).write_bytes(b"signature")
            before = {path.relative_to(delivery).as_posix(): path.read_bytes()
                      for path in delivery.rglob("*") if path.is_file()}
            output = private / "published"
            spec = {"package": {"sha256": release.digest(delivery / "package.dmg")}}

            class Publication:
                def validate_delivery(self, root, source, request, *, control_commit):
                    assert root == delivery and control_commit == CONTROL
                    return spec, [delivery / "package.dmg"]

                def verify_local_package(self, root, source, *, temp_root):
                    assert root == delivery
                    return {"mountedInventoryMatched": True, "targetCodeExecuted": False}

                def publish(self, source, files, root, result, repository, *, github):
                    assert root != delivery and root.joinpath("feed/release-receipt.json").read_bytes() == b'{}'
                    assert {path.name for path in files} == {"package.dmg", local.MANIFEST,
                                                          local.MANIFEST_SIGNATURE, "local-signed-delivery.zip"}
                    with local.zipfile.ZipFile(root / "local-signed-delivery.zip") as zipped:
                        assert set(zipped.namelist()) == set(before)
                    (root / "release-notes.txt").write_text("temporary publication notes")
                    return {"assets": []}

                def website_handoff(self, source, spec, signed, published, *, local_provenance):
                    assert local_provenance == provenance
                    return {"status": "website-owner-integration-required"}

            with patch.object(local, "verify_signed_manifest", return_value=provenance):
                local._publish_local(Client({}), self.request, self.context, self.source, delivery,
                                     output, private, release, Publication())
            self.assertEqual(before, {path.relative_to(delivery).as_posix(): path.read_bytes()
                                      for path in delivery.rglob("*") if path.is_file()})
            verification = json.loads((output / "mounted-package-verification.json").read_text())
            self.assertEqual(verification["packageSha256"], spec["package"]["sha256"])
            self.assertTrue(verification["verification"]["mountedInventoryMatched"])


if __name__ == "__main__":
    unittest.main()
