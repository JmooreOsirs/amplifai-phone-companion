# Native delivery and website promotion contracts

PRs #4, #5 and #6 are independently reviewed and normally merged. Active control `main` is `9fb2fa913292a5737310c2a2fe2381724211709f`; protected native source is frozen at `8f7ec4d3de4235ab5d36d5175c29121e3a26548a`. Both exact-source RC28 release qualifications passed, and their immutable artifact digests and app/source payload hashes were verified. Signing/notarization, publication and final signed-byte website promotion remain pending. The existing website owner has supplied adapter/Preview work; that evidence does not qualify final signed public bytes.

The hosted credential-bearing path is installed but has not completed a live signed release. Jeff has explicitly authorized a local Keychain mode, **under implementation and pending protected independent review**; see [LOCAL-MAC-RELEASE.md](LOCAL-MAC-RELEASE.md). It uses existing credentials on Jeff's Mac and must record that custody accurately. Disposable hosted-runner containment, GitHub environment secret isolation and separate hosted publication-job isolation are not local-mode assurances. Control-only implementation/documentation changes do not change or rebuild the frozen qualified native delivery.

## Native build owner hook

The reviewed native source commit must include `release_control/producer.py`, its `release.py` dependency and the existing standalone architecture verifier. Execute producer code **from that exact native source commit**. Read current default-branch policy as data; never fetch and execute arbitrary newer helper code. The existing source workflow's actual path must equal the source authorization's `buildWorkflow`, run at protected `release/native`, and have `GITHUB_SHA` equal the full authorized source SHA.

1. Run the existing architecture-specific build with its pinned runtime/dependency/source recipes. Keep the ARM and Intel mechanisms separate; do not replace the Windows workflow or remove Intel artifact retention.
2. Stage the app under `AMPLIFai Phone.app` or `AMPLIFai Phone Intel Test.app`, outside the checkout. Existing ARM raw output is `AmplifaiPhonePortable.app`; copy/rename it in runner temp before delivery. Preserve the bundle metadata and relative framework symlinks.
3. The reviewed source-closure adapter must reconcile the actual helper/SBOM/native dependencies with the matching source archives/notices, stage all current authored source, create the complete source ZIP/map below, and install the five exactly matching Legal files into the staged app. Existing retained dependency evidence can be reused only when its pins and actual dependency declarations match. No synthetic “complete” result, historical app inventory or bare git archive qualifies.
4. Run this actual producer CLI in the same secret-free native build job. The build job supplies `QUALIFIED_APP`, `COMPLETE_SOURCE_ZIP` and `RELEASE_CHANNEL` from its fixed reviewed recipe, not arbitrary dispatch commands. The policy file is the current `main` JSON fetched read-only into runner temp.

```sh
python3 release_control/producer.py \
  --checkout "$GITHUB_WORKSPACE" \
  --app "$QUALIFIED_APP" \
  --source-archive "$COMPLETE_SOURCE_ZIP" \
  --policy "$RUNNER_TEMP/release-policy.json" \
  --source-commit "$GITHUB_SHA" \
  --channel "$RELEASE_CHANNEL" \
  --output "$RUNNER_TEMP/qualified-delivery"
```

Use the existing hash-locked Python environment for this CLI. It executes the collector/bridge tests, the native Swift checks required by the source's qualification profile, actual frozen synthetic worker and frozen status checks; after **all executable qualification** it audits the final app again and compares every source-mapped native hash. It validates the clean Git checkout, every authored/source ZIP member and matching Legal bytes before emitting anything. No product credentials are allowed in this job.

5. Upload only `qualified-delivery/` with the existing pinned upload-artifact action (`ea165f8d65b6e75b540449e92b4886f43607fa02`), `if-no-files-found: error`, no `always()` success artifact on failure. Retain the artifact ID and server digest; the release dispatcher takes these exact values. `qualification.json`, `app.zip` and `source.zip` are the only three root files. No human approves those generated hashes again.

Merged PR #5 installed the source-closure/map emitter, five-file Legal staging and producer/upload hook while preserving the product fixes and later Windows/Intel work. Both RC28 channels were built and qualified from the final clean, reviewed merge commit. Historical and Preview deliveries retain their original source identities. Any changed native payload requires a newly reviewed source and qualification; control-only changes preserve the existing delivery and its complete source offer.

