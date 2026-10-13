# Intel Mac verification record

Recorded **2026-10-12**. This document separates completed local checks from the native Intel build and the remaining user tests. A compiled app is not proof of a completed iPhone import or account save.

## Source identity

- Published source baseline: `macos-2026.10.12-rc23-release`, archive SHA-256 `4106391606b1c373c8a78fe57823748534351505dee1353ed0bb69c365b79a6b`.
- Imported authored-source baseline commit: `81ecfb5`. See `INTEL-RC23-BASELINE.json` for the import inventory.
- Intel implementation submitted to CI: `ce59a26e8dc6044c6e1c97dc76c57c883c5538bd`.
- The local native checks below ran against the changed Swift files subsequently included in that implementation commit. They did not use the older companion `main` as their collector baseline.

## Completed local checks

Host: Apple silicon (`arm64`), macOS **26.6.2**, build **25G83**; Apple Swift **6.3.3**. All compilation targets retained a macOS **14.0** deployment floor. Compilation and Rosetta execution on this newer OS do not verify execution on macOS 14.

| Check | Result | What it establishes |
| --- | --- | --- |
| `Tests/check_flow.sh`, default ARM target | Passed | Existing synthetic collection-flow and byte-progress checks still pass. No phone collection occurred. |
| Full Swift app source, ARM target | Compiled successfully | The complete UI and updater changes compile for `arm64-apple-macos14.0`. The app was not launched. |
| ARM updater regression executable | Passed | Existing update deferral, install-on-quit and no-update classifications still pass; the new disabled-update policy prevents manual checks and installer-handler execution. |
| Full Swift app source, Intel target | Compiled successfully | The complete UI and updater changes compile for `x86_64-apple-macos14.0`. `file` confirmed an x86_64 Mach-O executable. The app was not launched. |
| Intel updater regression executable | Passed under **Rosetta** | The actual x86_64-compiled updater defaults to disabled and does not release deferred or quit-time installer handlers. This was not a physical Intel Mac run. |
| Invalid `AMPLIFAI_TARGET_ARCH` supplied to updater script | Rejected with exit status 2 | An unsupported target is rejected before building or starting an updater. |
| `git diff --check` for the native changes | Passed | No whitespace errors in the reviewed native patch. |

The update-denial tests use synthetic model state and callbacks. They neither fetch the production update feed nor install an update. The Intel policy rejects controller startup and manual checks before Sparkle is initialized. Intel also omits the Apple-silicon download/update buttons from the UI. ARM update behavior remains enabled.

### Dependency used for local compilation

SwiftPM resolved **Sparkle 2.10.0**, but its binary download stalled locally. The exact official ZIP was fetched separately and its SHA-256 matched the checksum in the pinned upstream `Package.swift`:

`17e28312b8e18ab7cdbbe09a6fb28cc55a5479ec6c371dbc07cdecd2a14fd959`

The direct Swift compilations and updater executables used its `Sparkle.xcframework/macos-arm64_x86_64` framework. This was a download workaround, not a dependency change. The complete local `check_updater.sh` invocation was stopped during SwiftPM download and is **not** recorded as a passing script run; its updater test sources were compiled and run directly against the checksum-verified framework. The checked-in script continues to use normal SwiftPM with a matching release build and target architecture.

Local output dates, taken from the built files, were 2026-10-12 **23:15–23:17 UTC**:

| Local verification executable | SHA-256 |
| --- | --- |
| ARM full Swift app | `1ef7d9495c75226acd75a52852a2e3da734eeefcc7c92aa75a48b2e46e51357b` |
| Intel full Swift app | `711efffd81c299cf8a95ffc95cd356d1b4bb9ca559f5c9fe88225eaadf0b8f61` |
| ARM updater checks | `3bd783b7808d4ab106cd893ee2deb00f52a559a86c91a7e75f764cd6a5885998` |
| Intel updater checks | `db76a210af12ae4dded389bd7976665a804f7405b97eb8862f2397781f3aba7a` |

These are temporary verification executables, not the packaged Intel candidate or a customer installer.

## Native Intel CI

