# AMPLIFai Phone macOS 2026.10.07-rc2 source

This branch carries the authored-source view for the Apple Silicon, macOS 14+
owner-test long-backup diagnostic and safe-report release. The matching complete source archive, including
143 upstream source archives and 278 notice files, is the designated source
offer beside the app and DMG at:
https://github.com/JmooreOsirs/amplifai-phone-companion/releases/tag/macos-2026.10.07-rc2

Use `AMPLIFai-Phone-macOS-Source-2026.10.07-rc2.tar.gz` and its checksums
from that release for the full offline package; upstream archives are not
duplicated into Git.
The October 7 rc1 update distinguished live backup finalization from a stalled
file frame and reported safe device/connection failure categories. This rc2
revision adds a user-controlled, pre-pair safe support code with a random
reference, static category/stage, elapsed time and byte counts. It does not
send phone content or diagnostic text from the native app. Apple accepted
notarization of the rc2 app and DMG. The partner's underlying 30-minute failure is not yet diagnosed;
normal fresh installation, physical iPhone completion, browser handoff and
account-save/retention remain separate owner-operated gates. This is not a
customer-ready or Windows release.

The project-authored companion is Copyright (C) 2026 OSIRS LLC and licensed
under **GPL-3.0-or-later**. See `COPYRIGHT` for the scope and warranty notice,
`LICENSE` for the GPLv3 text, and `THIRD-PARTY-NOTICES.txt` for retained upstream
notices. The separate private website/backend source is not part of this package.

This repository is source, not an installer. Its macOS files are projected from
the artifact-bound packet built from native source revision
`c074a93d371f541a692414fef8f49102bf2e24e2`. The complete release archive
contains the build inputs matched to the selected signed/stapled app inventory;
it does not claim a byte-for-byte reproducible native build or verified
operation on an owner phone.

## Complete source archive contents

- `source/`: project-authored Swift/Python companion, tests, exact dependency
  lock, logo and build/packaging scripts. This repository preserves earlier
  Android source; the complete Mac release archive excludes Android and the
  private account/web implementation.
- `third_party/source_archives/`: checksum-verified exact Python, native and
  Rust source archives. The complete candidate contains 143 archives. All 102
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
temporary-data handling. Normal fresh-install and device tests remain open
before any customer-ready claim.

## Source integrity

The 143-archive candidate includes the 102 Python sdists, CPython 3.12.12 and
its 20260203 standalone build recipe, OpenSSL 4.0.2 and 32 external Cargo
packages identified by cryptography, plus bzip2 1.0.8, Expat 2.6.3, mpdecimal
4.0.0, OpenSSL 3.5.5, SQLite 3.50.4 and xz 5.8.1 from the interpreter recipe.
The signed/stapled app inventory verifies 5,622 files and seven native paths.
The package contains all enumerated source inputs and 278 notice files;
it does not equate manifest coverage with legal certainty or a binary rebuild.