## Qualified RC28 deliveries

Both deliveries bind source `8f7ec4d3de4235ab5d36d5175c29121e3a26548a`, release mode, their trusted architecture-specific workflow, all five measured passing checks and `privateDataUsed=false`. The retained readback receipts verify the server artifact digest and exact `qualification.json` app/source payload hashes.

| Channel / identity | Successful run / immutable artifact | Server artifact SHA256 |
| --- | --- | --- |
| `macos-arm64` — `2026.10.16-rc28` / `26101628` | Run `39046812410`; artifact `12106277218` | `97dcc3423ff27a50f4e558f7c35ad065cad8a5ea3a8ca1670af0b2e1ad74d05b` |
| `macos-intel-test` — `2026.10.16-rc28-intel1` / `261016281` | Run `39046814619`; artifact `12106102831` | `c0a5bc8485847437a56f6f5e9fcfb31e85ce9e66f6c2b0228b91bc913c66c305` |

Evidence: `rc28-arm-release-qualification/delivery-readback-receipt.json` and `rc28-intel-release-qualification/delivery-readback-receipt.json` in the October 16 protected-native-release evidence packet, verified October 17. Merged PR #6 binds these source/channel/version/build/workflow records on `main`, with `activationApproved=true`, `publicationEnabled=true` and two exact authorized sources. The latest retained public-feed readback remains RC26; refresh it at promotion. Qualified deliveries are not yet Developer ID signed, notarized or published packages.

## Complete pre-sign source ZIP

ZIP members are unique regular relative files, with no directories, symlinks, traversal or unlisted payload. The root `release-source-map.json` is bounded to 4 MiB and contains:

| Field | Required content |
| --- | --- |
| `schema` | Integer `1` |
| `sourceCommit` | Exact reviewed native SHA |
| `files` | Every ZIP payload member except the source map itself: `{path, bytes, sha256}` |
| `authored` | Map every `git ls-files` path from that clean commit to its exact archive member; bytes must match the checkout |
| `native` | Map every actual Mach-O relative app path to `{preSignSha256, sourceInputs, noticeInputs}`; inputs name nonempty lists of checksum-inventoried source/notice members |
| `legal` | Map `LICENSE`, `COPYRIGHT`, `SOURCE-README.md`, `THIRD-PARTY-NOTICES.txt`, `source-package-file-manifest.json` to exact source members that match the staged app's Legal bytes |
| `appendPaths` | Exactly `release-evidence/final-signed-app-inventory.json` and `release-evidence/source-finalization.json` |

Native source/notice attribution is a reviewed build-recipe responsibility; the validator measures completeness and hash binding, not a native rebuild or legal determination. The adapter must preserve the existing SBOM/Cargo/interpreter recipe reconciliation and checksummed third-party source closure. Neither the current old inventories nor a caller-provided boolean replace it.

After qualification, `qualification.json` binds source/channel/version/build, actual `GITHUB_RUN_ID`, strictly boolean passing measured checks, `privateDataUsed=false`, and exact app/source ZIP names/sizes/hashes. It is authenticated by the immutable GitHub artifact digest and exact successful reviewed workflow provenance.

### Phone run and recovery qualification profile

Every newly approved source must include `"qualificationProfile": "phone-run-recovery-v1"` in its reviewed `approvedSources` row. This field is qualification policy, independent of consent disclosure versions. Adding the profile does not authorize a source or release: exact source approval, `releaseAuthorized`, protected reviews and all existing artifact/custody checks still apply.

In addition to the existing checks, this profile executes:

- `source/collector/macos/Tests/check_model.sh` → `collectorModel`.
- `source/collector/macos/Tests/check_window_lifecycle.sh` → `windowLifecycle` (real hidden AppKit windows on the macOS runner, synthetic model; no customer app activation).
- `source/collector/tests/test_bridge_run_timing.py` → `bridgeRunTiming`.
- `source/collector/tests/test_bridge_recovery.py` → `bridgeRecovery`.

All four files must exist as regular files inside the reviewed checkout; missing files, symlinks, failed commands and timeouts fail qualification. These results are emitted only after successful checks, as **schema 2**, the exact `qualificationProfile`, and the four additional strictly boolean `true` checks. The signer rejects schema-1 or incomplete results for a new source. The existing two RC28 tuples at `8f7ec4d3de4235ab5d36d5175c29121e3a26548a` alone retain their original schema-1 receipts and check path, allowing their immutable source to remain reproducible. A changed SHA, channel, version or build cannot use that exception.

