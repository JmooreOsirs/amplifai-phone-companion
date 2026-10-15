# October 15 rc26 package inputs

The accepted source baseline is rc24; `docs/DESKTOP-UPDATE-SOURCE-PINS.json`
records the released source archives and planned platform identities.
This ARM source is versioned `2026.10.15-rc26`, build `26101526`;
it requires a newly signed/notarized app and newly frozen Python helper because
the updater and independent running-app status protocol changed. Existing bundle identifier
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
- The rc12 review adds atomic all-page contact selection, a separately
  confirmed server-bound destination account for no-code Connection, a Copy
  fallback for old manual pairing, and digest-bound optional-source reason
  and encryption evidence. Connection never means account Save; the existing
  per-source receipt/readback/ACK contract still governs completion.
- The rc13 review adds optional local contact-name suggestions with explicit
  group deselection and generation-bound Undo. Matches are inspectable guesses,
  never automatic exclusions or source edits.
- The rc14 helper preserves allowlisted optional Call/SMS source rejection reasons
  through local review and digest-bound browser previews. This improves diagnosis
  without identifying the cause of any earlier private run or changing source
  integrity decisions. Matching website copy gives a plain course-progress
  timeout/network retry path without treating a 50-item receipt page as missing data.
- The rc15 review separates Main and Review contacts, starts Review excluded,
  and keeps all-page selection bound to stable reviewed IDs. The owner may
  include Review contacts individually or move a locally suggested group and
  undo that exact selection change. Save and continue preserves the reviewed
  selection before opening the signed-in account journey; navigation alone
  does not save data or acknowledge the Mac review.
- The rc16 error panel makes the safe failure details selectable and copyable.
  On a workspace-low-space failure it also reports current free space on the
  Mac Application Support volume, explicitly distinct from space at the failed
  attempt. The selected-source and further 2 GiB reserve protections are
  unchanged; copying details does not include phone records, passwords or paths.
- The rc17 setup makes a fresh owner choose between confirmed encrypted-backup
  preparation and continuing with potentially unavailable Calls. The first
  choice performs a read-only, literal-boolean phone backup-encryption preflight
  before backup transfer, then checks the completed Manifest. Off or unknown
  state stops before transfer; a later unencrypted Manifest rejects review.
  The companion does not enable encryption or create a Finder backup. A Finder
  setting change is owner-controlled, may start its own full backup requiring
  separate Mac storage, and does not guarantee every historical call. Missing
  Calls/Messages metrics say Unavailable rather than an observed zero.
- The rc18 review adds a deliberate all-contact choice across the complete,
  verified Main and Review workspace while keeping Main as the initial view.
  Review is not automatically selected.
  All/Main/Review filters, exact selected/available counts and separate group
  marks support page-spanning movement with confirmation and generation-bound
  Undo. Group movement is not an account Save or a source-data edit.
- The rc19 helper corrects named SQLite integer-primary-key row identity for
  Contacts and Calls. A smaller decrypted encrypted SQLite main can be accepted
  only with an exact producer ciphertext digest, intact private copy, no
  sidecars, and source-specific read-only SQLite validation. Failure remains
  fatal for Contacts or Unavailable for optional Calls/Messages. Its local
  copyable size-failure detail contains only bounded source, role, counts and
  proof statuses; it cannot identify the cause of an earlier private run.
- The rc20 app binds destination approval to the account/course and exact
  displayed handoff and intent. A newer request waits for actual Mac sheet
  dismissal; stale actions cannot approve it. Local Reconnect keeps a retained
  reviewed import, and replacement cannot close a bridge after its first
  bound account-save receipt. Editing contacts after pairing requires an
  explicit Change selected contacts action and a helper-accepted revocation;
  a racing first receipt keeps the original selection and ACK endpoint locked.
  The account browser still makes separate
  source Save decisions and confirms the final ACK.
- The rc21 setup reads the current local-backup encryption setting before
  asking the owner to approve a new collection. Only a literal true result
  presents the encrypted-backup path; off and unknown remain distinct.
  Finder instructions put the owner-controlled setting and separate full-backup
  storage warning first. The read-only check does not start a backup, alter
  encryption, or guarantee historical Calls. The explicit fallback may leave
  Calls unavailable. The collected run still checks the setting and completed
  Manifest. Read-only check cancellation, stale replies, abnormal exits and
  diagnostics cannot grant collection authority. The current setup step is
  visually primary, with full privacy facts before unchecked consent.
- The rc22 updater treats disabled automatic checks as a manual-only state,
  separate from an attempted check that failed. App updates stay out of active
  collection and unsaved review, while idle shows the current version and
  signed Mac download guide. Feed failures stay collapsed with an accurate
  manual-install fallback. Existing safe-install guards still refuse a relaunch
  until local work and its matching account receipt have settled.
- The rc23 setup keeps one active Prepare, Consent, or Connect card and moves
  Finder guidance and technical detail behind explicit help. The unchecked
  local disclosure and each later account-source approval remain separate.
  An unencrypted backup still admits an integrity-checked Calls database when
  present; absent Calls remain Unavailable, never a measured zero. The app does
  not change Finder encryption or claim complete call history.
