# AMPLIFai phone collectors

The public `/phone` and `/phone/account` setup links offer the signed Android
`2026.10.01-rc1` APK and the signed, notarized, stapled Apple Silicon/macOS 14+
`2026.10.02-rc1` Mac owner-test release. The October 1 Android update retains
the September 28 rc2 signing certificate and Java behavior; it updates only
launcher artwork, version and source notice. The October 2 Mac release adds
disk-aware streaming beyond the former fixed backup-size ceilings, but it does
not contain this October 7 long-backup diagnostic repair. Both public assets
remain owner/partner tests, not universal physical-device acceptance. Exact
public source, notices, checksums and release
receipts are in `PROJECT_PLAN.md`; Android-specific behavior is in
[`android/README.md`](android/README.md). `/pilot` is a secondary manual-file
importer, not the normal direct-phone path. No Windows or Intel Mac release exists.

October 1 Windows software qualification used a standard public-repository
Windows Server 2025 x64 VM, Python 3.12.10 and the exact hash-pinned Windows
dependency lock, without cache/artifact storage or paid runners. Run
`36897819582` on companion-only source `4a9214e` installed those dependencies,
but Windows private-directory ACL creation was rejected; a cancellation
fixture then waited indefinitely for startup until the 15-minute job limit.
This is a failed qualification, not Windows companion/installer readiness.
Native Windows UI, safe cancellation, installer/signing, Apple USB prerequisites,
browser transfer and physical-device acceptance remain separate gates.

The October 7 long-backup diagnostic repair is a separate source-matched,
locally signed and notarized owner-test candidate, not a published replacement.
Its source/notices package and exact release artifacts must be reviewed and
offered together before the public pointer changes. Normal physical install,
Trust/permissions, history coverage, browser handoff and consented account save
remain separate operator acceptance gates.

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
Control chatter cannot extend the separate data-aware slow-progress budget,
which starts at four hours and extends by actual file bytes at a conservative
16 KiB/s floor;
it is not an absolute four-hour cutoff for a productive large transfer.
Selected input, discarded input, outbound files and parsing copies are streamed
in 128 KiB chunks. Selected writes reserve their future parsing copy, and every
write must preserve a further 2 GiB of real free space. There is no fixed 1 GiB
session or 128 MiB database/manifest limit, nor an unrelated-file-count cap.
Backup control/plist data retain a 16 MiB memory bound; metadata readers retain
the explicit 1,000,000-row/source and 25,000-selected-contact safety frontiers.
SQLite cursors avoid a second full row list; retained metadata records still
reside in memory, so this is not an unlimited-history or paged-review claim.
Low space, metadata/control capacity, unsupported format, unsafe ownership,
filesystem unavailability, device-response stall, sustained slow progress and
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
Jeff's owner-test acceptance now requires a fresh public download and normal
install; an already-local developer build is not sufficient. A valid Developer
ID identity and Apple-accepted previous candidate are recorded in
`PROJECT_PLAN.md`, but the changed collector source needs a new release
candidate. Do not publish it as a consumer download or bypass Gatekeeper.
The collection interface
is from [pymobiledevice3](https://github.com/doronz88/pymobiledevice3), its
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
