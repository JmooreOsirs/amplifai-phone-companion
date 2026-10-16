# Protected Mac release proposal — October 16

**Review candidate; activation is disabled.** Brian already has write access to the public distribution repository, `JmooreOsirs/amplifai-phone-companion`. No access elevation, Apple invitation, credential transfer, source migration, signing run, public release or feed change is part of this milestone.

Jeff's approved authority on October 16 is **“Brian releases reviewed commits independently (Recommended)”**. This supersedes the earlier per-run reviewer proposal. Source, workflow, policy/source authorization and CODEOWNERS changes require enforced protected PR review. Brian may then build, qualify, sign, notarize and publish that authorized immutable source without another human approving the run or its generated artifact hashes. The implementation below removes that second gate; full live activation and the remaining producer/publication implementation are not yet complete.

## Source and control separation

The qualified native head is `02670fa2ea2c4fbb09b422e7b8cc183e45126e52`. Public `main` is `262a8e0e23f8e21a912e4ff0cbdbdb44a363470d`, a publication lineage without current native scripts or workflows. Neither repository's default branch currently installs a release workflow. GitHub requires the `workflow_dispatch` file on the [default branch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

Brian's newer Mac branch `cbd9e9c5a01f8ddc7260a4b3e9b4bfc317f1a6a4` and Windows branch `ac0164b9ca482605e6212dbc51084581201566e5` remain intact. Mac rc27 contains account-origin and contact-selection changes; it starts from rc26 source `257748f`, before the later Windows rc19 fixture/handoff receipts. Its Intel CI changes remove the success upload. A whole-branch merge would mix unrelated changes and remove current evidence.

Approved release model, with these exact refs to establish before activation:

| Responsibility | Ref | Rule |
| --- | --- | --- |
| Reviewed workflow, policy and signer code | `JmooreOsirs/...:main` | Add infrastructure files only; keep existing publication source |
| Integrated native source | `JmooreOsirs/...:release/native` | Descend from `02670fa`; separately review the rc27 product changes and preserve current Windows/Intel work |
| Public package destination | Existing JmooreOsirs releases | Preserve current bundle/key/feed authority |

There is no proposed repository migration. The organization copy has older public releases and Brian is an organization owner there; moving signing authority there would broaden the control boundary.

## What the candidate implements

`macos-protected-release.yml` operates on an **existing qualified build artifact**. It does not build arbitrary source in the signing job. The existing secret-free native build workflows remain unchanged. A future build-source integration must retain a qualified delivery artifact in the contract below; today's historical artifacts are not automatically eligible.

1. An allowed operator dispatches the control workflow from `main`, identifying the full source SHA, successful build run, immutable artifact ID/digest and CPU channel. PR/fork runs, wrong repositories/workflows/actors, reruns by a different actor and source predating the retained native lineage are rejected.
2. `dry-run` downloads and SHA256-verifies the exact GitHub artifact ZIP and retains an inspection receipt. It uses no Apple/Sparkle credentials, changes no release and makes no claim about package qualification. An expired artifact cannot be silently replaced.
3. `signed-candidate` requires enabled policy and an exact PR-reviewed `approvedSources` record: full source SHA, channel, fixed version/build, trusted build workflow path and `releaseAuthorized=true`. Run IDs, artifact IDs and generated hashes are not PR allowlist entries. A successful immutable artifact from that reviewed source's trusted CI contains a bounded `qualification.json`; its measured checks and app/source hashes determine eligibility without another policy edit. The checked-in policy has no authorized sources and `activationApproved=false`.
4. The signing job uses the exact-main-only protected environment **without required reviewers, wait timers or custom run-approval rules**. It checks out only the immutable control commit. Before Apple/Sparkle operations, it revalidates live control/source branch custody, exact build provenance and qualification payload hashes. It never runs candidate-source scripts, the collector, app, installer or source archive.
5. The trusted signer validates app paths/links, metadata, both exact main/helper CPU slices and the existing native architecture/dependency audit. It requires the five existing in-app Legal files. It verifies continuity with the embedded Sparkle public key before Developer ID signing; signs nested code inside-out in a temporary keychain; notarizes/staples/assesses the app and DMG; independently verifies the final Sparkle package signature; and retains the candidate plus final app inventory.
6. Current outputs are Actions artifacts only. The corresponding source payload is copied unchanged. Its final signed-inventory binding and source archive finalization remain explicit release gates (`sourceFinalizationRequired=true`). The complete promotion design below uses the same reviewed-source authority for publication, without a second run approval. Producer, finalizer and publication adapters remain implementation/qualification work; current code is not described as a completed independent production pipeline.

The actual credential-bearing path has **not** been exercised. Local tests verify rejection boundaries, safe extraction and configuration behavior. This proposal is not a qualified independent production release pipeline.

