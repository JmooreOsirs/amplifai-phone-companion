# October 8 processing-repair candidate inputs

Local processing-repair candidate only: `2026.10.08-rc6`, build `26100806`. Existing bundle identifier
`ai.satoris.amplifai.phone.candidate` is preserved. No signing, notarization,
publication, physical-device or authenticated browser proof is implied by this source file.
This source file alone is not distribution clearance. The native unchecked, operation-bound
pre-Connect collection approval is now source/model qualified: Agree alone does
not collect, and Connect/helper launch require its fresh run-bound receipt.
Actual rendered keyboard/focus, packaged installation and physical-device
permission/Trust checks remain open. A passive note or later sharing prompt
cannot replace this local collection approval.

- Build the actual Swift package with `swift build --package-path collector/macos`.
- Package the current `Info.plist`, Swift binary and current Python helper modules
  together; the saved-ack protocol requires the matching helper, not an rc2 helper.
- Generate the icon with `Tools/prepare_app_icon.swift` from the accepted
  `public/brand/amplifai-by-nexus-original.png`. Copy the generated
  `AmplifaiPhone.icns` into the app's `Contents/Resources/AmplifaiPhone.icns`.
  The top-level `script/build_and_run.sh --build-only` now compiles the packaging
  tool in an owned temporary directory, generates this icon and copies it into
  the matching bundle. The temporary generator inputs are removed on exit.
  No alternate logo, font or generative asset is used.
- Keep the existing logo PNG resource for the in-window brand mark.
- Run `sh collector/macos/Tests/check_flow.sh` and the existing
  `sh scripts/test_macos_recovery.sh` against actual model/protocol source.

## Earlier backup repair and current processing incident

The public rc5 build (`26100805`) remains the rollback candidate until this
source is separately packaged and released. Two earlier owner attempts in ACK2 ended
with the generic `collection_failed` code at the backup stage and zero received
bytes; this does **not** prove a backup file began transferring. The current helper
maps an upstream host-space rejection to `backup_host_space`, and suppresses a
zero-byte final progress event after a DeviceLink handshake/preflight failure.
The filtered DeviceLink receiver now advertises logical stream capacity to the
phone because it discards unselected backup bytes. Every selected-file write and
parsing copy still checks real host free space and preserves a 2 GiB reserve.
Synthetic tests cover a 275 GiB announced backup with small selected-file
headroom; they are not physical-device completion evidence.

A later owner run on build `26100804` streamed 71.46 GB, retained 1.18 GB and
stopped at local processing with `unsupported_schema`. This candidate isolates
manifest-listed but missing optional call/SMS payloads while retaining valid
contacts, rejects malformed selected-file entry types, and gives distinct safe
categories for backup-control, required-contact, missing-file and integrity
failures. It cannot identify the exact cause of that private run from the old
generic code, and no physical retry of this candidate has occurred.

The Mac app's fresh collection approval now includes automatic delivery of only
its bounded support code after an error. The fixed HTTPS endpoint accepts the
exact current build/code shape, forwards only allowlisted stage/category/counts
to existing telemetry, and gives an explicit delivered/failed state with retry.
It sends no backup bytes, source rows, phone identifier, account credentials or
password. The website endpoint must be live before distributing this build.
Each actual collection retry requires its own fresh local approval. Initial
unlock/Trust is required to establish a connection; a later screen lock alone
does not prove the connection failed.

## Browser completion contract

`POST /v1/pair` adds immutable `handoffId` and `payloadSha256`. Metadata delivery
and `/v1/complete` mean received, **not saved**. Only the normal account client,
after validating all intended actual source-save receipts, may send token-
authenticated `POST /v1/acknowledge-save` with exactly
`{handoffId,payloadSha256,saved:true}`. The endpoint requires the exact existing
Origin/Host and a completed matching transfer. The pairing code expires after
five minutes, but a completed transfer's matching ACK remains retryable while
the review is open. New companions require a second authenticated
`POST /v1/confirm-ack-received` with `{handoffId,payloadSha256,received:true}`;
the native window cannot auto-finish before that final response is written.
Expired, failed, partially saved or unacknowledged work keeps the native recovery
copy. A close/discard action explicitly cancels it; no delivery inference exists.

This source seam alone is not authenticated account-save or focus/stacking proof.
Before automatic-close acceptance, qualify the actual account client receipt
guard and cross-layer acknowledgement. Before a public signed release, qualify
normal install, OS Trust/password dialogs, window accessibility, signing,
notarization, matching-source archive and root-owned release hashes.

The browser receiver returns `{payload, imports, handoff}`. `handoff` is a readonly
UUID/hash binding or `null` for published old-protocol companions. The token is
private WeakMap state, not a returned/serialized field. Keep the original
`NativePreview` object in the current account flow's memory; reconstructing it
or starting another pairing cannot acknowledge the previous run.

`acknowledgeNativeSavedPreview(preview, confirmation, signal)` accepts only an
explicit complete receipt set, with this shape:

```text
{ intendedSources: [{source, fileHash,
    parts: [{payloadSha256, coverage, parsedCount, transferKey, partIndex, partCount}]}],
  receipts: [actual accepted server import receipts] }
```

The intended source subset is deliberate, never inferred from declined/unselected
categories. Expected part fields come from the immutable reviewed POST inputs,
not from receipt JSON. Non-multipart expectations have explicit null transfer
fields. Every intended part must have exactly one accepted matching receipt;
extra, duplicate, missing, stale, changed-count/hash/coverage or partial parts
fail closed before acknowledgement. This validates source/run fidelity, not
managed-session authority. The root account integration fences current owner,
enrollment, collection consent, original retention generation and review identity.
This has offline cross-client fixture proof, not a managed-session/server or
physical-device proof.

The account source now retains the original `NativePreview` and frozen expected
part descriptors computed before POST. Save records validated receipts only;
it does not acknowledge automatically. Every category requires an explicit Save
or Do not save choice. An attempted source cannot be silently declined because
some parts may have committed. Confirm saved sources and finish performs fresh
collection/retention checks plus current retained-source readback before invoking
the matching native acknowledgement with the same cancellation signal. Old
protocol, expired/revoked/stale/partial and failed checks retain the native review.
Normal managed Auth, actual source-save transaction and packaged cross-layer
acknowledgement remain release gates; offline fixture success is not those proofs.

The Android `2026.10.08-rc4` owner-test APK is independently published with
its matching source and existing signing certificate. It does not qualify this
Mac helper, normal installation or physical phones. Previously published Mac
and Android packages remain rollback artifacts.
