# Protected Mac release proposal — October 16

**Review candidate; activation is disabled.** Brian already has write access to the public distribution repository, `JmooreOsirs/amplifai-phone-companion`. No access elevation, Apple invitation, credential transfer, source migration, signing run, public release or feed change is part of this milestone.

Jeff's approved authority on October 16 is **“Brian releases reviewed commits independently (Recommended)”**. This supersedes the earlier per-run reviewer proposal. Source, workflow, policy/source authorization and CODEOWNERS changes require enforced protected PR review. Brian may then build, qualify, sign, notarize and publish that authorized immutable source without another human approving the run or its generated artifact hashes. Native producer/source-closure/finalizer/feed/publisher helpers are implemented in the pending integration PRs. Historical ARM and Intel candidates have qualification evidence. The new RC28 source still needs exact-candidate qualification; protected integration, credential provisioning and final signed-byte website promotion remain incomplete.

Jeff additionally authorized: **“Deploy when the required checks pass. No further routine deployment approval is needed.”** Qualified integration/publication may proceed under that standing authority; required independent review, exact source/byte custody, key handling and technical checks are still mandatory. No repeated deployment-consent request is needed.

## Live nonsecret configuration readback

October 16: strict `main` protection and two empty environments are now configured on the canonical repository. Main requires one independent PR approval, code-owner review, stale dismissal/latest-push approval and strict `release-control-checks` from GitHub Actions App `15368`; administrators are included, force pushes/deletion disabled. GitHub's personal-repository API rejected an explicitly empty user/team bypass-allowance block as organization-only; it was omitted and readback shows no bypass grants. The protected `release/native` branch is also established at qualified baseline `02670fa2ea2c4fbb09b422e7b8cc183e45126e52`; PR #5 targets it. Repository roles were not expanded.

`amplifai-macos-signing` and `amplifai-release-publication` each allow only the exact `main` branch and have no reviewers/timers/custom run approval. Both secret inventories were empty at the last metadata readback. This is configuration/API evidence, not an exercised forbidden-push or secret-release test. PR #4 is not merged, so effective CODEOWNERS and workflow installation on default main remain absent. `release/native` remains the protected qualified baseline until native PR #5 is reviewed and merged. PRs #4 and #5 are pending independent approval. Signing and publication flags remain false and `approvedSources` is empty. Effective reviewed integration, runtime enforcement evidence, one-time owner credential custody and exact RC28 qualification remain prerequisites. The existing website owner has supplied adapter/Preview work; final promotion using the signed public package/source/feed bytes is not yet qualified.

## Source and control separation

The retained qualified native baseline is `02670fa2ea2c4fbb09b422e7b8cc183e45126e52`. Public `main` is `262a8e0e23f8e21a912e4ff0cbdbdb44a363470d`, a publication lineage without current native scripts or workflows. Neither repository's default branch currently installs a release workflow. GitHub requires the `workflow_dispatch` file on the [default branch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).

Brian's newer Mac branch `cbd9e9c5a01f8ddc7260a4b3e9b4bfc317f1a6a4` and Windows branch `ac0164b9ca482605e6212dbc51084581201566e5` remain intact. Mac rc27 contains account-origin and contact-selection changes; it starts from rc26 source `257748f`, before the later Windows rc19 fixture/handoff receipts. Its Intel CI changes remove the success upload. A whole-branch merge would mix unrelated changes and remove current evidence.

Approved release model, with these established refs to integrate through protected review:

| Responsibility | Ref | Rule |
| --- | --- | --- |
| Reviewed workflow, policy and signer code | `JmooreOsirs/...:main` | Add infrastructure files only; keep existing publication source |
| Integrated native source | `JmooreOsirs/...:release/native` | Protected baseline `02670fa`; PR #5 integrates RC27 fixes and the new Mac design while preserving current Windows/Intel work |
| Public package destination | Existing JmooreOsirs releases | Preserve current bundle/key/feed authority |

There is no proposed repository migration. The organization copy has older public releases and Brian is an organization owner there; moving signing authority there would broaden the control boundary.

## What the candidate implements

`macos-protected-release.yml` operates on an **existing qualified build artifact**. It does not build arbitrary source in the signing job. The native integration preserves the existing architecture-specific build mechanisms and adds source/Legal staging plus the qualified-delivery producer. Historical artifacts demonstrate that path for their recorded source only; they are not automatically eligible for RC28 signing.

