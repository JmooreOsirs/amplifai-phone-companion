# AMPLIFai Phone 2026.10.08-rc8 companion source

This package is prepared for the matching `2026.10.08-rc8` Mac app, build
`26100808`; publication and binary checksums are separate release steps. The
filtered receiver drains unselected full-backup bytes, while every retained
write and parsing copy checks real Mac storage with a further 2 GiB reserve.
It distinguishes an early host-space refusal from generic collection failure
and does not report a zero-byte preflight as backup file progress. A bounded
safe failure code can be sent automatically to the AMPLIFai diagnostics route
after fresh local approval, with visible delivery status. No backup files,
phone records, names, passwords, paths or raw exceptions are reported.
Synthetic and local build proof is not real phone or account-save proof.
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
  Rust source archives. The complete candidate contains 158 archives. All 102
  pinned Python build inputs remain available; the package matrix marks which
  distributions were observed in the frozen helper.
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

The 158-archive candidate includes the 102 Python sdists, CPython 3.12.12 and
its 20260203 standalone build recipe, OpenSSL 4.0.2 and 32 external Cargo
packages identified by cryptography, plus bzip2 1.0.8, Expat 2.6.3, mpdecimal
4.0.0, OpenSSL 3.5.5, SQLite 3.50.4 and xz 5.8.1 from the interpreter recipe.
The selected app inventory records its exact file hashes and seven native paths.
The package contains all enumerated source inputs and 319 notice files;
it does not equate manifest coverage with legal certainty or a binary rebuild.