Initial run [38321799136](https://github.com/Osirs-io/amplifai-phone-companion/actions/runs/38321799136), commit `ce59a26e8dc6044c6e1c97dc76c57c883c5538bd`, ran natively on Intel macOS **15.7.9**, Apple Swift **6.1.2** and pinned CPython **3.12.15**. It successfully rebuilt and validated cryptography 50.0.1 with static OpenSSL 3.5.9, collected the matching Python source/notices, passed 12 build guards, 21 architecture fixture tests and **274 collector regressions**, and compiled/packaged/ad-hoc-signed the full app.

That run correctly stopped before publication because the architecture checker rejected the system WebKit framework after resolving Apple's Cryptex relocation. This was a checker defect, not an absent Intel library. Commit `5756202162db8415e37b45f3c38bcca22930bcf6` preserves normalized Apple system install names while retaining external-library and app-symlink rejection. The expanded 24 architecture tests pass locally, including the actual WebKit linkage. The exact Sparkle 2.10.0 framework also passes the complete audit/signing sequence in a local fixture.

Replacement run [38322269335](https://github.com/Osirs-io/amplifai-phone-companion/actions/runs/38322269335) tested `5756202162db8415e37b45f3c38bcca22930bcf6`. **Result: SUCCESS**, completed 2026-10-12 at approximately 23:42 UTC on native Intel macOS 15.7.9 / Swift 6.1.2. All workflow steps completed, including publication to this separate fork.

| Native Intel check | Observed result |
| --- | --- |
| Exact dependency source build | cryptography 50.0.1 / static OpenSSL 3.5.9 built; AES round trip, Intel architecture and no external OpenSSL linkage verified. All 102 Python versions preserved. |
| Source/build integrity guards | 17 passed. |
| Architecture checker regressions | 24 passed. |
| Collector regressions | 274 passed. |
| Complete Swift app and frozen helper | Built and ad-hoc signed; deep/strict signature verification passed. |
| Actual bundled native components | All 14 Mach-O files contain x86_64, satisfy declared minimum-OS checks, and pass dependency-closure checks. |
| Packaged helper launch/import checks | `inspect` and `runtime-check` passed with isolated HOME. |
| Native Swift tests | Flow/byte-progress and updater refusal checks passed on the Intel runner. |
| Actual frozen validation worker | Fictional contacts, calls and messages accepted; altered copy checksum rejected; temporary session cleanup verified. |
| Exact helper's compiled Python code | 73 metadata/capacity/workspace tests passed, zero failures/errors and no readable engine fallback. The code was extracted from the selected helper and executed with its matching dependency environment; this is separate from frozen-loader or physical-device proof. |

[Download the exact build, source and machine-readable receipts](https://github.com/Osirs-io/amplifai-phone-companion/releases/tag/intel-rc23-build-5756202162db).

- App ZIP SHA-256: `742ac4736f09bd8b5bec3e21bcf0d420108af98ee5501e91216d4751d63c10d3`.
- Source archive SHA-256: `ea7abb0e41477b314375ba4958226517c95dc596fb9e229f263bd0f058dc7b5d`.
- Native app executable SHA-256: `64f76b13ae6ddb37db8c5b6b13e1ab73ea4aa581d9c3dab7ed2f020b252c4619`.
- Frozen helper SHA-256: `5f9c318738e02b3c1725ce18b5cfd26964dddda19f3735ef2376b4fccaf7d869`.
- Version: `2026.10.12-rc23-intel1`, build `261012231`; candidate bundle ID `ai.satoris.amplifai.phone.intel-candidate`.
- Account origin retained from RC23: `https://amplifai-database-engine.vercel.app`. No account was opened by CI.

After download, the app ZIP and all downloaded JSON receipts matched the release checksums. ZIP integrity, both embedded executable hashes, the isolated bundle identity and absence of the ARM updater URL were checked locally without installing or launching the app. The 160 MB source archive's digest is recorded by GitHub and the CI-generated checksum file; it was not downloaded again for a local recheck.

## Still required before ordinary-user release

1. Developer ID signing, notarization/stapling, the final installer and normal downloaded Gatekeeper acceptance.
2. Execution on a physical Intel Mac, including macOS 14 before advertising that version as verified.
3. Owner-approved real iPhone connection, Trust/encryption/password handling and known-call review; saved-backup reprocessing and fresh USB collection must have separate results.
4. A compatible Intel setup/pairing route on the canonical website, then selected-source Save acknowledgements, Finalize, report and sign-out/sign-in persistence. This companion patch does not enable that website route.
5. Interrupted-save/report recovery with the retained reviewed data, without claiming that a new phone collection is necessary in every case.

No local check above changed the production website, an account database, the published ARM installer, or the user's phone. See `INTEL-MAC-HANDOFF.md` for the release steps and boundaries.
