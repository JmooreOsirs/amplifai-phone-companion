# AMPLIFai phone collectors

## Current public distribution — October 15, 2026

Use [the official phone setup](https://amplifai.satoris.ai/phone) to choose the
Mac installer for the chip shown in **Apple menu → About This Mac**:

- **Apple silicon:** [signed/notarized Mac rc26](https://github.com/JmooreOsirs/amplifai-phone-companion/releases/tag/macos-2026.10.15-rc26-release),
  build `26101526`; signed launch-time updates defer while local work is busy.
- **Intel:** [signed/notarized rc26-intel1 Intel Test](https://github.com/JmooreOsirs/amplifai-phone-companion/releases/tag/macos-2026.10.15-rc26-intel1-test),
  build `261015261`; distinct Test app and signed feed. The prior feed-disabled
  Intel Test requires a manual bootstrap. Physical acceptance remains open.
- **Android:** [signed rc10](https://github.com/JmooreOsirs/amplifai-phone-companion/releases/tag/android-2026.10.13-rc10).
- **Windows:** [rc19 unsigned x64 TEST](https://github.com/JmooreOsirs/amplifai-phone-companion/releases/tag/windows-2026.10.15-rc19-unsigned-test),
  MSI `1.0.19`; authenticated update manifest and installer bytes, explicit local
  approval, normal Installer UI. The MSI has no Authenticode signature. Prior
  rc17 requires a manual bootstrap.

Finish collection/review/Save before manual replacement; unfinished review does
not migrate. Older local Mac test builds need a manual official install. Run
one companion at a time. Each release has its own attached corresponding source
and checksums. This `main` branch contains the historical
rc6 Mac source snapshot, **not** the current app/helper source. macOS rc26 uses
source/build commit `257748fcb2d6aef758766314f4e3817c8d30c372`; Windows rc19
uses `d82bf65977a607026878019864e2e91a77bb5b1b`, which adds only a Windows
test-fixture repair with production files unchanged. Release receipts distinguish
later qualification-only commits. Published bytes and disposable runner upgrades
do not establish physical iPhone collection,
explicit account Save/ACK, report readback or return-login persistence.
Android-specific behavior is in [`android/README.md`](android/README.md);
`/pilot` remains a secondary manual-file importer.

## Historical source and verification notes

The following implementation notes describe the older Mac collector source in
this branch. Earlier rc4/rc5 and Windows qualification results are not current
download guidance.

The long-backup and saved-ACK repairs are present in the public Mac ACK2
owner-test package. Normal physical install,
Trust/permissions, history coverage, browser handoff and consented account save
remain separate operator acceptance gates.

The public `2026.10.08-rc5` Mac source addresses two observed
ACK2 failures at backup stage with zero received bytes. It separates logical
full-backup stream capacity from physical selected-file storage, keeps the real
2 GiB reserve on every retained write, identifies upstream host-space refusal,
and no longer labels a failed preflight as zero-byte backup progress. Its
fresh local collection disclosure covers automatic delivery of a bounded safe
error code to the AMPLIFai diagnostics endpoint; the app shows whether that
delivery succeeded. The rc5 public package was independently verified, but
synthetic coverage does not replace a normal
owner-operated iPhone retry. A subsequent build `26100804` owner run completed
the transfer but stopped in processing with `unsupported_schema`; the rc5
parser is unchanged from that build. The rc6 source snapshot narrows safe
failure categories and isolates a missing optional selected payload; that
source-level result alone did not establish an owner iPhone or account-save
result.

Build the host-specific macOS app without opening or connecting to a phone:

```sh
./script/build_and_run.sh --build-only
```

The result is `dist/AmplifaiPhone.app`. It is unsigned and relies on this
Mac's installed `uv` Python/package cache; copying it to another Mac is not
a supported installation. The app's **Connect iPhone** button is the only
collection trigger. It uses a connected USB iPhone first with the owner's
Apple Trust prompt. If no cable is present, it can use a phone this Mac already
exposes over an existing Wi-Fi pairing, without initiating new network pairing.
If USB Trust fails, it stops rather than silently switching to Wi-Fi.
The app provides local progress/cancel, contact selection, retained-history
metadata review, disconnect, and explicit inspection/cleanup of app-created
temporary data remaining after a crash. It does not save or upload the review.

For a **local-only arm64 packaging candidate** with an embedded Python runtime
instead of a runtime `uv` cache dependency:

```sh
./script/build_portable_candidate.sh --isolated
```

The isolated mode creates a new temporary output directory and preserves all
earlier candidates. The default command without `--isolated` still updates
`dist/AmplifaiPhonePortable.app`; use it only when that local target may be
replaced. Either mode runs read-only `inspect`
and `runtime-check` smoke checks from the frozen helper. This build script's
output is ad-hoc signed and is not a customer download. The existing published
Mac release has its own Developer ID, notarization, staple and source/notices
receipts; none cover new source. Packaging tools and dependencies are downloaded
only at build time.

The read-only release diagnostic can be run without an Apple login or phone:

```sh
bash script/companion_release_preflight.sh
```

It checks the outer app, frozen helper, and nested-library signatures,
hardened runtime and secure timestamps on the executable targets, locally
available code-signing identities, Gatekeeper assessment, and stapled ticket.
A failure keeps the candidate blocked; even a future all-pass result would not clear
the independent rights/notices, fresh-install, browser-transfer, or
owner-operated device gates. It does not sign, notarize, launch, or upload the
app and does not read keychain credentials.

After reviewing a selected set, the native window can explicitly open a
five-minute loopback pairing candidate. It binds only `127.0.0.1:48751`,
requires the exact pilot HTTPS Origin and Host, a one-time ten-digit code,
and a one-use bearer token. It hands off only selected contact names/phones
and matching call/message metadata; message bodies, source backups and
passwords never enter the bridge. Requests and payloads are bounded. The
source includes a publicly deployed owner-test `/phone` receiver. A synthetic
local HTTP browser run passed after `/phone` alone allowed the exact loopback
address in CSP. Hosted navigation and route security headers were verified,
but public HTTPS-to-loopback pairing and cross-browser local-network behavior
remain unverified. No helper starts collection or the bridge merely because a
site is visited.
An earlier USB charge or Trust tap alone does not prove Wi-Fi availability.
Apple documents a separate Finder **General → Show this iPhone when on Wi-Fi**
setting, enabled while the phone is connected by USB; this app does not change
that setting. See [Apple's instructions](https://support.apple.com/en-us/102471).
For a fresh owner, start with a data-capable cable, unlock/passcode and the
owner's Trust approval. Apple says encrypted local backups can include call
history that unencrypted backups omit; the collector only uses the existing
backup-encryption setting and asks for its password when needed. Apple also
excludes messages already synced to iCloud from computer backups, so no
multi-year message-coverage target can be guaranteed. See [encrypted-backup coverage](https://support.apple.com/en-ca/108353)
and [computer-backup exclusions](https://support.apple.com/en-au/108771).

From the repository root, with Python and `uv` installed:

```sh
PYTHONPATH=collector uvx --from pymobiledevice3==10.4.0 python -m amplifai_phone
```

The CLI asks the owner to connect one iPhone, approve Apple's Trust prompt if using USB,
and enter an **existing** encrypted-backup password only if the device requires
one. The password is collected interactively, never through argv. The code
requests only contacts, call-history, and SMS backup payloads; the first
filtered backup still transfers all backup bytes, then discards unselected
payloads. Selected source SQLite files can contain message bodies and other
private material, so they live only in private temporary directories. Queries
read only contact identity fields and call/message metadata. Temporary files
are deleted on normal completion, error, or interruption handled by Python;
an unclean process/OS crash can leave temporary material. The app detects
marked, abandoned sessions and requires an explicit cleanup action before
another collection. The helper itself makes no cloud upload. The browser can
save selected metadata only after separate per-source account consent.

Capacity-candidate recovery policy: received file or protocol bytes refresh a
15-minute inactivity watchdog, including discarded payload bytes and live
finalization responses with a flat percentage. During an active file frame,
control bytes cannot hide a 15-minute file stall. Between file frames,
control-only liveness is bounded to one hour since the last file bytes.
There is no total elapsed-time cutoff for an actively receiving file transfer.
An owner can cancel a slow transfer; the 15-minute no-byte and one-hour
control-only guards still stop a genuinely stalled session.
Selected input, discarded input, outbound files and parsing copies are streamed
in 128 KiB chunks. Selected writes reserve their future parsing copy, and every
write must preserve a further 2 GiB of real free space. There is no fixed 1 GiB
session or 128 MiB database/manifest limit, nor an unrelated-file-count cap.
Backup control/plist data retain a 16 MiB memory bound; metadata readers retain
the explicit 1,000,000-row/source and 25,000-selected-contact safety frontiers.
The 20 distinct phone/email values per contact contract also stops with an
explicit incomplete-source error instead of silently dropping later values.
SQLite cursors avoid a second full row list; retained metadata records still
reside in memory, so this is not an unlimited-history or paged-review claim.
Low space, metadata/control capacity, unsupported format, unsafe ownership,
filesystem unavailability, device-response stall and
incomplete cleanup have distinct safe errors. Cancellation and
owned-loop shutdown are bounded; incomplete cleanup retains a marked private
session and blocks another collection until explicit inspection/recovery.
These policies do not guarantee complete phone history or a five-to-ten-minute
run. Synthetic socket throughput is not a measured USB or phone speed.

The current terminal review lets an operator choose contacts and reports
matching retained available calls/messages without an arbitrary six-month cutoff.
The 50-contact/six-month figures are bounded owner-test targets, not production
limits. Technical source-row, payload and local-handoff bounds remain and can
stop a large history explicitly. A 32 MB paged local handoff and separately
consented 1,000-row cloud parts are implemented in working source, but the
cloud grouping and recovery/export/delete have synthetic software evidence.
Normal real-device and same-phone browser acceptance is still pending.
It excludes non-normalizable numbers, email-only iMessages, and group-chat
membership it cannot safely establish; counts are partial, not evidence of
zero interactions. Contacts DB property IDs, schemas, encrypted backups, USB
pairing, iOS versions, and Windows prerequisites still require physical-device
validation. No Android direct collection is in this candidate.

`pymobiledevice3`, `pyiosbackup`, and several transitive packages are GPL-family
dependencies. Published companion-only releases include corresponding source
and retained notices under the approved GPL-3.0-or-later boundary; the website
and backend repository remains private. Changed bundles need fresh exact-source,
notice and package reconciliation before distribution. This is not a general
legal-clearance claim. PyInstaller's
[special exception](https://pyinstaller.org/en/stable/license.html) does not
waive dependency obligations. The ordinary build-script output is ad-hoc signed only,
and the in-app browser has not proven the public HTTPS-to-loopback handoff;
its Local Network Access permission is still `prompt` on a separate test page.
See [ADR 0003](../docs/decisions/0003-local-phone-collection-candidate.md).
Owner-test acceptance requires a fresh public download and normal install; an
already-local developer build is insufficient. This historical checkout is not
the signed current installer or its corresponding source archive. Do not
publish its ad-hoc build as a consumer download or bypass Gatekeeper.
The collection interface is from
[pymobiledevice3](https://github.com/doronz88/pymobiledevice3), its
[backup2 CLI](https://doronz88.github.io/pymobiledevice3/cli/backup2/), and
the backup paths selected by its source. SQLite metadata columns are grounded
in [iLEAPP call](https://github.com/abrignoni/iLEAPP/blob/main/scripts/artifacts/callHistory.py)
and [message](https://github.com/abrignoni/iLEAPP/blob/main/scripts/artifacts/sms.py)
parsers and the [iQueryContacts AddressBook query](https://github.com/MetadataForensics/iQueryContacts/blob/main/AddressBook_Contacts.sql).

Synthetic verification:

```sh
PYTHONPATH=collector uvx --from pymobiledevice3==10.4.0 --with pyiosbackup python -m unittest discover -s collector/tests -v
uvx ruff check collector
sh scripts/test_macos_recovery.sh
```