The producer and receipt validator changes must be present in the final reviewed native source as well as protected main control. The native preview entry point must set this profile on its preview candidate; preview evidence still grants no release authority. This control change does not add or authorize a new source SHA.

For PRs targeting `release/native` and pushes to that branch, the existing required `release-control-checks` job depends on `native-phone-run-checks` passing on `macos-15`. That job uses the existing pinned Python/uv runtime and hash-locked dependencies to execute these four checks through `release_control/check_phone_run.py`. A failure or cancellation cannot become a green required check. Main-only control PRs keep the existing Python control suite without executing native code. No credentials, environment grants, branch protections or reviewer requirements are changed.

## Signing, finalization and feed output

Either reviewed signing mode must consume only the exact immutable qualified artifact. Immediately before codesign, the signer independently audits actual input bytes again and compares the source map's pre-sign native hashes. Signing steps execute no candidate app/source code. The hosted workflow uses its protected environment and temporary keychain; the local implementation uses the existing owner Keychain and notarization profile under [LOCAL-MAC-RELEASE.md](LOCAL-MAC-RELEASE.md), pending its protected review. Native Intel qualification remains the recorded Intel runner evidence; local signing on the ARM Mac does not constitute an Intel rebuild or execution check.

After app/DMG signing, notarization and staples, `finalize.py` preserves every source payload member byte-for-byte and appends only the two predeclared evidence paths. The finalization receipt pairs source-mapped pre-sign hashes with the full actual post-sign inventory; every final ZIP member is read back. It does not mutate in-app Legal after signing.

`feeds.py` calls the unchanged qualified `desktop_update_release.py.prepare`, preserving existing bundle IDs, public key, legacy ARM default channel, separate Intel Test channel and embedded signed-feed comments. It pins/retains the exact observed previous feed before making the new signed feed. The local preparation receipt still correctly says `locally-prepared-not-published`.

## Hosted isolated GitHub publication

In the installed hosted workflow, only the separate publication job has `contents:write`; it receives no Apple/Sparkle keys. `publicationEnabled=false` remains the fail-closed default; merged PR #6 explicitly enables publication for the authorized RC28 sources. Mode `publish` signs then promotes within the same workflow/run; it is not a second human approval. The separately authorized local implementation has its own custody/provenance contract and does not claim this hosted job isolation.

- Revalidate source/control custody and the publication environment, then require the exact signer artifact from the successful signer job in this same run **and attempt**.
- Verify all signed package/source/feed receipts, actual Ed25519 signatures, complete finalized source and descriptor/readback hashes.
- Compare the candidate with the live same-channel release inventory, including drafts, before starting and before publication. Equal/newer releases reject stale or conflicting runs; policy's initial floor is not the live authority.
- Atomically create a **new** lightweight tag at the authorized source SHA; an existing/raced tag is rejected. Use `gh release create --verify-tag`, not automatic tag creation with `--target`. Verify resolved tag→source again immediately before public edit.
- Upload into a draft without clobbering; read back every uploaded byte before making it public. Read all public asset bytes back afterward. A failed draft/tag is retained for inspection, never deleted/replaced automatically.
- Same-channel workflow concurrency serializes this reviewed publisher. Live order/feed/tag checks catch observed external changes, but GitHub has no atomic cross-release compare-and-swap here. Repository administrators/other publishing tools remain outside that mutex; this residual race is explicit in the receipt. These publisher checks do not establish platform-enforced immutable tags; tag-update/deletion restrictions require their own configuration evidence before that assurance can be claimed.

## Website owner packet

Successful native publication produces `website-handoff.json`; it does not establish website deployment. Common fields bind exact source/control commits, channel/CPU/bundle/version/build, public native release URL, package/source public URLs and exact byte records, feed public URL and canonical website path, and the expected previous feed hash/size with its retained rollback location. Hosted provenance uses the signed Actions artifact ID/digest/run/attempt. The local implementation instead uses `localSigningEvidence` with manifest, signature and delivery assets; `local-signed-delivery.zip` contains the unchanged full signed directory, including its feed receipt and previous-feed rollback, plus the signed manifest and `.sig`. It has no hosted `signedArtifact`. This local transport is under implementation and pending protected review; no successful signed package or publication is implied. The current native emitter still writes `websitePublisherImplemented=false` and `websitePromoted=false`; preserve that raw packet and record the independently supplied website adapter evidence separately. The implementation flag is a historical limitation of this emitter, not a production-promotion receipt. Only the website owner's actual verified signed-byte deployment can establish promotion; no documentation edit changes those emitted flags.