1. An allowed operator dispatches the control workflow from `main`, identifying the full source SHA, successful build run, immutable artifact ID/digest and CPU channel. PR/fork runs, wrong repositories/workflows/actors, reruns by a different actor and source predating the retained native lineage are rejected.
2. `dry-run` downloads and SHA256-verifies the exact GitHub artifact ZIP and retains an inspection receipt. It uses no Apple/Sparkle credentials, changes no release and makes no claim about package qualification. An expired artifact cannot be silently replaced.
3. `signed-candidate` requires enabled policy and an exact PR-reviewed `approvedSources` record: full source SHA, channel, fixed version/build, trusted build workflow path and `releaseAuthorized=true`. Run IDs, artifact IDs and generated hashes are not PR allowlist entries. A successful immutable artifact from that reviewed source's trusted CI contains a bounded `qualification.json`; its measured checks and app/source hashes determine eligibility without another policy edit. The checked-in policy has no authorized sources and `activationApproved=false`.
4. The signing job uses the exact-main-only protected environment **without required reviewers, wait timers or custom run-approval rules**. It checks out only the immutable control commit. Before Apple/Sparkle operations, it revalidates live control/source branch custody, exact build provenance and qualification payload hashes. It never runs candidate-source scripts, the collector, app, installer or source archive.
5. The producer audits again after all executable qualification; the signer independently re-audits and matches actual pre-sign source-mapped native hashes immediately before codesign. It validates paths/links, metadata, exact main/helper CPU, dependency closure and five matching Legal files. It checks existing Sparkle key continuity, signs nested code inside-out in a temporary keychain, notarizes/staples/assesses app and DMG, and verifies the final package signature.
6. `finalize.py` preserves all source payload bytes and appends only the predeclared signed-inventory/finalization evidence, with complete readback. `feeds.py` reuses the unchanged qualified feed generator and exact previous feed custody. Mode `signed-candidate` retains artifacts; mode `publish` additionally uses an isolated key-free publication job with same-run signer provenance, atomic exact-source tag creation, draft/public readback, live release ordering and previous-feed checks. `activationApproved=false` and `publicationEnabled=false` keep both inactive. These helpers have fixture evidence; no real signing or publication was run.

The actual credential-bearing path has **not** been exercised. Local tests verify rejection boundaries, safe extraction and configuration behavior. This proposal is not a qualified independent production release pipeline.

## Enforced custody and activation prerequisites

Configure before adding product credentials. Both source and control branches must have classic branch protection that the read-only Administration API can inspect:

- Required PR approval count at least one; required code-owner review; stale reviews dismissed; latest push approval by another person.
- Enforce restrictions for administrators. No users, teams or apps allowed to bypass PR requirements. Force pushes and deletion disabled.
- Strict required status check `release-control-checks` passes. The additive Ubuntu workflow runs the policy tests without secrets.
- `.github/CODEOWNERS` is exactly `* @JmooreOsirs @bac1876` on both refs. This deliberately protects every file, including workflows, the policy and CODEOWNERS itself. Any future owner change requires the same reviewed policy update. Both accounts' eligibility as code owners must be verified at activation.

At each signing/publication run, code reads both effective classic protections and CODEOWNERS; a weak `protected=true` marker is insufficient. GitHub's [branch-protection API](https://docs.github.com/en/rest/branches/branch-protection#get-branch-protection) requires **Administration:read** for this readback. Signing/inspection tokens retain only `contents:read` and `actions:read`; only the isolated publication job gets `contents:write`. A separate repository-scoped, expiring read-only audit credential is proposed below; no credential was created or uploaded. Its authority must not include branch modification, Actions dispatch, releases, account administration, or secret values.

Configure environment `amplifai-macos-signing` with only exact branch `main` (branch rule, no tag/wildcard rule) and no required reviewers, wait timers or custom approval rules. Runtime checks reject additional run-approval rules as inconsistent with Jeff's selected model. The obsolete environment administrator-bypass attestation has been removed; effective branch PR/admin/bypass protection is still read live. Repository administrators remain trusted configuration owners and can change platform configuration; Brian's canonical-repository role remains write, not admin.

Public repositories support these [environment controls](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments) on current Free plans. The initial audit found zero environments/rulesets/protection; the exact nonsecret controls above have since been applied and read back. Unknown/API-denied custody still fails closed. Before credentials, qualify actual direct-push/PR/self-review/secret-denial behavior and the accepted path; do not substitute metadata or local fixtures for those runtime checks.

