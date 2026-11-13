# Local Mac release path

This path signs an already qualified AMPLIFai Phone native build with the existing Developer ID and Sparkle keys in the owner's macOS Keychain. It requires the owner-approved release policy on current protected `main`. Either `JmooreOsirs` or `bac1876` may approve source/control changes, including their own, through a normal PR merge after required CI succeeds. Local Keychain execution remains restricted to `JmooreOsirs`. The hosted workflow remains a separate release path. A local Keychain release has same-user Mac custody; it does not claim the hosted runner's key isolation.

## Before any local release

1. Use a private checkout of current protected `main` with no modified, untracked, ignored, linked, or assume-unchanged control files. Run the CLI with `python3 -I -B`. The CLI checks current protected main, the authoritative merged PR and allowlisted `merged_by` actor, the latest successful checks on the exact PR head from GitHub Actions App `15368`, both protected refs and effective CODEOWNERS again at every phase. A newer failed or incomplete attempt cannot be replaced with an older success.
2. Use the existing `gh` login for `JmooreOsirs` on `github.com`. Do not supply `GH_TOKEN`, `GITHUB_TOKEN`, `GH_HOST`, hosted workflow variables, `PYTHONPATH`, or signing keys in the process environment. The CLI uses `gh api --hostname github.com` with the installed login and does not read or print the token.
3. Keep the existing Developer ID identity, `AMPLIFai-notary` profile, and `ai.satoris.amplifai.phone.candidate` Sparkle account available to the same macOS user. Normal Keychain prompts are part of key use. This path does not export a private key, create a credential, add a runner or role, or weaken branch protection.
4. Use a new absolute 0700 scratch directory and distinct new output directories outside the checkout. Put the signed delivery, scratch, and publication output in separate paths. Use the merged control implementation and exact source authorization. Keep that control SHA unchanged from signing through publication so the sealed manifest remains valid.

## November 13 RC36 Apple Silicon authorization

Native PR #54 merged normally to `e269653e57b7a5ab6c9eaab215a1e5ea32c26b33`,
preserving reviewed head `c79bf266ab5a9afdbeacd739b39cb796cf70e5a4`.
Required exact-head CI run `43493622975` passed. The new row authorizes only
`macos-arm64`, version `2026.11.13-rc36`, build `26111336`, with
`phone-run-recovery-v1`. Intel, Windows and Android release settings are unchanged.

RC36 enables three explicit password attempts within one approved live backup
acquisition, fresh authority checks before each local submission, exact input
handling and truthful password-or-key failure copy. Integrity checks remain
mandatory. Six prompt-driven frozen-code synthetic tests matched 14 packaged
modules and verified one acquisition and cleanup. Full local portable preview
qualification passed; this is not a release-mode CI artifact or customer-device
acceptance. The reported customer's actual encryption failure remains unproven.

The ARM rollback/build floor is RC35 `26102235`. Its GitHub appcast and both
website origins returned the same 1,561 bytes with SHA-256
`447ac209490b8d62b61b5bc11493b496f0f8c17cde71c16cc280f2f98da6584c`.
Apple notarization history access succeeded on November 13 after an earlier
agreement error. No RC36 notarization or publication is claimed by this policy.
Use the new release-mode run/artifact IDs and digest for the exact native merge
above; never substitute the local preview or historical RC35 identifiers.

After public signed artifacts exist, coordinate ARM-only website admission,
download and feed promotion using the verified publication handoff. Preserve
the RC35 rollback assets and all other channel bytes. Physical-device testing
remains a separate result.

## October 22 RC35 authorization (historical)

Native PR #29 merged normally at `2026-10-22T17:27:13Z` to
`dfc80751b8a4a5b16fb67833727130fb2ec3d44f`, preserving reviewed head
`2e1b909f92699ebc899bc5bfe37ba3ce8dd9b3d1`. Exact-head required CI run
`39821957136` passed. This policy authorizes ARM `2026.10.22-rc35` /
`26102235` and Intel Test `2026.10.22-rc35-intel1` / `261022351` under
`phone-run-recovery-v1`; qualification now includes the strict SQLite compatibility
suites and dense full-size synthetic Messages extraction.

The current RC34 feed bytes match at both website origins, and their existing
Ed25519 signatures were verified before pinning them as rollback/build floors.
The required website receiver is live at source
`7d364aa3d9c19f412ced046badb3e756a5104759`, Production deployment
`dpl_j9TYiRwtj3RaG1BWGjq4QbYLdKTG`. Native build, signing, notarization,
publication and final website pointer promotion are still pending. Use actual
RC35 release-mode artifact IDs and hashes with the command template below;
historical identifiers never authorize these packages. Jeff's renewed deployment
authority is recorded in the native delivery contract. Physical testing follows
publication and remains separately owner controlled.

## Historical RC34 candidate and command template

