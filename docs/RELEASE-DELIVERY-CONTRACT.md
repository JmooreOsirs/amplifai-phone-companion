# Native delivery and website promotion contracts

This is the integration contract for PR #4. Its native helpers are implemented but inactive. Current source branches do not yet emit the required source-map artifact; the website has no promotion endpoint/job. Neither dependency may be replaced with an invented passing receipt.

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

Use the existing hash-locked Python environment for this CLI. It executes the collector/bridge tests, three native Swift check scripts, actual frozen synthetic worker and frozen status checks; after **all executable qualification** it audits the final app again and compares every source-mapped native hash. It validates the clean Git checkout, every authored/source ZIP member and matching Legal bytes before emitting anything. No product credentials are allowed in this job.

5. Upload only `qualified-delivery/` with the existing pinned upload-artifact action (`ea165f8d65b6e75b540449e92b4886f43607fa02`), `if-no-files-found: error`, no `always()` success artifact on failure. Retain the artifact ID and server digest; the release dispatcher takes these exact values. `qualification.json`, `app.zip` and `source.zip` are the only three root files. No human approves those generated hashes again.

The missing native integration work is specific: preserve/integrate the newer product fixes, update the old source-workflow branch/version pins through PR review, add the source-closure/map emitter and five-file Legal staging for the **actual new app**, then call the producer and retain its output. Current ARM build-only CI does not upload this qualified artifact. This draft does not silently select rc26/rc27 as an authorized release.

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

Only the separate publication job has `contents:write`; it receives no Apple/Sparkle keys. `publicationEnabled=false` is the inactive default. Mode `publish` signs then promotes within the same workflow/run; it is not a second human approval.

- Revalidate source/control custody and the publication environment, then require the exact signer artifact from the successful signer job in this same run **and attempt**.
- Verify all signed package/source/feed receipts, actual Ed25519 signatures, complete finalized source and descriptor/readback hashes.
- Compare the candidate with the live same-channel release inventory, including drafts, before starting and before publication. Equal/newer releases reject stale or conflicting runs; policy's initial floor is not the live authority.
- Atomically create a **new** lightweight tag at the authorized source SHA; an existing/raced tag is rejected. Use `gh release create --verify-tag`, not automatic tag creation with `--target`. Verify resolved tag→source again immediately before public edit.
- Upload into a draft without clobbering; read back every uploaded byte before making it public. Read all public asset bytes back afterward. A failed draft/tag is retained for inspection, never deleted/replaced automatically.
- Same-channel workflow concurrency serializes this reviewed publisher. Live order/feed/tag checks catch observed external changes, but GitHub has no atomic cross-release compare-and-swap here. Repository administrators/other publishing tools remain outside that mutex; this residual race is explicit in the receipt. Activation should prohibit tag updates/deletions for the managed release tags and prove that configuration before claiming immutable tag enforcement.

## Website owner packet

The successful native publication output includes `website-handoff.json`, not a fabricated website deployment receipt. It contains exact source/control commits, channel/CPU/bundle/version/build, public native release URL, signed Actions artifact ID/digest/run/attempt, package/source public URLs and exact byte records, feed public URL and canonical website path, and the expected previous feed hash/size with its retained rollback location. It explicitly says `websitePublisherImplemented=false` and `websitePromoted=false`.

The root's read-only website audit verified the current owner checkout is `amplify-database-engine-account-release-oct14`, branch `codex/course-shell-integration-oct14`, source `7746f48`. Preserve that owner; do not use the stale base checkout or open a duplicate execution lane.

| Surface | Current exact contract |
| --- | --- |
| ARM feed | `public/phone/macos/appcast.xml`; rc26 / `26101526`; 1,561 bytes; SHA256 `afd0ede099c9219853c4626f1466cff4cde6dfd95e7c42abd9a1164d89b72b13` |
| Intel feed | `public/phone/macos/intel-appcast.xml`; rc26-intel1 / `261015261`; 1,667 bytes; SHA256 `6207d5831dc6f0f638445458379393ac3a9fa7539d7d892e42b54846ec76031c` |
| Feed signatures | Embedded trailing Sparkle XML comments; **no Mac feed `.sig` sidecar**. The native package's `.dmg.sig` is a different artifact |
| Acquisition pointers | `src/lib/release/phone-companion.ts`; update consistently with the same new package/source/version |
| Exact feed tests | `tests/macos-arm-update-feed.test.ts`, `tests/macos-intel-update-feed.test.ts`: exact bytes/hash/key/channel/source/enclosure |
| Download/status fixtures | `tests/native-download-metadata.test.ts`, `tests/companion-update-status-ui.test.ts`: version/build/URLs and old/current/newer expectations; preserve Android/Windows |
| Diagnostic/telemetry admission | `src/lib/telemetry/events.ts` and `src/app/api/v1/companion-diagnostics/route.ts`: separate exact build allowlists must agree |
| Admission negative fixtures | `tests/telemetry.test.ts`, `tests/companion-diagnostics-route.test.ts`: currently reject `26101527`; when approving that build, replace the neighboring negative fixture and retain private-field rejection |
| Routing/cache | Existing anonymous GET/HEAD handling in `proxy.ts`; no-store headers in `next.config.ts` |
| Observed public origins | `https://amplifai.satoris.ai` and `https://amplifai-database-engine.vercel.app`; both exact files returned 200/no-store at the audit |

No narrow website promotion interface exists. The website owner must implement a protected receipt-consuming adapter that updates feed, acquisition, release-pinned tests and diagnostic allowlists together. It must independently verify the signed feed/key/channel/CPU/newer build, official immutable assets, current highest published channel build and expected previous website feed. Deployment promotion needs a real expected-state/CAS or serialized equivalent to reject stale/concurrent packets, then both-origin feed/download readback and exact rollback custody. Native CI should receive only scoped dispatch/artifact authority for that interface, never general website deployment credentials. No new feed host or feed-only copying shortcut is part of this work.