## Proposed secret destinations — values never enter chat

Product credentials below belong **only** in the canonical repository's `amplifai-macos-signing` environment. The read-only `RELEASE_POLICY_AUDIT_TOKEN` is also needed in `amplifai-release-publication`; that environment receives no Apple/Sparkle credentials. Do not store product keys in repository-wide or organization-wide Actions secrets.

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

The app ZIP contains one correctly named `.app` with preserved executable modes and relative framework symlinks, without `__MACOSX` resource-fork siblings. The source ZIP contains the complete authored/dependency source offer, notices and build/source receipts; a bare `git archive` is insufficient. The integrated native workflows have produced historical qualified deliveries. RC28 must produce its own three-member delivery at the final clean source commit; historical qualification and policy tests do not qualify changed UI, font, source or version bytes.

| Channel | Bundle | Exact CPU | Feed retained | New build floor |
| --- | --- | --- | --- | --- |
| `macos-arm64` | `ai.satoris.amplifai.phone.candidate` | `arm64` | `/phone/macos/appcast.xml` | Greater than `26101526` |
| `macos-intel-test` | `ai.satoris.amplifai.phone.intel-candidate` | `x86_64` | `/phone/macos/intel-appcast.xml` | Greater than `261015261` |

Feed host remains `https://amplifai-database-engine.vercel.app`; signed-feed enforcement/key flags remain fixed. ARM keeps its legacy default Sparkle channel; Intel retains its separate Test channel. Windows remains on the existing unsigned Test workflow and signed manifest process. No new signing job touches Windows or its key/feed contracts.

## Complete build, signing and publication design

Brian uses his existing write role. Jeff's approved model authorizes independent runs after protected review of source and control code; **there is no second signing/release approval and no artifact-specific policy PR per build**. A new immutable source/version or changed recipe still needs normal protected review. The owner performs credential provisioning once after the protections exist, not per run. No extra reviewer or Apple account role is assumed.

| Stage | Inputs and implementation contract | Authority and output |
| --- | --- | --- |
| Review source/control | Protected PRs integrate RC27 fixes and the new Mac design with retained later Windows/Intel work; authorize full source SHA/channel/version and hash-pinned build recipe once | Required code-owner/PR/status review; no per-run gate |
| Build | Source checkout is the authorized full SHA from protected `release/native`; existing ARM `build_portable_candidate.sh --isolated` and Intel `build_macos_intel_candidate.py` remain the build mechanisms. Adapt their branch guards and version pins only in the reviewed native integration PR. Keep locked dependency/source checksums and CPU-specific standard hosted runners | No product credentials, no publication token; native/collector/frozen-worker checks and full source/legal closure must pass |
| Qualify delivery | Trusted producer measures the five checks, binds run/SHA/channel and exact app/source payloads in `qualification.json`, uploads the three-member artifact using pinned upload-artifact v4 | Immutable artifact ID/server digest and exact run provenance derive approval automatically; no human artifact allowlist |
| Sign/notarize | Current trusted control code revalidates custody/metadata/CPU/key, signs/notarizes/staples and independently verifies package signature | Exact-main-only signing environment, no run reviewers; read-only job token plus existing product secrets; signed candidate and final app inventory |
| Finalize source/feed | Trusted control adapter appends final signed inventory and nonsecret signing receipts to the complete source offer using the predeclared append-only evidence exclusions; verify full archive readback, source/native mapping and unchanged in-app Legal. Reuse existing `desktop_update_release.py.prepare` with the reviewed pins, official pinned Sparkle signer and retained prior feed bytes | Final package/source/feed hashes and byte records, signed feed, source-finalization receipt and exact rollback feed. Fail if any payload or closure changes |
| Publish package/source | Separate publication job checks out the same control SHA, revalidates branch custody and downloads the exact signer artifact from the same successful run/attempt. Independently verify receipt, package/feed signatures, final source binding and all byte counts. Create a new draft GitHub release targeting the authorized source SHA, upload without `--clobber`, read back every asset, then publish. Existing tag/release/asset with differing bytes is a hard stop, never overwrite | Exact-main-only `amplifai-release-publication` environment with no run reviewer; only this job gets `contents:write`; no Apple/Sparkle secrets. ARM stable/latest and Intel prerelease/Test remain separate |
| Promote website/feed | Existing website owner receives the exact signed feed/source/installer receipt; its reviewed promotion adapter verifies public GitHub bytes, writes only the canonical channel feed/download metadata, deploys the site through the existing project binding, then verifies both public feed origins and download bytes. Restore exact retained feed/deployment on failure | Existing website lane and scoped deployment authority; no private signing keys. Brian's approved source-release authority should carry through this adapter without another human run approval; cross-repository/app credential availability still needs proof |