RC28 was signed locally, published and promoted on October 17 with control `067b2bc687e3af81d033923608679578814c535b`; see the [historical delivery receipt](RELEASE-DELIVERY-CONTRACT.md#historical-rc28-deliveries-and-publication). Those artifact IDs must not be reused as RC34 inputs.

RC34 native [PR #23](https://github.com/JmooreOsirs/amplifai-phone-companion/pull/23) merged normally by `JmooreOsirs` at `2026-10-21T20:01:23Z`, preserving candidate `12cedc1f0ed7a7ff70612dec6bc5727ddad16ec4` in final native source `8b4474bb42e6c15aeb59136f807bc37d8f09ff33`. Required exact-head CI run `39666011657` passed. The release identities are ARM `2026.10.21-rc34` / `26102134` and Intel Test `2026.10.21-rc34-intel1` / `261021341`. Current policy pins that final source and the verified public RC33 build/feed floors. RC34 release-mode artifacts, signing and publication are still pending.

| Channel | Version | Build |
| --- | --- | --- |
| `macos-arm64` | `2026.10.21-rc34` | `26102134` |
| `macos-intel-test` | `2026.10.21-rc34-intel1` | `261021341` |

After normal source/control merges, take the **final native merge SHA** from `approvedSources` and each channel's successful release-mode run, immutable artifact ID and complete artifact ZIP SHA-256 from its qualification receipt. The PR head above is a candidate identifier, not the final release authorization. The CLI validates the artifact's three-member layout, qualification profile/checks, both payload digests, architecture/deployment audit and exact pre-sign source hash.

The following is a command template. Set every nonsecret input from those verified receipts and use fresh, distinct absolute paths; no RC34 run/artifact identifiers are available yet. From the clean control checkout:

```sh
: "${SOURCE_COMMIT:?Set the authorized final native merge SHA}"
: "${BUILD_RUN_ID:?Set the successful channel release-mode run ID}"
: "${ARTIFACT_ID:?Set its immutable artifact ID}"
: "${ARTIFACT_SHA256:?Set its verified artifact ZIP SHA-256}"
: "${RELEASE_CHANNEL:?Set macos-arm64 or macos-intel-test}"
: "${RELEASE_SCRATCH:?Set a new absolute private scratch path}"
: "${INSPECTION_OUTPUT:?Set a new absolute inspection path}"
: "${SIGNED_DELIVERY:?Set a new absolute signed-delivery path}"
: "${PUBLICATION_OUTPUT:?Set a new absolute publication path}"
mkdir -m 700 "$RELEASE_SCRATCH"

python3 -I -B release_control/local_release.py inspect \
  --source-commit "$SOURCE_COMMIT" \
  --build-run-id "$BUILD_RUN_ID" --artifact-id "$ARTIFACT_ID" \
  --artifact-sha256 "$ARTIFACT_SHA256" --channel "$RELEASE_CHANNEL" \
  --temp-root "$RELEASE_SCRATCH" --output "$INSPECTION_OUTPUT"

python3 -I -B release_control/local_release.py sign \
  --source-commit "$SOURCE_COMMIT" \
  --build-run-id "$BUILD_RUN_ID" --artifact-id "$ARTIFACT_ID" \
  --artifact-sha256 "$ARTIFACT_SHA256" --channel "$RELEASE_CHANNEL" \
  --temp-root "$RELEASE_SCRATCH" --output "$SIGNED_DELIVERY"

python3 -I -B release_control/local_release.py publish \
  --source-commit "$SOURCE_COMMIT" \
  --build-run-id "$BUILD_RUN_ID" --artifact-id "$ARTIFACT_ID" \
  --artifact-sha256 "$ARTIFACT_SHA256" --channel "$RELEASE_CHANNEL" \
  --temp-root "$RELEASE_SCRATCH" --delivery "$SIGNED_DELIVERY" \
  --output "$PUBLICATION_OUTPUT"
```

For the other channel, use its own receipt and fresh channel-specific directories. Apple Silicon signing of the Intel candidate requires the exact approved `arm64` → `x86_64` cross-sign policy **and** a successful run on `macos-15-intel`. The CLI records signer host architecture, candidate architecture and qualification runner separately. Static and all-architecture checks do not establish an Intel physical launch.

## Evidence and publication boundary

`inspect` writes `local-inspection.json` and performs no key use. `sign` produces the signed, notarized delivery plus `release-receipt.json`, `local-signing-manifest.json`, and its `.sig`; the receipt records actual accepted app and DMG notarization IDs, the pinned signing identity, execution UUID/time, approved control commit, operator, and real qualification IDs. The manifest signs a canonical inventory of every delivery file other than itself and its signature. Hosted signing run, attempt, and artifact IDs are never invented for local execution.

`publish` revalidates that manifest and signature, every delivery hash, source/feed/signature binding, the pinned Developer ID certificate, stapling, Gatekeeper assessment, and a read-only mounted app inventory. It saves `mounted-package-verification.json` outside the signed delivery. It makes `local-signed-delivery.zip` in private scratch with **all** sealed relative files, including feed release and rollback receipts, manifest and signature. The ZIP stays outside the signed manifest to avoid a self-hash cycle. Publication uploads the original seven assets, manifest, signature, and complete ZIP, reads draft and then public asset bytes back, and writes `publication-receipt.json` and `website-handoff.json` in the new output directory. The sealed signed delivery is not modified by publication.

The GitHub release does not promote website feeds or download pointers. The website owner uses the handoff with the signed manifest and complete archive, verifies current feed equals its expected previous hash, updates the exact channel pointer, and reads both public origins back. Physical device/account acceptance remains a separate result.

If signing or notarization fails, retain the bounded receipt/error and inspect before a fresh attempt. If publication has created a tag or draft, inspect that exact GitHub state before retrying; the CLI refuses to overwrite an existing tag or release. Keep the previous public feed and rollback bytes until website promotion and live readback succeed. See [PROTECTED-MAC-RELEASE.md](PROTECTED-MAC-RELEASE.md) for custody and owner approval and protection contract.
