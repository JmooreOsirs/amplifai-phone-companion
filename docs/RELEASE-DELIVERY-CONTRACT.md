# Native delivery and website promotion contracts

## RC33 diagnostic correction scope — October 21

Source-stage record: ARM `2026.10.21-rc33` / `26102133`; Intel Test `2026.10.21-rc33-intel1` / `261021331`. Publication is established by the external protected publisher and website receipts, not this source document. Routine publish/deploy authority was explicitly renewed by Jeff for this correction phase.

This candidate adds fail-closed larger encrypted Messages diagnostics and integer-only local call accounting. Failed count writes remain eligible for one retry at each subsequent review/pair opportunity; only successful persistence enters the deduplication cache. Independent source/count storage failures remain visible until all failed histories actually recover. Existing event shapes, consent/selection and transferred metadata remain unchanged. Source-stage tests cover the exact transient-write and mixed-history failures.

Larger encrypted Messages still fail `selected_payload_length`; no truncation, SQLite parsing on contradictory growth or integrity-only acceptance is introduced. The 4,096-byte observed Size discrepancy remains unexplained. The existing producer-Digest/ciphertext comparison is an assumption whose real byte domain is not established by synthetic fixtures. A diagnostic mismatch is not proof of corruption. Historical raw proofs were cleaned up; current work uses sanitized logs and pinned source. Private capture preparation is parked at Jeff's request.

The required shared native gate additionally executes `test_call_accounting` and `test_source_diagnostic_flow` in the reviewed source and protected control, preserving the existing qualification profile and receipt checks. Full-size production helper/bridge integration exercises exact source IDs, two real 30-second refresh cycles, the 65-second watchdog, same-page replay, receipt ACK, exact saved-report return, and aggregate history survival after ordinary session cleanup. Synthetic account acknowledgements are not physical iPhone/account persistence acceptance.

Website receiver `unsupported_size_growth` compatibility must be deployed before distributing these builds; final build admission and feeds must derive from qualified public signed metadata. No SQL migration, unrelated Google change, customer collection/Save/reset or installed-app replacement is part of native qualification. Preserved RC32 and earlier sections below are historical records for their named candidates.


RC29 was published on October 19 from native source `71dfabe3c3002e7a7787c649607607adafd29558` and protected control `e0e932a9d7d38de5cc63e64c697dda50e450213c`. Its coordinated website merge is `350ed70b4cdc0c17139a79418d0b0abfb4a2663a`, deployment `dpl_EyR8uZsANaMwAoGqLA4wyFu6ATBr`. The next candidate is RC30: ARM `2026.10.20-rc30` / `26102030`, Intel Test `2026.10.20-rc30-intel1` / `261020301`. Each changed native source requires exact-source qualification and approved merged-source custody before signing. Publication evidence does not establish physical acceptance.

Use the separately reviewed protected-main release control for local signing/publication with the existing owner Keychain and notarization profile. Native source includes its frozen qualification producer; its inactive policy and older publisher are not the production control plane. The RC30 qualification profile is documented below; the current policy must authorize the final full source SHA before a release-mode build. Jeff owns the physical iPhone test and account review. The website owner coordinates exact migrations, compatibility admission and signed-byte promotion.

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

Use the existing hash-locked Python environment for this CLI. It executes the collector/bridge tests, the native Swift checks required by the source qualification profile, actual frozen synthetic worker and frozen status checks; after **all executable qualification** it audits the final app again and compares every source-mapped native hash. It validates the clean Git checkout, every authored/source ZIP member and matching Legal bytes before emitting anything. No product credentials are allowed in this job.

5. Upload only `qualified-delivery/` with the existing pinned upload-artifact action (`ea165f8d65b6e75b540449e92b4886f43607fa02`), `if-no-files-found: error`, no `always()` success artifact on failure. Retain the artifact ID and server digest; the release dispatcher takes these exact values. `qualification.json`, `app.zip` and `source.zip` are the only three root files. No human approves those generated hashes again.

Merged PR #5 installed the source-closure/map emitter, five-file Legal staging and producer/upload hook. RC30 must be clean, normally reviewed/merged and built/qualified anew for each architecture after implementation, identity and documentation freeze. No historical app or successful test suite substitutes for that exact-source delivery.

The candidate identities above are not source authorization entries. After source qualification and normal merge, the protected-main policy must bind both identities to the final full source SHA and trusted workflows. Advance the previous-feed pins and published-build floors to the verified RC29 release. Preserve its sealed artifacts and the separate historical dependency/source-closure pins.

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

## Signing, finalization and feed output