## Enforced custody and activation prerequisites

Configure before adding product credentials. Both source and control branches must have classic branch protection that the read-only Administration API can inspect:

- Required PR approval count at least one; required code-owner review; stale reviews dismissed; latest push approval by another person.
- Enforce restrictions for administrators. No users, teams or apps allowed to bypass PR requirements. Force pushes and deletion disabled.
- Strict required status check `release-control-checks` passes. The additive Ubuntu workflow runs the policy tests without secrets.
- `.github/CODEOWNERS` is exactly `* @JmooreOsirs @bac1876` on both refs. This deliberately protects every file, including workflows, the policy and CODEOWNERS itself. Any future owner change requires the same reviewed policy update. Both accounts' eligibility as code owners must be verified at activation.

At each signing run, `release.py` reads both effective classic protections and CODEOWNERS; a weak `protected=true` marker is insufficient. GitHub's [branch-protection API](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection) requires **Administration:read** for this readback. The normal job token retains only `contents:read` and `actions:read`. A separate repository-scoped, expiring read-only audit credential is proposed below; no credential was created or uploaded. Its authority must not include branch modification, Actions dispatch, releases, account administration, or secret values.

Configure environment `amplifai-macos-signing` with only exact branch `main` (branch rule, no tag/wildcard rule) and no required reviewers, wait timers or custom approval rules. Runtime checks reject additional run-approval rules as inconsistent with Jeff's selected model. The obsolete environment administrator-bypass attestation has been removed; effective branch PR/admin/bypass protection is still read live. Repository administrators remain trusted configuration owners and can change platform configuration; Brian's canonical-repository role remains write, not admin.

Public repositories support these [environment controls](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments) on current Free plans. Current live audit found **zero environments, zero rulesets and no branch protection**. Nothing is active yet. Unknown/API-denied custody fails closed. Activation qualification must prove a direct push is rejected, PR/self-review and weakened-policy cases are rejected, a PR/fork cannot obtain signing credentials, and the normal accepted path works.

## Proposed secret destinations — values never enter chat

All names below belong **only** in the canonical repository's `amplifai-macos-signing` environment. Do not store product keys in repository-wide or organization-wide Actions secrets.

| Name | Existing material / proposed authority |
| --- | --- |
| `APPLE_CERTIFICATE_P12_BASE64` | Existing Developer ID Application certificate **with its private key**, encrypted PKCS12; no new certificate |
| `APPLE_CERTIFICATE_PASSWORD` | Password protecting that PKCS12 |
| `NOTARY_APPLE_ID` | Existing authorized notarization account; Brian's requested email does not establish credential ownership |
| `NOTARY_APP_PASSWORD` | Existing approved app-specific notarization password; never the normal Apple password |
| `SPARKLE_PRIVATE_KEY` | Existing Sparkle key in the official signer's supported file/stdin encoding; never generate/rotate it here |
| `RELEASE_POLICY_AUDIT_TOKEN` | Separately approved, expiring repo-scoped Administration:read credential; no write authority |

