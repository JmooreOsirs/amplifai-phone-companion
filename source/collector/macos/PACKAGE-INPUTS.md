# October 7 local native candidate inputs

Local candidate only: `2026.10.07-rc3`, build `26100703`. Existing bundle identifier
`ai.satoris.amplifai.phone.candidate` is preserved. No signing, notarization,
publication, physical-device or authenticated browser proof is implied by this source file.
This is not a final release candidate. The native unchecked, operation-bound
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

## Browser completion contract

`POST /v1/pair` adds immutable `handoffId` and `payloadSha256`. Metadata delivery
and `/v1/complete` mean received, **not saved**. Only the normal account client,
after validating all intended actual source-save receipts, may send token-
authenticated `POST /v1/acknowledge-save` with exactly
`{handoffId,payloadSha256,saved:true}`. The endpoint requires the exact existing
Origin/Host, a completed transfer and an unexpired matching run. The native
window polls that run and waits for acknowledged save plus clean helper exit.
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

The Android `2026.10.01-rc1` / `26100101` launcher update is independently
published with its matching source and existing signing certificate. The newer
Android `2026.10.07-rc1` source candidate is not published. Neither qualifies
this Mac helper, normal installation or physical phones; existing published
Mac rc2 and Android October 1 releases remain rollback artifacts.
