# AMPLIFai Phone 2026.10.12-rc23 companion source

This package is prepared for the matching `2026.10.12-rc23` Mac app, build
`26101223`; publication and binary checksums are separate release steps. The
setup now shows one active Prepare, Consent, or Connect step with Finder and
technical help on demand. An unencrypted backup is accepted for review and
its verified Calls file is parsed when present; an absent file remains
Unavailable and no historical-completeness claim follows. The
new precollection guide reads the existing local-backup encryption setting
without starting a backup. Only a literal true result unlocks the owner’s
encrypted-backup choice. Off and unknown have separate explanations. Finder
setup remains owner-controlled: enabling encryption can start a full backup,
overwrite/encrypt older local backups, and require Mac storage beyond this
app’s 2 GiB reserve. The owner keeps the password and enters it locally if
asked. This app does not turn encryption on or guarantee historical Calls.
The owner may continue with potentially unavailable Calls. An encrypted-choice
collection rechecks the setting before receiving backup bytes and checks the
completed Manifest before review. An unavailable source is not measured zero.
The review can atomically select or clear all contacts across the immutable
paged list. Connection to a signed-in destination uses a five-minute one-use
server intent and an explicit Mac approval of the server-confirmed account and
course; the temporary code and Copy control remain as recovery. Neither
connection nor preview delivery saves a source to the account. This revision
separates Main and Review contacts with stable-ID, all-page selection. Review
is initially excluded; the owner can include specific contacts and undo a
local suggestion-based change. Save and continue holds the exact review while
opening account Connection, where source saving remains a separate decision.
It also carries typed, digest-bound optional-source unavailability and captured
backup-encryption evidence, without reinterpreting earlier missing-only runs.
The filtered receiver drains unselected full-backup bytes, while every retained
write and parsing copy checks real Mac storage with a further 2 GiB reserve.
It distinguishes an early host-space refusal from generic collection failure
and does not report a zero-byte preflight as backup file progress. A bounded
safe failure code can be sent automatically to the AMPLIFai diagnostics route
after fresh local approval, with visible delivery status. No backup files,
phone records, names, passwords, paths or raw exceptions are reported.
Synthetic and local build proof is not real phone or account-save proof.
When an unencrypted selected Contacts or Messages SQLite main file is smaller
than its manifest declaration, this revision accepts it only if the canonical
manifest entry supplies an independent whole-file producer digest, the copied
file matches that digest, no active or missing listed SQLite sidecar can be
ignored, and a private read-only worker validates the parser-consumed schema
and SQLite structure. Contacts validation explicitly excludes unused Apple
search caches that require a device-only tokenizer; it does not claim whole
database integrity. Invalid digests, missing evidence, active sidecars, or
unsupported canonical tables still fail closed. Encrypted backups and files
with exact manifest lengths keep their existing validation path. For an encrypted
main file whose decrypted SQLite bytes are smaller than its declaration, this build
requires the exact consumed ciphertext to match an unambiguous producer digest,
an intact copied file, absent WAL/SHM/journal sidecars, and source-specific
read-only SQLite validation. Missing or conflicting evidence still rejects the
source. Named integer primary keys in Contacts and Calls now retain their
original row identities. A failed selected-file length/copy check can show only
bounded source, component, byte-count and proof statuses in local copyable error
details; no phone records, paths, passwords, keys or digest values are exported.
This synthetic source qualification does not identify the cause of an earlier
private photographed failure or establish a real-phone call-history result.
This revision treats a missing selected optional call or SMS database as an
explicit unavailable source rather than discarding otherwise valid captured
sources. A missing contacts database and any invalid selected-file size,
identity, or decryption still stop processing. The app reports bounded distinct
processing codes for malformed backup controls, selected payload failures, and
unsupported contacts schema. A later owner rc6 run stopped with a generic
`source_capacity_limit` after 21.73 GB received; no private trigger subtype
was available. This revision streams larger DeviceLink control frames to a
private temporary file, reads larger on-disk control plists without duplicating
their raw bytes, drains private device error text in bounded chunks, and emits
distinct safe capacity codes if a guard still stops collection. It does not
prove the owner's exact failure is fixed. This revision streams sanitized
contacts and interactions into a private SQLite review store, pages contact
selection and browser transfer, and retains all valid contact values and group
participants that fit an individual bounded page. A failed optional source
read removes its partial rows before that category is marked unavailable.
The collector keeps an actively receiving backup alive without a total
elapsed-time cutoff. Real disk checks and finite page/request bounds remain.
The current local bridge also reports observed source rows separately from
selected rows and keeps the Mac review open until the browser confirms receipt
of its account-save acknowledgment. These safeguards do not prove a real
device-to-account save. Page delivery alone does not mean account persistence;
the matching website validates page hashes and durable account readback.
The retained rc20 source binds the Mac destination prompt to the exact displayed
one-use intent, handoff, server-confirmed account and course. A later request
cannot retarget an already visible approval; the next prompt waits for actual
sheet dismissal. A retained reviewed import can create a new local pairing
without another phone collection. Direct selection edits and review replacement
are locked while its current browser handoff is live. Change selected contacts
first asks the helper to close that link; if an account-save receipt has arrived,
atomic refusal preserves the old reviewed selection and final acknowledgment
endpoint. The browser still requires separate source approvals, durable account
readback and a bound final acknowledgment. These controls are for newly installed
rc20, rc21, rc22 and rc23; an open review cannot be moved into a new version by restarting it.
The Mac review offers optional local name-based contact suggestions, with
deliberate group deselection and generation-bound Undo; no source contact is
automatically excluded or changed. This build also carries only fixed, safe
optional Call/SMS unavailability reasons from the selected-file validator to
the local review and bound browser preview. It does not identify the cause of
an earlier private run, restore missing history, or relax validation.
This Mac revision keeps pinned Sparkle 2.10.0 but distinguishes disabled
automatic checks from an attempted feed failure. App-updates controls stay out
of collection, parsing, browser delivery and unsaved review; idle retains the
version and signed manual-download guide. A failed or unavailable feed stays
collapsed with a manual-install fallback. No update can install or restart
the app during those unsafe phases. The public signed appcast remains
unavailable, so this release makes no in-app update-success claim. A future
feed and archive must match the publisher key before in-app updating is offered.

