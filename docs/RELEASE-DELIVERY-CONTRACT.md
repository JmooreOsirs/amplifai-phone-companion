# Native delivery and website promotion contracts

Jeff authorizes either `JmooreOsirs` or `bac1876` to approve source/control/release changes, including their own, through a normal PR merge after required CI. The owner-approval policy retains strict `release-control-checks` pinned to GitHub Actions App `15368`, administrator enforcement, no force push/deletion and empty bypass allowances. Runtime custody requires the authoritative allowlisted merge action and latest successful exact-head checks; CODEOWNERS remains both actors for routing. See [the protection contract](PROTECTED-MAC-RELEASE.md#enforced-custody).

RC28 was locally signed, published and promoted on October 17 under control `067b2bc687e3af81d033923608679578814c535b` and native source `8f7ec4d3de4235ab5d36d5175c29121e3a26548a`. Its historical receipts are retained below. Physical/account acceptance was not established by that deployment.

RC29 native PR #12 merged normally by `JmooreOsirs` at `2026-10-19T19:04:36Z`, preserving head `45777eb7fa1ff965815cca6de6d042fb0895a679` in final native source `71dfabe3c3002e7a7787c649607607adafd29558`. The live merge actor and latest exact-head App15368 CI passed runtime validation. RC29 has no release-mode build, signed package or publication receipt yet. Its identities are ARM `2026.10.19-rc29` / `26101929` and Intel `2026.10.19-rc29-intel1` / `261019291`; current policy pins the final source and RC28 previous-feed floors.

The local Keychain path is implemented and exercised; see [LOCAL-MAC-RELEASE.md](LOCAL-MAC-RELEASE.md). It remains restricted to Jeff's existing credentials and records same-user Mac custody. Hosted environment/runner/job isolation is a separate contract whose credential-bearing execution remains unproven. Control-only changes preserve qualified native payloads and complete source offers.

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

## Historical RC28 deliveries and publication

The historical qualification deliveries bind source `8f7ec4d3de4235ab5d36d5175c29121e3a26548a`, release mode, their trusted architecture-specific workflow, all five measured passing checks and `privateDataUsed=false`. The retained readback receipts verify the server artifact digest and exact `qualification.json` app/source payload hashes.

| Channel / identity | Successful run / immutable artifact | Server artifact SHA256 |
| --- | --- | --- |
| `macos-arm64` — `2026.10.16-rc28` / `26101628` | Run `39046812410`; artifact `12106277218` | `97dcc3423ff27a50f4e558f7c35ad065cad8a5ea3a8ca1670af0b2e1ad74d05b` |
| `macos-intel-test` — `2026.10.16-rc28-intel1` / `261016281` | Run `39046814619`; artifact `12106102831` | `c0a5bc8485847437a56f6f5e9fcfb31e85ce9e66f6c2b0228b91bc913c66c305` |

Qualification evidence: `rc28-arm-release-qualification/delivery-readback-receipt.json` and `rc28-intel-release-qualification/delivery-readback-receipt.json` in the October 16 protected-native-release evidence packet, verified October 17. These remain historical immutable build receipts.

Subsequent local signing, public package/source/feed publication and combined website promotion completed under control `067b2bc687e3af81d033923608679578814c535b`. The canonical receipt is `/Users/jeffmoore/Documents/🗣️ Voice Orchestrators/outputs/release-checks/2026-10-17/rc28-combined-website/completion.json`, completed `2026-10-17T17:31:48Z`, with website merge `7dd79178b3a028eee3d2c8e358da79b8cb9d88a0`, deployment `dpl_ChSk6sFXE75hatGNYW5q9qtDgxWr` and `physicalAcceptance=false`.

Its published feed bytes are ARM 1,561 bytes / `22c677a174a5e4688825fae98f242b23eb74b731be14da3d27252411446a072a` and Intel 1,654 bytes / `747b851faa2bb4efbb6be3ffdc8265710dcdcee51881e736158ca47ea2f9da2f`. Revalidate those against live state before RC29 signing/promotion. The retained RC26 dependency/source-closure baseline is separate and remains unchanged.

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

Every newly approved source must include `"qualificationProfile": "phone-run-recovery-v1"` in its reviewed `approvedSources` row. This field is qualification policy, independent of consent disclosure versions. Adding the profile does not authorize a source or release: exact source authorization, `releaseAuthorized`, normal owner-approved PR merges and all existing artifact/custody checks still apply.

In addition to the existing checks, this profile executes:

- `source/collector/macos/Tests/check_model.sh` → `collectorModel`.
- `source/collector/macos/Tests/check_window_lifecycle.sh` → `windowLifecycle` (real hidden AppKit windows on the macOS runner, synthetic model; no customer app activation).
- `source/collector/tests/test_bridge_run_timing.py` → `bridgeRunTiming`.
- `source/collector/tests/test_bridge_recovery.py` → `bridgeRecovery`.

All four files must exist as regular files inside the reviewed checkout; missing files, symlinks, failed commands and timeouts fail qualification. These results are emitted only after successful checks, as **schema 2**, the exact `qualificationProfile`, and the four additional strictly boolean `true` checks. The signer rejects schema-1 or incomplete results for a new source. The existing two RC28 tuples at `8f7ec4d3de4235ab5d36d5175c29121e3a26548a` alone retain their original schema-1 receipts and check path, allowing their immutable source to remain reproducible. A changed SHA, channel, version or build cannot use that exception.

The producer and receipt validator changes must be present in the final reviewed native source as well as protected main control. The native preview entry point must set this profile on its preview candidate; preview evidence still grants no release authority. This control change does not add or authorize a new source SHA.

For PRs targeting `release/native` and pushes to that branch, the existing required `release-control-checks` job depends on `native-phone-run-checks` passing on `macos-15`. That job uses the existing pinned Python/uv runtime and hash-locked dependencies to execute these four checks through `release_control/check_phone_run.py`. A failure or cancellation cannot become a green required check. Main-only control PRs keep the existing Python control suite without executing native code. The qualification profile adds no credentials, environment grants or additional human approval gate; owner approval and live protection follow the contract above.

## Signing, finalization and feed output

Either approved signing mode must consume only the exact immutable qualified artifact. Immediately before codesign, the signer independently audits actual input bytes again and compares the source map's pre-sign native hashes. Signing steps execute no candidate app/source code. The hosted workflow uses its protected environment and temporary keychain; the local implementation uses the existing owner Keychain and notarization profile under [LOCAL-MAC-RELEASE.md](LOCAL-MAC-RELEASE.md). Native Intel qualification remains the recorded Intel runner evidence; local signing on the ARM Mac does not constitute an Intel rebuild or execution check.

After app/DMG signing, notarization and staples, `finalize.py` preserves every source payload member byte-for-byte and appends only the two predeclared evidence paths. The finalization receipt pairs source-mapped pre-sign hashes with the full actual post-sign inventory; every final ZIP member is read back. It does not mutate in-app Legal after signing.

`feeds.py` calls the unchanged qualified `desktop_update_release.py.prepare`, preserving existing bundle IDs, public key, legacy ARM default channel, separate Intel Test channel and embedded signed-feed comments. It pins/retains the exact observed previous feed before making the new signed feed. The local preparation receipt still correctly says `locally-prepared-not-published`.

## Hosted isolated GitHub publication

In the installed hosted workflow, only the separate publication job has `contents:write`; it receives no Apple/Sparkle keys. `publicationEnabled=false` remains the fail-closed default; enabled publication still requires each exact source/channel authorization in protected main. Mode `publish` signs then promotes within the same workflow/run; it is not a second human approval. The separately authorized local implementation has its own custody/provenance contract and does not claim this hosted job isolation.

- Revalidate source/control custody and the publication environment, then require the exact signer artifact from the successful signer job in this same run **and attempt**.
- Verify all signed package/source/feed receipts, actual Ed25519 signatures, complete finalized source and descriptor/readback hashes.
- Compare the candidate with the live same-channel release inventory, including drafts, before starting and before publication. Equal/newer releases reject stale or conflicting runs; policy's initial floor is not the live authority.
- Atomically create a **new** lightweight tag at the authorized source SHA; an existing/raced tag is rejected. Use `gh release create --verify-tag`, not automatic tag creation with `--target`. Verify resolved tag→source again immediately before public edit.
- Upload into a draft without clobbering; read back every uploaded byte before making it public. Read all public asset bytes back afterward. A failed draft/tag is retained for inspection, never deleted/replaced automatically.
- Same-channel workflow concurrency serializes this reviewed publisher. Live order/feed/tag checks catch observed external changes, but GitHub has no atomic cross-release compare-and-swap here. Repository administrators/other publishing tools remain outside that mutex; this residual race is explicit in the receipt. These publisher checks do not establish platform-enforced immutable tags; tag-update/deletion restrictions require their own configuration evidence before that assurance can be claimed.

## Website owner packet

Successful native publication produces `website-handoff.json`; it does not establish website deployment. Common fields bind exact source/control commits, channel/CPU/bundle/version/build, public native release URL, package/source public URLs and exact byte records, feed public URL and canonical website path, and the expected previous feed hash/size with its retained rollback location. Hosted provenance uses the signed Actions artifact ID/digest/run/attempt. The local implementation instead uses `localSigningEvidence` with manifest, signature and delivery assets; `local-signed-delivery.zip` contains the unchanged full signed directory, including its feed receipt and previous-feed rollback, plus the signed manifest and `.sig`. It has no hosted `signedArtifact`. This local transport was exercised by the RC28 publication; each new candidate still needs its own sealed manifest, signature and public-byte receipts. The current native emitter still writes `websitePublisherImplemented=false` and `websitePromoted=false`; preserve that raw packet and record the independently supplied website adapter evidence separately. The implementation flag is a historical limitation of this emitter, not a production-promotion receipt. Only the website owner's actual verified signed-byte deployment can establish promotion; no documentation edit changes those emitted flags.

The existing website owner supplies the promotion adapter and owns the current canonical checkout. Reconcile its current implementation and receipt before handoff; do not open a duplicate execution lane. The following publication baseline comes from the completed RC28 deployment, and must be freshly verified before RC29 promotion.

| Surface | Retained publication contract; refresh before promotion |
| --- | --- |
| ARM feed | `public/phone/macos/appcast.xml`; rc28 / `26101628`; 1,561 bytes; SHA256 `22c677a174a5e4688825fae98f242b23eb74b731be14da3d27252411446a072a` |
| Intel feed | `public/phone/macos/intel-appcast.xml`; rc28-intel1 / `261016281`; 1,654 bytes; SHA256 `747b851faa2bb4efbb6be3ffdc8265710dcdcee51881e736158ca47ea2f9da2f` |
| Feed signatures | Embedded trailing Sparkle XML comments; **no Mac feed `.sig` sidecar**. The native package's `.dmg.sig` is a different artifact |
| Acquisition pointers | `src/lib/release/phone-companion.ts`; update consistently with the same new package/source/version |
| Exact feed tests | `tests/macos-arm-update-feed.test.ts`, `tests/macos-intel-update-feed.test.ts`: exact bytes/hash/key/channel/source/enclosure |
| Download/status fixtures | `tests/native-download-metadata.test.ts`, `tests/companion-update-status-ui.test.ts`: version/build/URLs and old/current/newer expectations; preserve Android/Windows |
| Diagnostic/telemetry admission | `src/lib/telemetry/events.ts` and `src/app/api/v1/companion-diagnostics/route.ts`: separate exact build allowlists must agree |
| Admission negative fixtures | `tests/telemetry.test.ts`, `tests/companion-diagnostics-route.test.ts`: reconcile RC29 admission/negative fixtures with both confirmed architecture-specific builds, preserve supported legacy paths and retain private-field rejection |
| Routing/cache | Existing anonymous GET/HEAD handling in `proxy.ts`; no-store headers in `next.config.ts` |
| Observed public origins | `https://amplifai.satoris.ai` and `https://amplifai-database-engine.vercel.app`; RC28 completion verified production readback; recheck current files and no-store headers during RC29 promotion |

The existing website owner has supplied and exercised a receipt-consuming adapter for RC28. RC29 acceptance must verify that its current implementation updates feed, acquisition, release-pinned tests and diagnostic allowlists together for the **actual signed public RC29 bytes**. It must independently verify the signed feed/key/channel/CPU/newer build, official immutable assets, current highest published channel build and expected previous website feed. Deployment promotion needs a real expected-state/CAS or serialized equivalent to reject stale/concurrent packets, then both-origin feed/download readback and exact rollback custody. Native CI should receive only scoped dispatch/artifact authority for that interface, never general website deployment credentials. No new feed host or feed-only copying shortcut is part of this work.


## Custody and enforcement handoff

Follow [PROTECTED-MAC-RELEASE.md](PROTECTED-MAC-RELEASE.md#runtime-custody-evidence-and-limits) for recorded runtime denials, accepted protected merges and their limits. Its hosted provisioning sequence remains available for that route: the audit credential belongs in both exact named environments, Apple/Sparkle material only in signing. The existing Sparkle key upload succeeded with public continuity and cleanup evidence; complete hosted custody/signing remains unproven. The exercised local route follows [LOCAL-MAC-RELEASE.md](LOCAL-MAC-RELEASE.md) with current owner-approved control and exact source authorization. Record metadata-only custody receipts, never secret values or export contents.

Release readiness requires separate receipts for the exact source/build delivery, signing/notarization in the selected approved mode, public package/source/feed bytes, website production promotion, installed app identity and Jeff's physical iPhone transfer plus explicit source review/Save. None implies the next. Existing deployment authority carries through these technical checks without another routine deployment permission request.