The signer downloads only that immutable artifact. Immediately before codesign, it independently audits actual input bytes again and compares the source map's pre-sign native hashes. It executes no app/source code while credentials are available.

After app/DMG signing, notarization and staples, `finalize.py` preserves every source payload member byte-for-byte and appends only the two predeclared evidence paths. The finalization receipt pairs source-mapped pre-sign hashes with the full actual post-sign inventory; every final ZIP member is read back. It does not mutate in-app Legal after signing.

`feeds.py` calls the unchanged qualified `desktop_update_release.py.prepare`, preserving existing bundle IDs, public key, legacy ARM default channel, separate Intel Test channel and embedded signed-feed comments. It pins/retains the exact observed previous feed before making the new signed feed. The local preparation receipt still correctly says `locally-prepared-not-published`.

## Isolated GitHub publication

Only the separate publication job has `contents:write`; it receives no Apple/Sparkle keys. `publicationEnabled=false` is the inactive default. Hosted mode `publish` signs then promotes within the same workflow/run. The current local-Keychain path instead seals signing output first, then verifies that unchanged delivery with `local_release.py publish`; neither path requires a second routine human approval.

- Revalidate source/control custody and the publication environment, then require the exact signer artifact from the successful signer job in this same run **and attempt**.
- Verify all signed package/source/feed receipts, actual Ed25519 signatures, complete finalized source and descriptor/readback hashes.
- Compare the candidate with the live same-channel release inventory, including drafts, before starting and before publication. Equal/newer releases reject stale or conflicting runs; policy's initial floor is not the live authority.
- Atomically create a **new** lightweight tag at the authorized source SHA; an existing/raced tag is rejected. Use `gh release create --verify-tag`, not automatic tag creation with `--target`. Verify resolved tag→source again immediately before public edit.
- Upload into a draft without clobbering; read back every uploaded byte before making it public. Read all public asset bytes back afterward. A failed draft/tag is retained for inspection, never deleted/replaced automatically.
- Same-channel workflow concurrency serializes this reviewed publisher. Live order/feed/tag checks catch observed external changes, but GitHub has no atomic cross-release compare-and-swap here. Repository administrators/other publishing tools remain outside that mutex; this residual race is explicit in the receipt. Activation should prohibit tag updates/deletions for the managed release tags and prove that configuration before claiming immutable tag enforcement.

## Website owner packet

The successful native publication output includes `website-handoff.json`, not a fabricated website deployment receipt. It contains exact source/control commits, channel/CPU/bundle/version/build, public native release URL, signed Actions artifact ID/digest/run/attempt, package/source public URLs and exact byte records, feed public URL and canonical website path, and the expected previous feed hash/size with its retained rollback location. The current native emitter still writes `websitePublisherImplemented=false` and `websitePromoted=false`; preserve that raw packet and record the independently supplied website adapter evidence separately. The implementation flag is a historical limitation of this emitter, not a production-promotion receipt. Only the website owner's actual verified signed-byte deployment can establish promotion; no documentation edit changes those emitted flags.

The earlier read-only website audit recorded checkout `amplify-database-engine-account-release-oct14`, branch `codex/course-shell-integration-oct14`, source `7746f48`. That is historical audit provenance, not a claim about the current website head. Reconcile the existing website owner's supplied adapter/Preview receipt and current canonical checkout before final handoff; do not open a duplicate execution lane.

| Surface | Retained audit contract; refresh before final promotion |
| --- | --- |
| ARM feed | `public/phone/macos/appcast.xml`; rc29 / `26101929`; 1,561 bytes; SHA256 `eeffb2be34d95bb8cdbb74e303200ca57ac6ea1caab2feacaac32c873c689313` |
| Intel feed | `public/phone/macos/intel-appcast.xml`; rc29-intel1 / `261019291`; 1,654 bytes; SHA256 `31be4d7d539c3aadbb4a2fb3c9442bb87d73ab665486b659159d4b97452c1f70` |
| Feed signatures | Embedded trailing Sparkle XML comments; **no Mac feed `.sig` sidecar**. The native package's `.dmg.sig` is a different artifact |
| Acquisition pointers | `src/lib/release/phone-companion.ts`; update consistently with the same new package/source/version |
| Exact feed tests | `tests/macos-arm-update-feed.test.ts`, `tests/macos-intel-update-feed.test.ts`: exact bytes/hash/key/channel/source/enclosure |
| Download/status fixtures | `tests/native-download-metadata.test.ts`, `tests/companion-update-status-ui.test.ts`: version/build/URLs and old/current/newer expectations; preserve Android/Windows |
| Diagnostic/telemetry admission | `src/lib/telemetry/events.ts` and `src/app/api/v1/companion-diagnostics/route.ts`: separate exact build allowlists must agree |
| Admission negative fixtures | `tests/telemetry.test.ts`, `tests/companion-diagnostics-route.test.ts`: the earlier audit rejected `26101527`; reconcile current candidate admission/negative fixtures with both confirmed architecture-specific builds and retain private-field rejection |
| Routing/cache | Existing anonymous GET/HEAD handling in `proxy.ts`; no-store headers in `next.config.ts` |
| Observed public origins | `https://amplifai.satoris.ai` and `https://amplifai-database-engine.vercel.app`; both exact files returned 200/no-store at the audit |