The earlier read-only website audit recorded checkout `amplify-database-engine-account-release-oct14`, branch `codex/course-shell-integration-oct14`, source `7746f48`. That is historical audit provenance, not a claim about the current website head. Reconcile the existing website owner's supplied adapter/Preview receipt and current canonical checkout before final handoff; do not open a duplicate execution lane.

| Surface | Retained audit contract; refresh before final promotion |
| --- | --- |
| ARM feed | `public/phone/macos/appcast.xml`; rc26 / `26101526`; 1,561 bytes; SHA256 `afd0ede099c9219853c4626f1466cff4cde6dfd95e7c42abd9a1164d89b72b13` |
| Intel feed | `public/phone/macos/intel-appcast.xml`; rc26-intel1 / `261015261`; 1,667 bytes; SHA256 `6207d5831dc6f0f638445458379393ac3a9fa7539d7d892e42b54846ec76031c` |
| Feed signatures | Embedded trailing Sparkle XML comments; **no Mac feed `.sig` sidecar**. The native package's `.dmg.sig` is a different artifact |
| Acquisition pointers | `src/lib/release/phone-companion.ts`; update consistently with the same new package/source/version |
| Exact feed tests | `tests/macos-arm-update-feed.test.ts`, `tests/macos-intel-update-feed.test.ts`: exact bytes/hash/key/channel/source/enclosure |
| Download/status fixtures | `tests/native-download-metadata.test.ts`, `tests/companion-update-status-ui.test.ts`: version/build/URLs and old/current/newer expectations; preserve Android/Windows |
| Diagnostic/telemetry admission | `src/lib/telemetry/events.ts` and `src/app/api/v1/companion-diagnostics/route.ts`: separate exact build allowlists must agree |
| Admission negative fixtures | `tests/telemetry.test.ts`, `tests/companion-diagnostics-route.test.ts`: the earlier audit rejected `26101527`; reconcile current RC28 admission/negative fixtures with both confirmed architecture-specific builds and retain private-field rejection |
| Routing/cache | Existing anonymous GET/HEAD handling in `proxy.ts`; no-store headers in `next.config.ts` |
| Observed public origins | `https://amplifai.satoris.ai` and `https://amplifai-database-engine.vercel.app`; both exact files returned 200/no-store at the audit |

The existing website owner has supplied a receipt-consuming adapter and Preview evidence. Final acceptance must verify that the supplied implementation updates feed, acquisition, release-pinned tests and diagnostic allowlists together for the **actual signed public RC28 bytes**. It must independently verify the signed feed/key/channel/CPU/newer build, official immutable assets, current highest published channel build and expected previous website feed. Deployment promotion needs a real expected-state/CAS or serialized equivalent to reject stale/concurrent packets, then both-origin feed/download readback and exact rollback custody. Native CI should receive only scoped dispatch/artifact authority for that interface, never general website deployment credentials. No new feed host or feed-only copying shortcut is part of this work.


## Custody and enforcement handoff

Follow [PROTECTED-MAC-RELEASE.md](PROTECTED-MAC-RELEASE.md#runtime-custody-evidence-and-limits) for recorded runtime denials, accepted protected merges and their limits. Its hosted provisioning sequence remains available for that route: the audit credential belongs in both exact named environments, Apple/Sparkle material only in signing. The existing Sparkle key upload succeeded with public continuity and cleanup evidence; complete hosted custody/signing remains unproven. The authorized local route follows [LOCAL-MAC-RELEASE.md](LOCAL-MAC-RELEASE.md) and remains pending protected implementation review. Record metadata-only custody receipts, never secret values or export contents.

Release readiness requires separate receipts for the exact source/build delivery, signing/notarization in the selected reviewed mode, public package/source/feed bytes, website production promotion, installed app identity and Jeff's physical iPhone transfer plus explicit source review/Save. None implies the next. Existing deployment authority carries through these technical checks without another routine deployment permission request.