Current implementation status: inspection, source-derived authorization, measured secret-free qualification producer, source finalizer, qualified feed-generator wrapper and isolated GitHub publisher are in PR #4. Its source build workflow files register the manual entry points on the default branch; dispatch them on the reviewed native source ref, which owns the actual scripts. PR #5 contains the source-closure/map emitter, five-file Legal staging and raw-app producer hook for both Mac channels. Historical ARM qualification and successful Intel run `38926522970` at `f9a0da3e10f6335be637fc72cea10491fb23f040` establish earlier source evidence; they do not qualify the new Mac UI/font/version bytes. The existing website owner has supplied its adapter and Preview work. Actual RC28 artifact qualification, hosted credential use/signing/notarization/publication, runtime denial checks and final signed-byte website promotion remain unqualified. See [RELEASE-DELIVERY-CONTRACT.md](RELEASE-DELIVERY-CONTRACT.md) for the artifact and website packet.

Confirmed planned RC28 identities are ARM `2026.10.16-rc28` / `26101628` and Intel `2026.10.16-rc28-intel1` / `261016281`. The source audit confirmed channel compatibility and monotonic ordering; the live feeds still match the retained RC26 hashes. The new packages remain unpublished and unqualified. This document does not create source authorization or alter build scripts/policy. The final reviewed source, channel-specific build identity, qualification record and website admission lists must agree before activation.

The publication job must have no access to the signing environment; the signing job must retain its read-only GitHub token. Pin download/upload actions and verify same-run/attempt provenance rather than downloading an artifact by a mutable name. Package/source releases become public before the new feed is offered. A package publication receipt is not a successful website promotion receipt or physical iPhone acceptance.

## Precise remaining activation steps

1. Obtain independent approval of the final PR #4 and PR #5 heads from a code owner other than their author/last pusher. Brian's `bac1876` review is the available other code owner. Re-read effective classic protection before normal merges; never bypass it to bootstrap the lane.
2. Merge reviewed control infrastructure to `main` and native integration to the existing protected `release/native`. Authorize the final exact source SHA, channel-specific version/build and trusted recipe through protected policy review. Keep `activationApproved=false`, `publicationEnabled=false` and `approvedSources=[]` during the current preparation slice.
3. Complete the runtime enforcement plan below while product secrets are absent. Configuration readback and local fixtures are supporting evidence, not runtime denials.
4. The credential owner completes the existing-key provisioning sequence below. A valid installed signing identity and working local notary profile do not prove private-key exportability or hosted custody.
5. Qualify the final clean RC28 source separately for ARM and Intel. Require current measured source/Legal/frozen-helper/architecture evidence and immutable delivery IDs/digests; retain older receipts under their original source identities.
6. Qualify hosted signing/notarization, temporary-key cleanup and exact package/source/feed readback under the reviewed activation policy. The existing website owner then consumes the signed-byte packet through its supplied promotion path and proves both-origin production feed/download readback and rollback custody. Preview adapter evidence alone does not satisfy this final promotion gate.
7. Jeff performs the physical transfer on this Mac with his iPhone, explicitly reviews and Saves each chosen source in his account. Production publication, installed app identity and physical/account acceptance remain separate receipts. Existing deploy-after-checks authority remains in force; these are technical and owner-custody steps, not additional routine release approvals.

### Runtime enforcement verification plan — not yet executed

Run after the reviewed baseline is installed and before product credentials are provisioned. Keep release activation/publication false. Reuse existing observed denials when the actor/ref/configuration is unchanged; a new app version alone does not require repeating custody probes. Use the actual operator identities, retain exact refs/SHAs/run URLs and denial messages, and never grant bypasses, force-push, disable protections, attempt self-approval or impersonate the independent reviewer.