- The rc24 guide shows one current setup step, illustrates where to find the
  iPhone, Backups and encryption setting in Finder, and keeps read-only On, Off
  and Unknown preparation distinct. Receive and Read show measured local
  progress without an invented whole-flow percentage. The contact workspace
  separates page count from whole-set selection, and displays source-backed
  name-field hints only when present. A missing source field is not inferred.
  Existing consent, private storage, selected IDs, per-source Save and ACK
  boundaries are unchanged; physical call coverage remains unproven.
- The rc25 updater retains the signed ARM feed/key and uses a separate qualified
  Intel feed and bundle identity. Intel packaging remains disabled without its
  explicit new-build opt-in; its earlier disabled package requires one manual
  bootstrap. Info.plist enables automatic download/install defaults while
  Sparkle preserves saved user choices. Actual scheduled-check, relaunch and
  install-on-quit delegates retain the live idle/review/pairing/ACK installation
  gate. Once status ownership is ready, signed automatic checks/downloads may
  proceed during local work; manual dialogs and installation remain deferred.
  A separately owned frozen helper process serves bounded status on port 48752;
  an occupied port prevents this app from collecting and never takes over the
  other listener. Status mode does not read a workspace or collect phone data.
  New notices reuse the existing Arial sizes, Brand colors, VStack spacing and
  `.panel()` surface; this change introduces no new native visual primitives.

### Native update/status reference mapping

| Changed element | Existing rc24 primitive |
| --- | --- |
| Update header and status copy | Existing Arial 16 bold/regular, `Brand.heading` / `Brand.body` |
| Conflict/unavailable notice heading | Existing residue-notice Arial 18 bold and `Brand.heading` |
| Notice body and vertical rhythm | Existing update-panel Arial 16 regular and `VStack(spacing: 10)` |
| Check/download controls | Existing `SecondaryButton`: Arial 14 bold, 18 horizontal padding, 40 minimum height, 8 corner radius |
| Panels | Existing `.panel()`: 20 padding, `Brand.panel`, 12 corner radius, 1-point `Brand.border` |
| Overall layout | Existing 24 outer padding/stack spacing and 880 maximum content width |

`script/render_macos_update_acceptance.py` renders the actual current and pinned
rc24 `ContentView` offscreen at matched 375/820 widths with synthetic model/event
inputs. It creates no window, starts no controller or collection, and reads no
owner workspace. Current/manual states retain their collapsed panel; busy
deferred work retains its hidden update panel. Those six rendered PNG pairs
must be byte-identical. Available-copy wrapping and the new instance notice
are the intentional visual changes; shared Brand/panel/button source must match.
- Run `sh collector/macos/Tests/check_flow.sh` and the existing
  `sh scripts/test_macos_recovery.sh` against actual model/protocol source.
- This source pages the local contact preview in 200-row helper events and
  loads more/searches on demand. The Swift model validates totals, cursors,
  duplicate IDs and stale responses while preserving selected IDs. A local
  review now freezes the selected IDs and requires an exact echoed review ID
  and selected count before browser pairing can open. Sanitized rows and
  selected IDs live in a private temporary SQLite review store. The v2 handoff
  has an immutable manifest and bounded, chained source pages; the matching
  website must confirm every page and durable account receipts before saved ACK.
  Do not pair this Swift app with the rc7 Python helper.

## Earlier backup repair and current processing incident

The public rc7 build (`26100807`) was the rollback candidate for the earlier
capacity repair; published rc11 is this candidate's rollback. Two earlier owner attempts in ACK2 ended
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
generic code. A later owner rc6 run stopped with `source_capacity_limit` after
21.73 GB received and 616.82 MB retained. The generic code cannot identify
which capacity guard fired. The rc7 source candidate fixes three plausible
avoidable control-volume failures with bounded file-backed/plist reads and
private-error draining; it leaves path, disk, schema and finite parsing guards
intact, and adds distinct safe capacity categories. The exact owner failure
is not attributed to one guard and rc7 has not passed physical collection.

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

The Android `2026.10.09-rc9` website APK is independently published with
its matching source and existing signing certificate. It does not qualify this
Mac helper, normal installation or physical phones. Previously published Mac
and Android packages remain rollback artifacts.


## rc26 source failure diagnostics

Brian PR2's protocol journal cleanup requires a successful terminal Backup result
and the exact host-created empty-file identity; real source files stay intact.
The strict v1 contract is `docs/SOURCE-DIAGNOSTIC-CONTRACT-v1.json`. Current
failure metadata survives optional-source partial success and is hashed into both
transfer formats. Private bounded history survives helper/app relaunch in a
separate marker-owned diagnostics directory; it is labeled Previous run and
never added to a new capture or account Save.

The review and error disclosure reuse the existing Source details/technical
disclosure, Arial typography, Brand colors and panel primitives. Readable zero
remains zero. A1 support metadata derives the actual validated bundle build.
Native/source/package evidence does not establish a physical phone transfer or
Jeff's explicit account Save, report readback and return-login. The compatible
web receiver must be qualified before new native publication/feed promotion.