The project-authored companion is Copyright (C) 2026 OSIRS LLC and licensed
under **GPL-3.0-or-later**. See `COPYRIGHT` for the scope and warranty notice,
`LICENSE` for the GPLv3 text, and `THIRD-PARTY-NOTICES.txt` for retained upstream
notices. The separate private website/backend source is not part of this package.

This is a source release candidate for the macOS arm64 companion and its frozen
Python helper. It is not a signed app or installer. It includes the source and
build inputs matched to the selected artifact inventory; it does not claim a
byte-for-byte reproducible binary build or verified operation on an owner phone.

## Contents

- `source/`: project-authored Swift/Python companion, tests, exact dependency
  lock, logo and build/packaging scripts. No Android app or private account/web
  implementation is included.
- `third_party/source_archives/`: checksum-verified exact Python, native and
  Rust source archives. The complete candidate contains 159 archives. All 102
  pinned Python build inputs remain available; the package matrix marks which
  distributions were observed in the frozen helper. The pinned Sparkle source
  archive and its complete upstream license are also retained.
- `third_party/notices/`: byte-preserved upstream notices with per-file hashes.
  The readable aggregate retains both runtime and build-source evidence; it
  does not change the upstream license choices. Originals govern any character
  that could not be represented in the UTF-8 aggregate.
- `third_party/reconciliation.json`: selected app hashes, native source mapping,
  runtime hooks, cryptography SBOM/source closure and remaining validation gates.
- `third_party/package-file-manifest.json`: SHA-256 for every package file
  except the manifest itself. `bundle-scan-report.json` records the scoped
  publication-path and archive/notice integrity check.

## Build and test

Requires an Apple Silicon Mac, Xcode Command Line Tools (Swift), Python 3.12+
for the audit tools, and `uv`. Build tools are developer prerequisites, not
requirements for eventual installer users. The portable helper uses Python
3.12 and the exact hash-pinned lock. Upstream source archives, Cargo.lock,
OpenSSL SBOM build flags and the standalone CPython build/patch recipe are
retained for source inspection or rebuilding dependency wheels/interpreter.
The ordinary app build consumes hash-pinned wheels; it does not rebuild those
native dependencies from source.

```sh
cd source
./script/build_portable_candidate.sh --isolated
PYTHONPATH=collector:collector/tests uv run --python 3.12 \
  --with-requirements collector/portable-requirements.lock \
  python -m unittest discover -s collector/tests
```

Isolated builds create a separate local ad-hoc-signed development candidate
and preserve existing app copies. Building does not connect to a phone. The
native Connect action requires the owner's own USB/Trust/backup decisions;
refer to `source/collector/README.md` for retained-history limitations and
temporary-data handling. Public distribution still requires source-to-binary
review, a verified signed/stapled release artifact and normal fresh-install/
device tests.

## Source integrity

The 159-archive candidate includes the 102 Python sdists, CPython 3.12.12 and
its 20260203 standalone build recipe, OpenSSL 4.0.2 and 32 external Cargo
packages identified by cryptography, plus bzip2 1.0.8, Expat 2.6.3, mpdecimal
4.0.0, OpenSSL 3.5.5, SQLite 3.50.4 and xz 5.8.1 from the interpreter recipe.
The selected app inventory records its exact file hashes and seven native paths.
The package contains all enumerated source inputs and 320 notice files;
it does not equate manifest coverage with legal certainty or a binary rebuild.