| Check | Bounded probe and expected evidence |
| --- | --- |
| Direct push to both protected refs | Record each current tip. From a disposable local checkout, create an **empty commit** on that tip and attempt a normal fast-forward push to that exact ref as the intended operator. Expect server rejection and unchanged remote SHA. A no-op push or `--dry-run` is not proof. If unexpectedly accepted, stop before credentials/activation; record the harmless tip change and repair protection through the normal owner path. Do not reset or force-push it away. |
| Missing independent review | Reuse an existing recorded refusal when possible. Otherwise use a disposable documentation-only probe PR, never a release candidate. Attempt an ordinary merge while independent approval is missing; expect refusal. Preserve the result and close the unmerged probe. Do not attempt self-approval. Any unexpected acceptance stops activation; remove probe content later through normal protected review. |
| Required check enforcement | Use a separate nonshipping probe PR with a deliberately failing fixture in the existing `release-control-checks` job. After the normal independent review requirement is satisfied, an ordinary merge attempt must be refused for the failing check. Do not use admin merge, fabricate a successful status, or weaken the required check. Close the unmerged probe after capturing the refusal. |
| Stale/latest-push review | On a nonshipping probe PR, capture a normal independent approval, then push a harmless documentation change as the author. Verify the approval is dismissed/latest-push approval becomes required and an ordinary merge remains blocked until the normal independent review is renewed. Close the probe without merging. |
| Environment branch denial | Use one small reviewed probe workflow that has read-only permissions, checks out no candidate code and references only an explicitly noncredential `CUSTODY_PROBE` sentinel. For **each** named environment, dispatch it from an unprotected branch and from `release/native`; expect environment rejection before any step executes. Dispatch its reviewed version from `main`; require successful nonempty-sentinel validation with no value printed. This establishes the allowed/denied path without exposing product keys. Remove the sentinel and probe workflow after evidence, using normal protected review for workflow removal. |
| Accepted integration path | Record the ordinary PR #4/#5 merges with independent code-owner approval and green required checks. Re-read effective protections/CODEOWNERS on both resulting refs. No second approval of a generated build artifact is required. |

A rejected probe proves only its recorded actor/ref/configuration. Snapshot runtime environment/branch rules again immediately before provisioning and every real release; the control code continues to enforce live custody. The publisher’s immutable-tag checks and actual hosted cleanup retain their own publication/signing evidence; do not label them proven by this branch/environment probe.

### Owner-operated existing-key provisioning

Use a dedicated local Terminal session owned by Jeff after the gates above. No agent reads the secret values. Do not paste credentials into chat, `--body` arguments, shell variables, screenshots or checked-in files. Existing owner Keychain consent is required; no new certificate, Sparkle key, Apple role or account is created. The official Sparkle archive remains pinned to SHA256 `c2bf58aa8387266ac179357b1415d6f2635f044da8be41042af32425dae6da0c` (2.10.0).

Start a fresh Bash shell for this sequence, disable tracing and create one private temporary directory. These commands prepare custody only when the owner runs them; they have not been executed by this documentation update.

```sh
bash --noprofile --norc
set +x
set -euo pipefail
umask 077
release_repo='JmooreOsirs/amplifai-phone-companion'
provision_dir="$(mktemp -d "${TMPDIR:-/tmp}/amplifai-provision.XXXXXX")"
cleanup_provisioning() {
  rm -f -- "$provision_dir/developer-id.p12" "$provision_dir/sparkle-private-key"
  rmdir -- "$provision_dir"
}
trap cleanup_provisioning EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'Private export directory: %s\n' "$provision_dir"
```

In Keychain Access, select **Developer ID Application: Jeff Moore (WSBR54954W)** with its associated private key and export encrypted PKCS12 to `developer-id.p12` in the printed directory. Confirm the public certificate fingerprint is `59300C81CAA8FEDF07F60970E4621524243F83F4`. A certificate-only `.cer` is insufficient. Enter the export password in the native dialog; keep the same password for the secure prompt below.

The following pipeline emits **single-line base64 with no trailing newline** directly to GitHub's environment-secret stdin. The signer uses strict base64 decoding. Do not run the encoding command alone or add `tee`/logging.

```sh
test -s "$provision_dir/developer-id.p12"
python3 -c 'import base64, pathlib, sys; sys.stdout.buffer.write(base64.b64encode(pathlib.Path(sys.argv[1]).read_bytes()))' \
  "$provision_dir/developer-id.p12" | \
  gh secret set APPLE_CERTIFICATE_P12_BASE64 --repo "$release_repo" --env amplifai-macos-signing
gh secret set APPLE_CERTIFICATE_PASSWORD --repo "$release_repo" --env amplifai-macos-signing
```

