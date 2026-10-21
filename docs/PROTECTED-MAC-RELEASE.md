# Protected Mac release runbook — October 19

## Current authority and release state

Jeff authorizes **either `JmooreOsirs` or `bac1876` to approve source, control and release changes, including their own changes**. This supersedes the earlier independent-review requirement. Approval is recorded by an allowlisted actor's normal PR merge with successful required checks on the exact PR head. No fabricated self-review, second routine deployment approval or per-artifact human allowlist is needed.

This control update uses `releaseAuthority: "owner-approved-source-release"` and `requiredCheckAppId: 15368`. Live protection was updated and read back at `2026-10-19T19:04:20Z`, with every non-review field unchanged. This control update still requires its normal PR merge before release execution.

RC28 was signed through the local Keychain path, published and promoted on October 17 under control `067b2bc687e3af81d033923608679578814c535b`, source `8f7ec4d3de4235ab5d36d5175c29121e3a26548a`. The combined website receipt records deployment `dpl_ChSk6sFXE75hatGNYW5q9qtDgxWr` and `physicalAcceptance=false`. See [the retained RC28 receipt](RELEASE-DELIVERY-CONTRACT.md#historical-rc28-deliveries-and-publication).

RC34 native [PR #23](https://github.com/JmooreOsirs/amplifai-phone-companion/pull/23) merged normally by `JmooreOsirs` at `2026-10-21T20:01:23Z`, preserving candidate `12cedc1f0ed7a7ff70612dec6bc5727ddad16ec4` in final native source `8b4474bb42e6c15aeb59136f807bc37d8f09ff33`. Required exact-head CI run `39666011657` passed. The release identities are ARM `2026.10.21-rc34` / `26102134` and Intel Test `2026.10.21-rc34-intel1` / `261021341`. Current policy pins that final source and the verified public RC33 build/feed floors. RC34 release-mode artifacts, signing and publication are still pending.

## Source, control and signing custody

| Responsibility | Ref or mode | Contract |
| --- | --- | --- |
| Release workflows, policy and signer/publisher code | Protected `main` | Current approved control; exact source/channel authorization; normal PR merge and exact-head CI |
| Native source and build recipes | Protected `release/native` | Final authorized merge SHA; retained qualified ancestor `02670fa2ea2c4fbb09b422e7b8cc183e45126e52`; separate ARM/Intel build mechanisms |
| Local signing | Jeff's Mac | Existing Developer ID, Sparkle Keychain account and `AMPLIFai-notary`; operator `JmooreOsirs` only |
| Hosted signing | Exact-main signing environment | Existing isolated workflow; complete hosted credential custody/signing remains unproven |
| Public packages and website | Existing GitHub releases and website owner | Exact signed bytes, previous-feed custody, combined deployment and public readback |

Local signing is implemented and exercised for RC28. It has same-user Mac custody; disposable hosted-runner containment, environment secret isolation and a separate key-free publication job are hosted assurances. Either approval actor may authorize changes; this does not expand local Keychain access. Follow [LOCAL-MAC-RELEASE.md](LOCAL-MAC-RELEASE.md).

Control-only changes preserve already-qualified native payloads. Changed native source, version or payload requires its own exact authorization and qualification. Execute producer code from the authorized native SHA and signer/publisher code from current protected main; do not substitute newer helpers into a frozen candidate. Keep the signing control SHA stable through publication.

## Enforced custody

Both `main` and `release/native` retain classic branch protection with these exact review settings for this personal repository:

- A PR is required; `required_approving_review_count=0`, `require_code_owner_reviews=false`, `require_last_push_approval=false`, and stale-review dismissal remains enabled.
- Administrator enforcement stays enabled. No users, teams or apps have bypass allowances. Force pushes and deletion stay disabled.
- Strict required `release-control-checks` is pinned to GitHub Actions App `15368`.
- `.github/CODEOWNERS` remains exactly `* @JmooreOsirs @bac1876` for routing. Both effective files must match policy.

Actor-specific branch-protection bypass lists are unsupported for this personal repository. Empty bypass lists are required; the zero-review setting lets either approved owner merge their own PR after CI. Runtime checks require the authoritative merged PR, exact repository/base/head/merge identities and allowlisted `merged_by` action. Required checks must be the latest completed successful attempts on that exact PR head from App `15368`; missing, forged-app, stale-success or newer failed/incomplete checks are rejected. This preserves explicit owner authority without asserting a GitHub self-review occurred.

The runtime reads effective protection and CODEOWNERS for both refs; a `protected=true` marker alone is insufficient. Unknown or denied custody readback fails closed. Current collaborators are Jeff (admin) and Brian (write); this update grants no repository role. Administrators remain trusted configuration owners and can change platform settings.

`amplifai-macos-signing` and `amplifai-release-publication` allow only exact `main`, with no reviewers, wait timers or custom approval rules. Hosted inspection/signing use read-only contents/actions tokens; only the isolated publication job receives `contents:write`. Live branch-protection readback additionally requires the existing approved repository-scoped `Administration:read` audit credential. Its provisioning remains unproven for hosted execution; this runbook grants no new credential authority.

## Artifact contract and channels

Each approved source record binds `sourceCommit`, `channel`, `version`, `build`, `buildWorkflow`, `qualificationProfile` and `releaseAuthorized=true`. It authorizes an immutable source and recipe across build attempts. Generated run IDs, artifact IDs and hashes derive from trusted successful CI and need no new human policy edit.

The immutable Actions artifact contains exactly `qualification.json`, `app.zip` and `source.zip`. New sources require schema 2 and `phone-run-recovery-v1`: the five base measured checks plus collector model, window lifecycle, bridge timing and bridge recovery. Only the two unchanged historical RC28 tuples retain schema-1 compatibility. The signer verifies the server ZIP digest, all payload hashes, identity/architecture/key and pre-sign source mapping. See [RELEASE-DELIVERY-CONTRACT.md](RELEASE-DELIVERY-CONTRACT.md#phone-run-and-recovery-qualification-profile).

The app preserves executable modes and relative framework symlinks. The complete source offer includes authored/dependency source, notices and receipts; a bare git archive is insufficient. Finalization preserves source payload bytes and appends only declared evidence. No signing step executes candidate app/source code.

| Channel | Bundle | CPU | Feed | Published RC28 floor |
| --- | --- | --- | --- | --- |
| `macos-arm64` | `ai.satoris.amplifai.phone.candidate` | `arm64` | `/phone/macos/appcast.xml` | `26101628` |
| `macos-intel-test` | `ai.satoris.amplifai.phone.intel-candidate` | `x86_64` | `/phone/macos/intel-appcast.xml` | `261016281` |

Revalidate live release ordering and previous feed bytes at execution. The RC26 dependency/source-closure baseline is separate from these publication floors and is unchanged. Feed host remains `https://amplifai-database-engine.vercel.app`; ARM retains its legacy default Sparkle channel and Intel its Test channel. Existing Windows contracts remain unchanged.

## Hosted execution path

1. An allowed operator dispatches the exact-main control workflow with the full source SHA, successful trusted build run, immutable artifact ID/digest and channel. Reject fork/PR builds, wrong repositories/workflows/actors, mismatched rerun actors and sources outside the retained lineage.
2. `dry-run` verifies/downloads the qualified artifact and retains an inspection receipt without using Apple/Sparkle credentials or changing a release.
3. `signed-candidate` requires enabled exact source authorization and qualifying payloads, revalidates live custody, then signs nested code inside-out in a temporary keychain, notarizes/staples/assesses app and DMG and verifies signatures. Existing public Developer ID and Sparkle identities must match.
4. Finalization appends only declared inventory/evidence and verifies complete source readback; feed generation uses the existing qualified generator and exact retained previous feed bytes.
5. `publish` uses the separate key-free publication job and exact same-run/attempt signer artifact. It verifies signatures and every asset, checks live channel ordering, atomically creates a new exact-source tag, uploads without clobbering and reads draft/public bytes back. Existing tags/releases are inspected rather than overwritten or deleted automatically.
6. The website owner consumes the resulting `website-handoff.json`, verifies signed bytes and expected previous state, updates feeds/acquisition/admission together and proves production readback. Native publication alone does not establish website deployment.

Same-channel concurrency serializes this publisher. GitHub has no atomic cross-release compare-and-swap across other publishing tools; live order/feed/tag checks detect observed changes, and administrators remain outside that mutex. Do not claim platform-enforced tag immutability without separate configuration evidence. Hosted credential-bearing signing/publication and forced-cancellation cleanup remain unexercised; successful local RC28 publication does not establish those hosted properties.

## Remaining RC29 steps

1. Complete the tested owner-approval policy and live protection readback, then normally merge the source/control PRs after required checks. Pin the final native merge SHA, intended channel identities, qualification profile and fresh RC28 feed floors in protected main.
2. Dispatch both architecture-specific release builds from that authorized native SHA. Retain actual successful run IDs, immutable artifact IDs/server digests and schema-2 app/source payload receipts.
3. Inspect, sign and notarize those exact qualified deliveries through the existing local path; preserve source bytes and the signing control SHA. Make the signed candidate available for Jeff's physical iPhone run; retain the actual installed-candidate and account results.
4. Publish the sealed channel deliveries and hand both packets to the existing website owner for combined production promotion and both-origin byte readback. Preserve rollback bytes and inspect retained partial release state before a retry.
5. Record Jeff's physical transfer on this Mac with his iPhone and his explicit review/Save of chosen sources. Do not perform consent or Save for him. Preserve those physical/account results separately from CI, signing and deployment; publication does not stand in for them.

Standing deploy-after-checks authority applies throughout. Exact database migration approval and physical/account participation remain separate when needed; no extra routine release approval is introduced.

### Runtime custody evidence and limits

October 17 receipts are historical evidence for their recorded configuration: normal direct pushes to both refs received GH006 with unchanged tips; green unreviewed PR #7 was refused for missing code-owner review; PRs #4/#5/#6 merged after exact-head independent approvals. The prior required-review setting has been explicitly superseded by the October 19 owner-approval policy and is not a current release prerequisite.

The October 19 protection snapshot and proposed changes are retained under `owner-either-approval` in the canonical phone/Google review evidence folder. Record the mutation/readback receipt there when applied. That readback, actual normal merges and runtime validation establish the new configuration; this document alone does not. Separate failing-check, stale-review, off-main secret-access and forced-cancellation probes have not been independently exercised. Do not introduce probe-only PRs or approval layers solely to refresh historical evidence. Repair an actual failed runtime check or changed risk and retain its bounded evidence.

## Hosted secret destinations — values never enter chat

For hosted execution, product credentials below belong **only** in the canonical repository's `amplifai-macos-signing` environment. The read-only `RELEASE_POLICY_AUDIT_TOKEN` is also needed in `amplifai-release-publication`; that environment receives no Apple/Sparkle credentials. Do not store product keys in repository-wide or organization-wide Actions secrets.

| Name | Existing material / proposed authority |
| --- | --- |
| `APPLE_CERTIFICATE_P12_BASE64` | Existing Developer ID Application certificate **with its private key**, encrypted PKCS12; no new certificate |
| `APPLE_CERTIFICATE_PASSWORD` | Password protecting that PKCS12 |
| `NOTARY_APPLE_ID` | Existing authorized notarization account; Brian's requested email does not establish credential ownership |
| `NOTARY_APP_PASSWORD` | Existing approved app-specific notarization password; never the normal Apple password |
| `SPARKLE_PRIVATE_KEY` | Existing Sparkle key in the official signer's supported file/stdin encoding; never generate/rotate it here |
| `RELEASE_POLICY_AUDIT_TOKEN` | Existing approved, expiring repo-scoped Administration:read credential when available; no write authority or new grant from this runbook |

The existing local `AMPLIFai-notary` profile is a Keychain record on Jeff's Mac, not a portable GitHub credential. The hosted runbook uses the documented app-specific-password route; an API-key route is a separate configuration change, not an implied new key. Brian's email `brian@searchnwa.com` alone does not supply the profile or Developer ID authority. [Apple Developer ID](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/) and [notarytool](https://developer.apple.com/documentation/technotes/tn3147-migrating-to-the-latest-notarization-tool) requirements must be satisfied by the existing owner; a CI operator need not receive the private keys personally.

Current exact signing identity is `Developer ID Application: Jeff Moore (WSBR54954W)`, public leaf SHA1 fingerprint `59300C81CAA8FEDF07F60970E4621524243F83F4`, rederived from the installed signed rc26 certificate. Sparkle 2.10.0 tool archive and signer binary hashes are pinned to the already-qualified release tooling. Key continuity uses the public key `vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs=`; [Sparkle recommends retaining existing keys](https://sparkle-project.org/documentation/).

Temporary credential files use a private directory under the disposable hosted runner's temp directory. The code deletes the keychain in `finally`; an `always()` workflow cleanup also removes surviving private directories. Forced cancellation cleanup has not been executed; hosted runner disposal is the final containment boundary. The hosted workflow does not permit a self-hosted or owner Mac runner. The separately authorized local mode runs directly on Jeff's Mac under its own [custody contract](LOCAL-MAC-RELEASE.md); hosted disposal and job-isolation claims do not apply to it.

### Hosted secure setup from existing owner-held material

This sequence is for the hosted route; local Keychain mode does not require exporting the Developer ID identity or notarization profile into GitHub. Routine no-value-output hosted provisioning may proceed under Jeff's existing deploy authority after the recorded custody checks, using already-approved material. The credential owner must complete native Keychain export/consent and password entry where those cannot be performed without them; a valid installed identity and local notary profile do not prove hosted custody. Do not paste values into chat, `--body` arguments, shell variables, screenshots or checked-in files. If the scoped Administration:read audit credential is unavailable, stop that step until an approved credential is supplied; this runbook grants no token. No new certificate, Sparkle key, Apple role or account is created. The official Sparkle archive remains pinned to SHA256 `c2bf58aa8387266ac179357b1415d6f2635f044da8be41042af32425dae6da0c` (2.10.0).

Start a fresh Bash shell for this sequence, disable tracing and create one private temporary directory. The credential owner controls native export and secure password entry; a trusted operator may perform the no-value-output upload steps with existing approved material. This documentation update executes none of these commands. The existing Sparkle export/upload/cleanup already succeeded on October 17; reuse its receipt rather than repeating it solely to refresh this document.

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

Retain only secret **names and update timestamps**, destination environment, command exit status and the already-public certificate/Sparkle key identity. Secret-list metadata confirms upload, not correct private values or successful signing. There should be six names in the signing environment and only the audit token in publication. Reconcile unexpected names instead of deleting them blindly.

```sh
gh secret list --repo "$release_repo" --env amplifai-macos-signing
gh secret list --repo "$release_repo" --env amplifai-release-publication
exit
```

`exit` invokes cleanup of the two exact temporary exports and their directory; errors remain visible. If the shell or Mac terminates without running the trap, the owner removes those same exports before resuming. No private-file contents or hashes enter the release receipt. Hosted signing must still match the pinned public Developer ID identity and Sparkle key, authenticate notarization and exercise cleanup. These existing tools/runbook commands are sufficient; no new provisioning helper is needed.

## Control verification and rollback

Run the existing control suite with `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s release_control -p 'test_*.py' -v`. Source qualification and release evidence remain separately bound to their actual source/channel/build.

A normal owner-approved policy PR restoring false activation/publication flags and empty approved sources stops subsequent signing/publication. Existing published packages, feeds and installed apps remain separate state. Preserve exact prior feed/deployment receipts for rollback; retire hosted environment credentials only through existing owner custody procedures.