The existing website owner has a receipt-consuming adapter with retained RC29 production evidence. Final acceptance must verify that the supplied implementation updates feed, acquisition, release-pinned tests and diagnostic allowlists together for the **actual signed public candidate bytes**. It must independently verify the signed feed/key/channel/CPU/newer build, official immutable assets, current highest published channel build and expected previous website feed. Deployment promotion needs a real expected-state/CAS or serialized equivalent to reject stale/concurrent packets, then both-origin feed/download readback and exact rollback custody. Native CI should receive only scoped dispatch/artifact authority for that interface, never general website deployment credentials. No new feed host or feed-only copying shortcut is part of this work.


## Custody and enforcement handoff

The existing local Keychain signing path was used for RC28 and RC29. Reuse its reviewed protected-main commands and non-secret custody receipts; do not repeat credential provisioning. Hosted execution, if separately selected, retains the custody requirements in PROTECTED-MAC-RELEASE.md. Native build jobs remain free of product secrets, and source checkout code is never executed inside the signing step.

Track separate receipts for exact source/build delivery, signing/notarization, public package/source/feed bytes, website production promotion, installed app identity and physical iPhone transfer plus explicit source review/Save. None implies the next. Physical testing was explicitly deferred until after publication; it is not a publication hold. Existing qualified-release authority carries through the technical checks without another routine deployment question.


### Phone run and recovery qualification profile

Every newly approved source must include `"qualificationProfile": "phone-run-recovery-v1"` in its reviewed `approvedSources` row. This field is qualification policy, independent of consent disclosure versions. Adding the profile does not authorize a source or release: exact source approval, `releaseAuthorized`, either-owner approval and all existing artifact/custody checks still apply.

In addition to the existing checks, this profile executes:

- `source/collector/macos/Tests/check_model.sh` → `collectorModel`, including the mandatory real30-second periodic-refresh integration. The producer supplies its pinned interpreter as `AMPLIFAI_TEST_PYTHON`; qualification never selects the developer quick-only mode.
- `source/collector/macos/Tests/check_window_lifecycle.sh` → `windowLifecycle` (real hidden AppKit windows on the macOS runner, synthetic model; no customer app activation).
- `source/collector/tests/test_bridge_run_timing.py` → `bridgeRunTiming`.
- `source/collector/tests/test_bridge_recovery.py` and `test_bridge_refresh.py` → `bridgeRecovery`, including typed refresh, exact-page retry and terminal authority/expiry checks.
- `source/collector/tests/test_call_accounting.py` and `test_source_diagnostic_flow.py` additionally require diagnostic retention recovery, private aggregate counts and immutable failure/receipt evidence.

All seven entry-point files must exist as regular files inside the reviewed checkout; missing files, symlinks, failed commands and timeouts fail qualification. These results are emitted only after successful checks, as **schema 2**, the exact `qualificationProfile`, and the four additional strictly boolean `true` checks. The signer rejects schema-1 or incomplete results for a new source. The existing two RC28 tuples at `8f7ec4d3de4235ab5d36d5175c29121e3a26548a` alone retain their original schema-1 receipts and check path, allowing their immutable source to remain reproducible. A changed SHA, channel, version or build cannot use that exception.

The producer and receipt validator changes must be present in the final reviewed native source as well as protected main control. The native preview entry point must set this profile on its preview candidate; preview evidence still grants no release authority. This control change does not add or authorize a new source SHA.

For PRs targeting `release/native` and pushes to that branch, the existing required `release-control-checks` job depends on `native-phone-run-checks` passing on `macos-15`. That job uses the existing pinned Python/uv runtime and hash-locked dependencies to execute these required checks through `release_control/check_phone_run.py`. A failure or cancellation cannot become a green required check. Main-only control PRs keep the existing Python control suite without executing native code. No credentials, environment grants, branch protections or reviewer requirements are changed.