The second command uses `gh`'s secure interactive input; enter the PKCS12 export password there. Do the same for the **existing** approved notarization account and app-specific password. The local Keychain profile name is not the password and is not a hosted credential.

```sh
gh secret set NOTARY_APPLE_ID --repo "$release_repo" --env amplifai-macos-signing
gh secret set NOTARY_APP_PASSWORD --repo "$release_repo" --env amplifai-macos-signing
```

Use the `generate_keys` executable from the already hash-verified official Sparkle 2.10.0 archive. The path prompt below is nonsecret. The explicit `-x` exports the existing account; omitting export mode is prohibited because the tool can generate a key. Jeff completes any native Keychain interaction. Redirect tool output to `/dev/null` so an unexpected diagnostic cannot print key material; a nonzero exit stops the sequence without uploading the export.

```sh
read -r -p 'Path to verified Sparkle 2.10.0 bin/generate_keys: ' sparkle_key_tool
test -x "$sparkle_key_tool"
if ! "$sparkle_key_tool" --account ai.satoris.amplifai.phone.candidate \
  -x "$provision_dir/sparkle-private-key" >/dev/null 2>&1; then
  printf '%s\n' 'Sparkle export failed; no upload performed. Inspect the native Keychain prompt and verified tool path locally.' >&2
  exit 1
fi
test -s "$provision_dir/sparkle-private-key"
gh secret set SPARKLE_PRIVATE_KEY --repo "$release_repo" --env amplifai-macos-signing \
  < "$provision_dir/sparkle-private-key"
```

Enter the separately approved, expiring, canonical-repository-only Administration:read audit credential through each prompt. It receives no write authority and is not the broad owner `gh` credential. Do not create it implicitly as part of these commands.

```sh
gh secret set RELEASE_POLICY_AUDIT_TOKEN --repo "$release_repo" --env amplifai-macos-signing
gh secret set RELEASE_POLICY_AUDIT_TOKEN --repo "$release_repo" --env amplifai-release-publication
```

Retain only secret **names and update timestamps**, destination environment, command exit status and the already-public certificate/Sparkle key identity. Secret-list metadata confirms upload, not correct private values or successful signing. There should be six names in the signing environment and only the audit token in publication, after removal of the noncredential probe sentinel. Reconcile unexpected names instead of deleting them blindly.

```sh
gh secret list --repo "$release_repo" --env amplifai-macos-signing
gh secret list --repo "$release_repo" --env amplifai-release-publication
exit
```

`exit` invokes cleanup of the two exact temporary exports and their directory; errors remain visible. If the shell or Mac terminates without running the trap, the owner removes those same exports before resuming. No private-file contents or hashes enter the release receipt. Hosted signing must still match the pinned public Developer ID identity and Sparkle key, authenticate notarization and exercise cleanup. These existing tools/runbook commands are sufficient; no new provisioning helper is needed.

## Infrastructure-only PR scope

PR #4 is the existing infrastructure branch from `main`; update it rather than create another port. Its scoped files include:

- `.github/CODEOWNERS`
- `.github/workflows/macos-protected-release.yml`
- `.github/workflows/release-control-checks.yml`
- `.github/workflows/macos-arm64-check.yml`, `.github/workflows/macos-intel.yml` — register the reviewed native manual build entry points; actual build scripts remain source-owned
- `release_control/policy.json`, `release_control/release.py`, `release_control/test_release.py`
- `source/script/verify_macos_architecture.py` — unchanged trusted stdlib verifier from the qualified native head; the signer imports it, so this explicit dependency is mandatory
- `release_control/producer.py`, `finalize.py`, `feeds.py`, `publish.py`, `test_pipeline.py`
- `source/script/desktop_update_release.py` and `docs/DESKTOP-UPDATE-SOURCE-PINS.json` — unchanged qualified generator/baseline; wrapper derives planned channel/version only from reviewed source policy
- `docs/PROTECTED-MAC-RELEASE.md`
- `docs/RELEASE-DELIVERY-CONTRACT.md`

No unrelated app source or workflow belongs in that PR. Keep activation false and approved sources empty. Verify the port with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s release_control -p 'test_*.py' -v`, workflow lint and `git diff --check`. Review/merge the infrastructure separately from native source integration and credential/protection activation. Reverting this additive PR removes the lane; it does not change installed apps, current releases or feeds. Disabling activation and removing environment credentials stops subsequent signing.
