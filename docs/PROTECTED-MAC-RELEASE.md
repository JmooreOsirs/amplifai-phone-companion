# Protected Mac release proposal — October 16

**Review candidate; activation is disabled.** Brian already has write access to the public distribution repository, `JmooreOsirs/amplifai-phone-companion`. No access elevation, Apple invitation, credential transfer, source migration, signing run, public release or feed change is part of this milestone.

## Source and control separation

The qualified native head is `02670fa2ea2c4fbb09b422e7b8cc183e45126e52`. Public `main` is `262a8e0e23f8e21a912e4ff0cbdbdb44a363470d`, a publication lineage without current native scripts or workflows. Neither repository's default branch currently installs a release workflow. GitHub requires the `workflow_dispatch` file on the [default branch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

Brian's newer Mac branch `cbd9e9c5a01f8ddc7260a4b3e9b4bfc317f1a6a4` and Windows branch `ac0164b9ca482605e6212dbc51084581201566e5` remain intact. Mac rc27 contains account-origin and contact-selection changes; it starts from rc26 source `257748f`, before the later Windows rc19 fixture/handoff receipts. Its Intel CI changes remove the success upload. A whole-branch merge would mix unrelated changes and remove current evidence.

Proposed refs, requiring owner approval before creation/protection:

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
3. `signed-candidate` additionally requires enabled policy and an exact reviewed candidate record with `qualificationReviewed=true`, a newer channel build, app/source ZIP byte records, and the fixed version. The checked-in policy has no approved candidates and `activationApproved=false`.
4. The signing job waits for the protected environment. It checks out **only the immutable control commit**. Before Apple/Sparkle operations, it revalidates live control/source branch custody and downloads the approved artifact. It never runs candidate-source scripts, the collector, app, installer or source archive.
5. The trusted signer validates app paths/links, metadata, both exact main/helper CPU slices and the existing native architecture/dependency audit. It requires the five existing in-app Legal files. It verifies continuity with the embedded Sparkle public key before Developer ID signing; signs nested code inside-out in a temporary keychain; notarizes/staples/assesses the app and DMG; independently verifies the final Sparkle package signature; and retains the candidate plus final app inventory.
6. Outputs are Actions artifacts only. The corresponding source payload is copied unchanged. Its final signed-inventory binding and source archive finalization remain explicit release gates (`sourceFinalizationRequired=true`). Feed authoring and public publication remain with the existing release/website owners and need separate authorization and immutable-byte verification.

The actual credential-bearing path has **not** been exercised. Local tests verify rejection boundaries, safe extraction and configuration behavior. This proposal is not a qualified independent production release pipeline.

## Enforced custody and activation prerequisites

Configure before adding product credentials. Both source and control branches must have classic branch protection that the read-only Administration API can inspect:

- Required PR approval count at least one; required code-owner review; stale reviews dismissed; latest push approval by another person.
- Enforce restrictions for administrators. No users, teams or apps allowed to bypass PR requirements. Force pushes and deletion disabled.
- Strict required status check `release-control-checks` passes. The additive Ubuntu workflow runs the policy tests without secrets.
- `.github/CODEOWNERS` is exactly `* @JmooreOsirs @bac1876` on both refs. This deliberately protects every file, including workflows, the policy and CODEOWNERS itself. Any future owner change requires the same reviewed policy update. Both accounts' eligibility as code owners must be verified at activation.

At each signing run, `release.py` reads both effective classic protections and CODEOWNERS; a weak `protected=true` marker is insufficient. GitHub's [branch-protection API](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection) requires **Administration:read** for this readback. The normal job token retains only `contents:read` and `actions:read`. A separate repository-scoped, expiring read-only audit credential is proposed below; no credential was created or uploaded. Its authority must not include branch modification, Actions dispatch, releases, account administration, or secret values.

