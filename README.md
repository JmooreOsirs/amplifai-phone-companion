# AMPLIFai Phone companion source

**Mac and Android owner/partner test builds.** Normal fresh installation,
owner-operated phone collection and hosted-browser transfer still need
acceptance. These are not verified customer-ready downloads. Do not bypass
Gatekeeper, Play Protect or device permission prompts. Windows and Intel Mac
companions are not available.

- [Mac Apple Silicon/macOS 14+ rc5 prerelease](https://github.com/JmooreOsirs/amplifai-phone-companion/releases/tag/macos-2026.10.08-rc5): Apple-notarized iPhone companion with a distinct early host-space error, guarded selected-file writes and bounded automatic safe-code failure reporting. ACK2 remains available as rollback. A real owner iPhone and account-save run is still required.
- [Android 8+ rc4 prerelease](https://github.com/JmooreOsirs/amplifai-phone-companion/releases/tag/android-2026.10.08-rc4): release-signed owner/partner test APK for same-phone browser handoff. Contacts, calls and SMS have independent permission/coverage outcomes; MMS/RCS are not collected. Provider scan limits reject incomplete reads before review.
- Start through your [Week 1 account](https://amplifai-database-engine.vercel.app/phone/account), or use the separate [browser-only pilot](https://amplifai-database-engine.vercel.app/phone). The temporary app-generated pairing code is not a phone number or email verification code.

## Android source and build

`source/collector/android/` contains the complete project-authored Android app,
resources, JVM tests and pinned Gradle build configuration. Its README documents
the normal owner flow, permission boundaries, foreground handoff lifecycle and
remaining physical-device matrix. The Android release carries its matching
source archive, license/notices and checksums beside the APK. No private signing
key, password, account source or owner phone data is included.

Build with Android SDK 37, JDK 25 and the pinned Gradle wrapper. Developer build
tools are not required by installer users. Without release signing variables,
the generated release APK is unsigned and must not be distributed as installable.
The Android README describes operator-supplied signing and verification.

## Mac source package

The [versioned release assets](https://github.com/JmooreOsirs/amplifai-phone-companion/releases)
carry the complete corresponding-source archive, including the `third_party/`
files described below, beside the candidate binary. This repository contains
the companion-authored portion and retained notice aggregate; it is not the
private AMPLIFai website/backend repository.

The project-authored companion is Copyright (C) 2026 OSIRS LLC and licensed
under **GPL-3.0-or-later**. See `COPYRIGHT` for the scope and warranty notice,
`LICENSE` for the GPLv3 text, and `THIRD-PARTY-NOTICES.txt` for retained upstream
notices. The separate private website/backend source is not part of this package.

The Mac rc5 release is built from the updated Mac source in this tag. Its
complete corresponding-source ZIP beside the binary is authoritative for
upstream archives, notices and the exact selected inventory. This repository
also retains the separate Android project; the Mac ZIP does not include it.

This is a source release candidate for the macOS arm64 companion and its frozen
Python helper. It is not a signed app or installer. It includes the source and
build inputs matched to the selected artifact inventory; it does not claim a
byte-for-byte reproducible binary build or verified operation on an owner phone.

## Contents

- `source/`: project-authored Swift/Python companion, tests, exact dependency
  lock, logo and build/packaging scripts, plus the separate Android source folder.
  No private account/web implementation is included.
- `third_party/source_archives/`: checksum-verified exact Python, native and
  Rust source archives in the Mac release ZIP. The complete candidate contains 158 archives. All 102
  pinned Python build inputs remain available; the package matrix marks which
  distributions were observed in the frozen helper.
- `third_party/notices/`: byte-preserved upstream notices in the Mac release ZIP with per-file hashes.
  The readable aggregate retains both runtime and build-source evidence; it
  does not change the upstream license choices. Originals govern any character
  that could not be represented in the UTF-8 aggregate.
- `third_party/reconciliation.json`: selected app hashes, native source mapping,
  runtime hooks, cryptography SBOM/source closure and remaining validation gates.
- `third_party/package-file-manifest.json`: SHA-256 for every package file
  except the manifest itself. `bundle-scan-report.json` records the scoped
  publication-path and archive/notice integrity check.

## Build and test

Requires an Apple Silicon Mac, Xcode Command Line Tools (Swift), Python 3.11+
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
PYTHONPATH=collector uv run --with-requirements collector/portable-requirements.lock \
  --with pytest pytest -q collector/tests
```

Isolated builds create a separate local ad-hoc-signed development candidate
and preserve existing app copies. Building does not connect to a phone. The
native Connect action requires the owner's own USB/Trust/backup decisions;
refer to `source/collector/README.md` for retained-history limitations and
temporary-data handling. Public distribution still requires review of the exact
release artifact, signing/notarization and normal fresh-install/device tests.

## Source integrity

The 158-archive Mac candidate includes the 102 Python sdists, CPython 3.12.12 and
its 20260203 standalone build recipe, OpenSSL 4.0.2 and 32 external Cargo
packages identified by cryptography, plus bzip2 1.0.8, Expat 2.6.3, mpdecimal
4.0.0, OpenSSL 3.5.5, SQLite 3.50.4 and xz 5.8.1 from the interpreter recipe.
The selected app inventory records exact file hashes and seven native paths.
The Mac package contains all enumerated source inputs and 319 notice files;
it does not equate manifest coverage with legal certainty or a binary rebuild.
