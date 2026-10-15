AMPLIFai Apple Silicon RC27 account-origin fix — release handoff for Jeff

BRANCH: codex/brian-rc27-account-origin (JmooreOsirs and Osirs-io)
BASE: macos-2026.10.15-rc26-release, 257748fcb2d6aef758766314f4e3817c8d30c372
RELEASE SOURCE COMMITS:
  0a50e00  fix(bridge): accept the canonical account origin and bind each transfer
  b25a0bc  release(macos-arm64): prepare 2026.10.15-rc27 build 26101527
CI-ONLY COMMITS (not app bytes; drop or keep as you prefer):
  9130383  points macos-intel.yml's job guard at this branch and drops its success upload
  167a65d, c643105  add the Apple Silicon build-only check (macos-arm64-check.yml)
To build from the RC26 tag without the CI commits:
  git switch -c release/macos-rc27 macos-2026.10.15-rc26-release
  git cherry-pick 0a50e00 b25a0bc

WHAT BROKE
- Website ca328b7 (12:40 MDT Production, carried into dpl_9yeqobAZiQ74UBw5UvNpf2m91aEz) redirects
  GET https://amplifai-database-engine.vercel.app/phone/account(/report) with 307 to
  https://amplifai.satoris.ai/phone/account# and drops ?continue=reviewed.
- RC26 bridge.py accepts exactly one browser Origin on 127.0.0.1:48751: the Vercel alias.
  The canonical page's discover/connect/pair/transfer calls are refused, which the site shows as
  an unreachable companion. The status service on 48752 accepts both origins, so the site can see
  the app running while transfer is blocked.
- Not the cause: download links, update feeds and CSP. Both hosts serve identical feeds, and the
  CSP allows 127.0.0.1:48751/48752 on /phone and /phone/account for either host.
- Same bridge, same failure: Intel rc26-intel1 and Windows rc19 package the same helper.

THE FIX
- bridge.py allows exactly https://amplifai.satoris.ai and https://amplifai-database-engine.vercel.app
  (the same pair as companion_status.py). It still requires a loopback peer, an exact Host and a
  single Origin header, and echoes only the matched origin. The first successful /v1/pair or
  /v3/connect binds the transfer; the other origin is then refused on every route.
- ContentView opens https://amplifai.satoris.ai/phone/account?continue=reviewed directly, plus the
  account and guide links. CONNECT_API_HOST, SUFeedURL, the bundle ID and the Sparkle key are unchanged.
- Version 2026.10.15-rc27, build 26101527 (> 26101526), so installed RC26 apps accept the update.

VALIDATION COMPLETED (no Developer ID signing, notarization or publication)
- Intel Mac run 38815731550 on 9130383: 348 collector tests OK (4 skipped); the app built and passed
  the native flow, updater and frozen-worker checks.
- Apple Silicon run 38819929271 on c643105: 349 collector tests OK (4 skipped), including 25 bridge
  tests; the native arm64 Swift checks passed; the ad-hoc portable app was arm64 with status
  receipt "qualified".
- Apple Silicon run on b25a0bc (rc27 version bump): see RESULT below.
- Local: bridge/status tests pass. desktop_update_tests needs an Ed25519-capable OpenSSL
  (/opt/homebrew/bin/openssl); macOS LibreSSL fails 12 tests identically before and after.

RELEASE STEPS (existing process, on the release Mac)
1. bash source/script/build_portable_candidate.sh --isolated
   The status receipt must report 2026.10.15-rc27 / 26101527.
2. Place the five Legal files, then: source/script/package_macos_dmg.sh --check-legal <app>
3. Developer ID sign (team WSBR54954W, hardened runtime, secure timestamp) the app, the helper
   and every nested .dylib/.so. Notarize and staple, then run source/script/companion_release_preflight.sh.
4. source/script/package_macos_dmg.sh <notarized app> AMPLIFai-Phone-Apple-Silicon-2026.10.15-rc27.dmg
   Then sign, notarize and staple the DMG.
5. Build the corresponding-source ZIP (see docs/DESKTOP-UPDATE-SOURCE-OFFER.md).
6. Publish GitHub release tag macos-2026.10.15-rc27-release with these assets:
   AMPLIFai-Phone-Apple-Silicon-2026.10.15-rc27.dmg, ...-rc27-Source.zip,
   arm-qualification.json, SHA256SUMS-Apple-Silicon-rc27.txt
7. Generate the feed with source/script/desktop_update_release.py:
   - channel macos-arm64, version/build/bundleId exactly as in docs/DESKTOP-UPDATE-SOURCE-PINS.json
   - sourceBaseCommit f8c38a5217ad7c356abefd95264762a9c9afe478
   - baselineSourceArchiveSha256 171c383e06b56f681a627a571fd9ed228ebc4c34a8b59c311112ec4f3b8265c3
   - --previous-feed: the live rc26 appcast (1561 bytes, sha256 afd0ede0…2b13)
   - Keychain account ai.satoris.amplifai.phone.candidate
8. Website (website owner). Brian has a local branch codex/brian-rc27-companion-build (635f4da)
   that already accepts build 26101527 in companion diagnostics and telemetry. Then:
   - src/lib/release/phone-companion.ts MAC_PHONE_COMPANION: version, build, download, release
   - replace public/phone/macos/appcast.xml with the exact generated feed bytes
   - update tests/macos-arm-update-feed.test.ts (feed length and sha256, DMG length),
     tests/native-download-metadata.test.ts, tests/phone-connection-setup.test.ts and the
     companion-update-status-ui.test.ts rows
   - run the full suite, stage Production with --skip-domain, verify, promote, then read back
     the public feed bytes on both hosts

CREDENTIALS AND ACCESS NEEDED (Brian has none of the signing items)
- Developer ID Application certificate and private key for team WSBR54954W
  (Brian's Mac has 0 code-signing identities).
- notarytool credentials for team WSBR54954W (no profile name is recorded in this repo).
- The Sparkle EdDSA private key in Keychain account ai.satoris.amplifai.phone.candidate
  (public key vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs=), the pinned Sparkle 2.10.0 tools and an
  Ed25519-capable OpenSSL.
- The out-of-repo tooling for Legal placement, nested Developer ID signing and the source ZIP.
- Already available: bac1876 has write on JmooreOsirs (branches pushed; no release created), and
  the session's Vercel connection can read project amplifai-database-engine (team osirs). Its
  deploy rights were not checked, and nothing was deployed.

ACCEPTANCE STILL REQUIRED (the fix is not complete until this passes)
On a Mac without a developer build:
- download the signed RC27 DMG from https://amplifai.satoris.ai/phone and install it
- with a physical iPhone: transfer → local review → Connect from https://amplifai.satoris.ai/phone/account
  → per-source Save → ACK → report → sign out and back in, with the report persisting
- confirm an installed RC26 updates to RC27 from the feed
Intel and Windows need their own rebuilds (Windows links: codex/brian-rc27-windows-origin, ac0164b).

RESULT
Apple Silicon run 38822252274 on b25a0bc: success. 349 collector tests OK (4 skipped) and 24
script tests OK; native arm64 checks passed; the ad-hoc portable app is arm64 with status receipt
version 2026.10.15-rc27, build 26101527, status "qualified". Nothing was uploaded or published.