The existing local `AMPLIFai-notary` profile is a Keychain record on Jeff's Mac, not a portable GitHub credential. The proposal chooses the documented app-specific-password route; an API-key route is a separate configuration change, not an implied new key. Brian's email `brian@searchnwa.com` alone does not supply the profile or Developer ID authority. [Apple Developer ID](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/) and [notarytool](https://developer.apple.com/documentation/technotes/tn3147-migrating-to-the-latest-notarization-tool) requirements must be satisfied by the existing owner; a CI operator need not receive the private keys personally.

Current exact signing identity is `Developer ID Application: Jeff Moore (WSBR54954W)`, public leaf SHA1 fingerprint `59300C81CAA8FEDF07F60970E4621524243F83F4`, rederived from the installed signed rc26 certificate. Sparkle 2.10.0 tool archive and signer binary hashes are pinned to the already-qualified release tooling. Key continuity uses the public key `vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs=`; [Sparkle recommends retaining existing keys](https://sparkle-project.org/documentation/).

Temporary credential files use a private directory under the disposable hosted runner's temp directory. The code deletes the keychain in `finally`; an `always()` workflow cleanup also removes surviving private directories. Forced cancellation cleanup has not been executed; hosted runner disposal is the final containment boundary. No self-hosted or owner Mac runner is allowed by this candidate.

## Artifact contract and deterministic channels

A PR-reviewed `policy.json.approvedSources` record contains `sourceCommit`, `channel`, `version`, `build`, `buildWorkflow` and `releaseAuthorized=true`. Approval is for the immutable source and trusted recipe; it is reusable across build attempts. Changing that source, recipe, control code or authorization requires protected review. A new CI run/artifact does not require a new human policy edit. No user-supplied URL, build script, signing tool, command or credential name is accepted.

The upstream immutable Actions artifact ZIP contains exactly `qualification.json`, one app ZIP and one complete source ZIP at its root. The qualification record has schema 1, exact source/channel/version/build, `buildRunId`, `privateDataUsed=false`, and strictly boolean passing `functional`, `architecture`, `frozenHelper`, `sourceClosure` and `legal` checks. `appArchive` and `sourceArchive` records bind exact name, integer byte count and lowercase SHA256. It is generated by the reviewed trusted CI workflow after measured checks pass, not supplied as a human assertion. The server artifact digest authenticates the whole record/payload package; the signer separately rechecks all member hashes and app identity/CPU/key.

The app ZIP contains one correctly named `.app` with preserved executable modes and relative framework symlinks, without `__MACOSX` resource-fork siblings. The source ZIP contains the complete authored/dependency source offer, notices and build/source receipts; a bare `git archive` is insufficient. Current historical workflows do not yet produce this three-member qualification contract. Therefore no real artifact is eligible merely because these policy tests pass.

| Channel | Bundle | Exact CPU | Feed retained | New build floor |
| --- | --- | --- | --- | --- |
| `macos-arm64` | `ai.satoris.amplifai.phone.candidate` | `arm64` | `/phone/macos/appcast.xml` | Greater than `26101526` |
| `macos-intel-test` | `ai.satoris.amplifai.phone.intel-candidate` | `x86_64` | `/phone/macos/intel-appcast.xml` | Greater than `261015261` |

Feed host remains `https://amplifai-database-engine.vercel.app`; signed-feed enforcement/key flags remain fixed. ARM keeps its legacy default Sparkle channel; Intel retains its separate Test channel. Windows remains on the existing unsigned Test workflow and signed manifest process. No new signing job touches Windows or its key/feed contracts.

## Complete build, signing and publication design

Brian uses his existing write role. Jeff's approved model authorizes independent runs after protected review of source and control code; **there is no second signing/release approval and no artifact-specific policy PR per build**. A new immutable source/version or changed recipe still needs normal protected review. The owner performs credential provisioning once after the protections exist, not per run. No extra reviewer or Apple account role is assumed.

| Stage | Inputs and implementation contract | Authority and output |
| --- | --- | --- |
| Review source/control | Protected PRs integrate rc27 fixes with retained later Windows/Intel work; authorize full source SHA/channel/version and hash-pinned build recipe once | Required code-owner/PR/status review; no per-run gate |
| Build | Source checkout is the authorized full SHA from protected `release/native`; existing ARM `build_portable_candidate.sh --isolated` and Intel `build_macos_intel_candidate.py` remain the build mechanisms. Adapt their branch guards and version pins only in the reviewed native integration PR. Keep locked dependency/source checksums and CPU-specific standard hosted runners | No product credentials, no publication token; native/collector/frozen-worker checks and full source/legal closure must pass |
| Qualify delivery | Trusted producer measures the five checks, binds run/SHA/channel and exact app/source payloads in `qualification.json`, uploads the three-member artifact using pinned upload-artifact v4 | Immutable artifact ID/server digest and exact run provenance derive approval automatically; no human artifact allowlist |
| Sign/notarize | Current trusted control code revalidates custody/metadata/CPU/key, signs/notarizes/staples and independently verifies package signature | Exact-main-only signing environment, no run reviewers; read-only job token plus existing product secrets; signed candidate and final app inventory |
| Finalize source/feed | Trusted control adapter appends final signed inventory and nonsecret signing receipts to the complete source offer using the predeclared append-only evidence exclusions; verify full archive readback, source/native mapping and unchanged in-app Legal. Reuse existing `desktop_update_release.py.prepare` with the reviewed pins, official pinned Sparkle signer and retained prior feed bytes | Final package/source/feed hashes and byte records, signed feed, source-finalization receipt and exact rollback feed. Fail if any payload or closure changes |
| Publish package/source | Separate publication job checks out the same control SHA, revalidates branch custody and downloads the exact signer artifact from the same successful run/attempt. Independently verify receipt, package/feed signatures, final source binding and all byte counts. Create a new draft GitHub release targeting the authorized source SHA, upload without `--clobber`, read back every asset, then publish. Existing tag/release/asset with differing bytes is a hard stop, never overwrite | Exact-main-only `amplifai-release-publication` environment with no run reviewer; only this job gets `contents:write`; no Apple/Sparkle secrets. ARM stable/latest and Intel prerelease/Test remain separate |
| Promote website/feed | Existing website owner receives the exact signed feed/source/installer receipt; its reviewed promotion adapter verifies public GitHub bytes, writes only the canonical channel feed/download metadata, deploys the site through the existing project binding, then verifies both public feed origins and download bytes. Restore exact retained feed/deployment on failure | Existing website lane and scoped deployment authority; no private signing keys. Brian's approved source-release authority should carry through this adapter without another human run approval; cross-repository/app credential availability still needs proof |

Current implementation status: inspection, source-derived authorization, qualification-record validation and the protected signer are in PR #4. The measured build-delivery producer, source-finalization/feed adapter, package publisher and website promotion adapter are **not implemented or live-qualified by this PR**. The table defines the entire requested path and its trust boundaries; the signing foundation alone does not fulfill the full production request. Do not activate a source until every required stage has implementation and acceptance evidence.

The publication job must have no access to the signing environment; the signing job must retain its read-only GitHub token. Pin download/upload actions and verify same-run/attempt provenance rather than downloading an artifact by a mutable name. Package/source releases become public before the new feed is offered. A package publication receipt is not a successful website promotion receipt or physical iPhone acceptance.

## Precise remaining activation steps

1. Finish the focused review and obtain the protected PR approval for #4 from a code owner other than its author/last pusher. Configure/read back strict classic protection on `main` before merge. Do not bypass the approval to bootstrap the lane. The current PR is authored by `JmooreOsirs`; Brian's `bac1876` review is the available other code owner, not a new collaborator grant.
2. Separately review and merge the integrated native source, preserving newer rc27 product fixes and later Windows/Intel work. Establish/protect `release/native`, install the exact CODEOWNERS/check workflow and measured build-delivery producer, and authorize its exact source SHA/recipe in a protected policy PR. No live source or candidate is authorized yet.
3. Configure/read back exact-main-only signing and publication environments with no per-run approval rules. Confirm both ref protections reject direct pushes, self/latest-push approvals, missing required checks, PR bypass grants and untrusted workflow refs. Current audit has no such live configuration; this PR update does not claim it was created.
4. After those checks, the credential owner exports **the existing** Developer ID identity and private key as an encrypted PKCS12 through Keychain Access's native export interaction. Provision it only into `amplifai-macos-signing`, with the same pinned public certificate fingerprint. A public `.cer` is insufficient. No export or owner Keychain consent has occurred here.
5. The credential owner authorizes one-time export of the existing Sparkle account `ai.satoris.amplifai.phone.candidate` using the qualified Sparkle 2.10 tool's explicit export mode (`--account … -x PRIVATE_FILE`), never its default generation mode. Use a private temporary location and send the file directly to the environment secret via `gh secret set SPARKLE_PRIVATE_KEY --repo JmooreOsirs/amplifai-phone-companion --env amplifai-macos-signing < PRIVATE_FILE`; delete the temporary export after verified provisioning. No private value enters chat or command arguments. This native export/consent interaction remains an owner prerequisite, not an approval to ask again about Brian's release model.
6. Provision the existing notarization account/app-specific password directly into the named environment secrets; the local `AMPLIFai-notary` Keychain profile cannot simply be named on a hosted runner. Provide the proposed expiring repository-scoped Administration:read audit credential directly into its environment secret. Use `gh secret set NAME --env amplifai-macos-signing --repo JmooreOsirs/amplifai-phone-companion` with secure local stdin/interactive entry. No GitHub/Apple role expansion or signing-key rotation is required. These values are not available in the new hosted environment now.
7. Implement and test the producer/finalizer/publisher/promotion adapters above before setting `activationApproved=true`; retain positive no-extra-approval evidence and all negative access/tamper tests. Run hosted signing/notary/cancellation-cleanup qualification with a newly approved source/build, then publish only that qualified new release. Establish the existing website owner's deployment credential/binding path separately; do not migrate feed host or add credentials blindly to make the status look complete.

## Exact infrastructure-only port onto main

Port only these files from the native candidate onto a fresh branch from `origin/main`:

- `.github/CODEOWNERS`
- `.github/workflows/macos-protected-release.yml`
- `.github/workflows/release-control-checks.yml`
- `release_control/policy.json`, `release_control/release.py`, `release_control/test_release.py`
- `source/script/verify_macos_architecture.py` — unchanged trusted stdlib verifier from the qualified native head; the signer imports it, so this explicit dependency is mandatory
- `docs/PROTECTED-MAC-RELEASE.md`

No other app source or existing workflow belongs in that PR. Keep activation false and approved sources empty. Verify the port with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s release_control -p 'test_*.py' -v`, workflow lint and `git diff --check`. Review/merge the infrastructure separately from native source integration and credential/protection activation. Reverting this additive PR removes the lane; it does not change installed apps, current releases or feeds. Disabling activation and removing environment credentials stops subsequent signing.
