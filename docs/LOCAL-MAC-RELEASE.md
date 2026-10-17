# Local Mac release path

This path signs an already qualified AMPLIFai Phone native build with the existing Developer ID and Sparkle keys in the owner's macOS Keychain. It requires an independently reviewed, merged local-release policy on current protected `main`. The hosted workflow remains a separate release path. A local Keychain release has same-user Mac custody; it does not claim the hosted runner's key isolation.

## Before any local release

1. Use a private checkout of current protected `main` with no modified, untracked, ignored, linked, or assume-unchanged control files. Run the CLI with `python3 -I -B`. The CLI checks current protected main, the exact merged PR head and independent CODEOWNER approval, required checks, both protected refs and effective CODEOWNERS again at every phase.
2. Use the existing `gh` login for `JmooreOsirs` on `github.com`. Do not supply `GH_TOKEN`, `GITHUB_TOKEN`, `GH_HOST`, hosted workflow variables, `PYTHONPATH`, or signing keys in the process environment. The CLI uses `gh api --hostname github.com` with the installed login and does not read or print the token.
3. Keep the existing Developer ID identity, `AMPLIFai-notary` profile, and `ai.satoris.amplifai.phone.candidate` Sparkle account available to the same macOS user. Normal Keychain prompts are part of key use. This path does not export a private key, create a credential, add a runner or role, or weaken branch protection.
4. Use a new absolute 0700 scratch directory and distinct new output directories outside the checkout. Put the signed delivery, scratch, and publication output in separate paths. Do not execute a signing or publication command until this implementation and policy are reviewed and merged.

## Fixed qualified inputs

| Channel | Version / build | CI run | Artifact ID | GitHub artifact ZIP SHA-256 | Qualified runner |
| --- | --- | ---: | ---: | --- | --- |
| `macos-arm64` | `2026.10.16-rc28` / `26101628` | `39046812410` | `12106277218` | `97dcc3423ff27a50f4e558f7c35ad065cad8a5ea3a8ca1670af0b2e1ad74d05b` | `macos-15` |
| `macos-intel-test` | `2026.10.16-rc28-intel1` / `261016281` | `39046814619` | `12106102831` | `c0a5bc8485847437a56f6f5e9fcfb31e85ce9e66f6c2b0228b91bc913c66c305` | `macos-15-intel` |

Both runs bind native source `8f7ec4d3de4235ab5d36d5175c29121e3a26548a` on protected `release/native`. The CLI checks immutable run and artifact metadata, the complete artifact ZIP digest and three-member layout, qualification booleans, both payload digests, native architecture/deployment audit, and the exact pre-sign source hash. It treats these as build evidence, not signing or device acceptance.

## Commands after reviewed merge

Use one channel's exact run, artifact and digest from the table in each command. From the clean control checkout:

```sh
mkdir -m 700 '/absolute/private/path/local-release-scratch'

python3 -I -B release_control/local_release.py inspect \
  --source-commit 8f7ec4d3de4235ab5d36d5175c29121e3a26548a \
  --build-run-id 39046812410 --artifact-id 12106277218 \
  --artifact-sha256 97dcc3423ff27a50f4e558f7c35ad065cad8a5ea3a8ca1670af0b2e1ad74d05b \
  --channel macos-arm64 \
  --temp-root '/absolute/private/path/local-release-scratch' \
  --output '/absolute/private/path/arm-inspection'

python3 -I -B release_control/local_release.py sign \
  --source-commit 8f7ec4d3de4235ab5d36d5175c29121e3a26548a \
  --build-run-id 39046812410 --artifact-id 12106277218 \
  --artifact-sha256 97dcc3423ff27a50f4e558f7c35ad065cad8a5ea3a8ca1670af0b2e1ad74d05b \
  --channel macos-arm64 \
  --temp-root '/absolute/private/path/local-release-scratch' \
  --output '/absolute/private/path/arm-signed-delivery'

python3 -I -B release_control/local_release.py publish \
  --source-commit 8f7ec4d3de4235ab5d36d5175c29121e3a26548a \
  --build-run-id 39046812410 --artifact-id 12106277218 \
  --artifact-sha256 97dcc3423ff27a50f4e558f7c35ad065cad8a5ea3a8ca1670af0b2e1ad74d05b \
  --channel macos-arm64 \
  --temp-root '/absolute/private/path/local-release-scratch' \
  --delivery '/absolute/private/path/arm-signed-delivery' \
  --output '/absolute/private/path/arm-publication'
```

For Intel, use the Intel table row and fresh Intel-specific directories. Apple Silicon signing of the Intel candidate is permitted only by the exact reviewed `arm64` → `x86_64` cross-sign policy **and** a successful run on `macos-15-intel`. The CLI records signer host architecture, candidate architecture, and qualification runner separately. Static and all-architecture checks do not establish an Intel physical launch.

## Evidence and publication boundary

`inspect` writes `local-inspection.json` and performs no key use. `sign` produces the signed, notarized delivery plus `release-receipt.json`, `local-signing-manifest.json`, and its `.sig`; the receipt records actual accepted app and DMG notarization IDs, the pinned signing identity, execution UUID/time, reviewed control commit, operator, and real qualification IDs. The manifest signs a canonical inventory of every delivery file other than itself and its signature. Hosted signing run, attempt, and artifact IDs are never invented for local execution.

`publish` revalidates that manifest and signature, every delivery hash, source/feed/signature binding, the pinned Developer ID certificate, stapling, Gatekeeper assessment, and a read-only mounted app inventory. It saves `mounted-package-verification.json` outside the signed delivery. It makes `local-signed-delivery.zip` in private scratch with **all** sealed relative files, including feed release and rollback receipts, manifest and signature. The ZIP stays outside the signed manifest to avoid a self-hash cycle. Publication uploads the original seven assets, manifest, signature, and complete ZIP, reads draft and then public asset bytes back, and writes `publication-receipt.json` and `website-handoff.json` in the new output directory. The sealed signed delivery is not modified by publication.

The GitHub release does not promote website feeds or download pointers. The website owner uses the handoff with the signed manifest and complete archive, verifies current feed equals its expected previous hash, updates the exact channel pointer, and reads both public origins back. Physical device/account acceptance remains a separate result.

If signing or notarization fails, retain the bounded receipt/error and inspect before a fresh attempt. If publication has created a tag or draft, inspect that exact GitHub state before retrying; the CLI refuses to overwrite an existing tag or release. Keep the previous public feed and rollback bytes until website promotion and live readback succeed. See [PROTECTED-MAC-RELEASE.md](PROTECTED-MAC-RELEASE.md) for custody and protected review procedure.