Configure environment `amplifai-macos-signing` with only exact branch `main` (branch rule, no tag/wildcard rule), an independent required reviewer, prevent self-review, and administrator bypass disabled. Runtime reads verify reviewers, self-review prevention and exact deployment branch rules. The documented environment GET response does not expose administrator-bypass state: `adminBypassDisabledVerified` records a separate owner-verified setting and **does not detect subsequent drift of that UI-only setting**. Repository administrators remain trusted configuration owners. Disable activation if that setting changes; do not claim this flag proves platform enforcement.

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

A reviewed candidate record in `policy.json.approvedCandidates` contains: `request` (exact dispatch fields); `qualificationReviewed` true; `version`; `build`; and `appArchive` / `sourceArchive` records, each containing exact `name`, integer `bytes`, and lowercase `sha256`. These records are authored only after build/source qualification and reviewed through protected control PRs. No user-supplied URL, build script, signing tool, command or credential name is accepted.

The upstream Actions artifact ZIP must contain exactly those two regular ZIP files at its root. The app ZIP contains one correctly named `.app` with preserved executable modes and relative framework symlinks, without `__MACOSX` resource-fork siblings. The source ZIP must contain the complete reviewed authored/dependency source offer, notices and build/source receipts. A bare `git archive` is insufficient. Review binds that exact source payload to the pre-sign app and records qualification evidence before setting `qualificationReviewed=true`.

| Channel | Bundle | Exact CPU | Feed retained | New build floor |
| --- | --- | --- | --- | --- |
| `macos-arm64` | `ai.satoris.amplifai.phone.candidate` | `arm64` | `/phone/macos/appcast.xml` | Greater than `26101526` |
| `macos-intel-test` | `ai.satoris.amplifai.phone.intel-candidate` | `x86_64` | `/phone/macos/intel-appcast.xml` | Greater than `261015261` |

Feed host remains `https://amplifai-database-engine.vercel.app`; signed-feed enforcement/key flags remain fixed. ARM keeps its legacy default Sparkle channel; Intel retains its separate Test channel. Windows remains on the existing unsigned Test workflow and signed manifest process. No new signing job touches Windows or its key/feed contracts.

## Operator independence and public release boundary

Brian can select and run an **already reviewed immutable candidate** using his existing write role. He does not need Jeff to operate Xcode, handle a local Keychain prompt, export keys per run, or invite him to Apple administration. Under this proposal another trusted reviewer still approves every credential-bearing job. If Jeff is the only reviewer, **Jeff remains a per-run approval dependency**. To remove that personal dependency, authorize another qualified independent reviewer. Fully unattended one-person signing is a different security decision and is not promised here.

Public release/feed publication is deliberately absent from this workflow: its token has no `contents:write`, release API calls or website credentials. A future publishing job should use its own `amplifai-release-publication` environment and exact signed artifact/source/feed receipt checks, retain prior feed/deployment bytes, and never access Apple/Sparkle keys. Defining that separate activation/automation is beyond this candidate milestone; do not call it implemented.

## Exact infrastructure-only port onto main

Port only these files from the native candidate onto a fresh branch from `origin/main`:

- `.github/CODEOWNERS`
- `.github/workflows/macos-protected-release.yml`
- `.github/workflows/release-control-checks.yml`
- `release_control/policy.json`, `release_control/release.py`, `release_control/test_release.py`
- `source/script/verify_macos_architecture.py` — unchanged trusted stdlib verifier from the qualified native head; the signer imports it, so this explicit dependency is mandatory
- `docs/PROTECTED-MAC-RELEASE.md`

No other app source or existing workflow belongs in that PR. Keep activation false and approved candidates empty. Verify the port with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s release_control -p 'test_*.py' -v`, workflow lint and `git diff --check`. Review/merge the infrastructure separately from native source integration and credential/protection activation. Reverting this additive PR removes the lane; it does not change installed apps, current releases or feeds. Disabling activation and removing environment credentials stops subsequent signing.
