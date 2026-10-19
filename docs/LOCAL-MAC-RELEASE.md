# Local Mac release path

This path signs an already qualified AMPLIFai Phone native build with the existing Developer ID and Sparkle keys in the owner's macOS Keychain. It requires the owner-approved release policy on current protected `main`. Either `JmooreOsirs` or `bac1876` may approve source/control changes, including their own, through a normal PR merge after required CI succeeds. Local Keychain execution remains restricted to `JmooreOsirs`. The hosted workflow remains a separate release path. A local Keychain release has same-user Mac custody; it does not claim the hosted runner's key isolation.

## Before any local release

1. Use a private checkout of current protected `main` with no modified, untracked, ignored, linked, or assume-unchanged control files. Run the CLI with `python3 -I -B`. The CLI checks current protected main, the authoritative merged PR and allowlisted `merged_by` actor, the latest successful checks on the exact PR head from GitHub Actions App `15368`, both protected refs and effective CODEOWNERS again at every phase. A newer failed or incomplete attempt cannot be replaced with an older success.
2. Use the existing `gh` login for `JmooreOsirs` on `github.com`. Do not supply `GH_TOKEN`, `GITHUB_TOKEN`, `GH_HOST`, hosted workflow variables, `PYTHONPATH`, or signing keys in the process environment. The CLI uses `gh api --hostname github.com` with the installed login and does not read or print the token.
3. Keep the existing Developer ID identity, `AMPLIFai-notary` profile, and `ai.satoris.amplifai.phone.candidate` Sparkle account available to the same macOS user. Normal Keychain prompts are part of key use. This path does not export a private key, create a credential, add a runner or role, or weaken branch protection.
4. Use a new absolute 0700 scratch directory and distinct new output directories outside the checkout. Put the signed delivery, scratch, and publication output in separate paths. Use the merged control implementation and exact source authorization. Keep that control SHA unchanged from signing through publication so the sealed manifest remains valid.

## Current candidate and command inputs

RC28 was signed locally, published and promoted on October 17 with control `067b2bc687e3af81d033923608679578814c535b`; see the [historical delivery receipt](RELEASE-DELIVERY-CONTRACT.md#historical-rc28-deliveries-and-publication). Those artifact IDs must not be reused as RC29 inputs.

RC29 native PR #12 merged normally by `JmooreOsirs` at `2026-10-19T19:04:36Z`, preserving head `45777eb7fa1ff965815cca6de6d042fb0895a679` in final native source `71dfabe3c3002e7a7787c649607607adafd29558`. The live merge actor and latest exact-head App15368 CI passed runtime validation. RC29 has no release-mode build, signed package or publication receipt yet. Its identities are ARM `2026.10.19-rc29` / `26101929` and Intel `2026.10.19-rc29-intel1` / `261019291`; current policy pins the final source and RC28 previous-feed floors.

| Channel | Version | Build |
| --- | --- | --- |
| `macos-arm64` | `2026.10.19-rc29` | `26101929` |
| `macos-intel-test` | `2026.10.19-rc29-intel1` | `261019291` |

After normal source/control merges, take the **final native merge SHA** from `approvedSources` and each channel's successful release-mode run, immutable artifact ID and complete artifact ZIP SHA-256 from its qualification receipt. The PR head above is a candidate identifier, not the final release authorization. The CLI validates the artifact's three-member layout, qualification profile/checks, both payload digests, architecture/deployment audit and exact pre-sign source hash.

The following is a command template. Set every nonsecret input from those verified receipts and use fresh, distinct absolute paths; no RC29 run/artifact identifiers are available yet. From the clean control checkout:

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
