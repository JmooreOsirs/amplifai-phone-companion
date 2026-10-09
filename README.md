# AMPLIFai Phone Windows x64 source qualification

**Current Mac and website Android downloads are published.** Normal fresh
installation, populated phone history and authenticated hosted Save/return still
need owner acceptance. Do not bypass Gatekeeper, Play Protect or device
permission prompts. Windows and Intel Mac companions are not customer downloads.

- [Mac Apple Silicon/macOS 14+ rc16](https://github.com/JmooreOsirs/amplifai-phone-companion/releases/tag/macos-2026.10.09-rc16-release): signed, notarized companion with its exact corresponding-source archive; it has not been installed over the owner's open review.
- [Website Android 8+ rc9](https://github.com/JmooreOsirs/amplifai-phone-companion/releases/tag/android-2026.10.09-rc9): release-signed APK with all-page contact review, explicit Android source permissions and server-confirmed account destination approval. The one-use code remains a recovery path. Normal installer and synthetic emulator checks do not prove populated physical call/SMS history or authenticated hosted account Save.
- Start through your [Week 1 account](https://amplifai-database-engine.vercel.app/phone/account), or use the separate [browser-only pilot](https://amplifai-database-engine.vercel.app/phone). The temporary app-generated pairing code is not a phone number or email verification code.

## Android source and build

`source/collector/android/` contains the complete project-authored Android app,
resources, JVM tests and pinned Gradle build configuration. Its README documents
the normal owner flow, permission boundaries, foreground handoff lifecycle and
remaining physical-device matrix. The Android release carries its matching
source archive, license/notices and checksums beside the APK. No private signing
key, password, account source or owner phone data is included.

This Windows candidate branch retains a historical Android source projection for
`2026.10.08-rc4` from native revision `90646e6`; the separate rc9 release and
`codex/android-rc9-oct9` branch are authoritative for current Android bytes.
The website APK is not Google Play-ready: this full-history build
requests restricted SMS and call-log permissions without default-handler status
or a reviewed Play exception. Ordinary download/install and physical-device
permission behavior remain owner-operated release gates; do not turn off device
protections to force source access. The signed APK and source archive are for
bounded owner/partner testing, not an all-user or customer-ready claim.

Build with Android SDK 37, JDK 25 and the pinned Gradle wrapper. Developer build
tools are not required by installer users. Without release signing variables,
the generated release APK is unsigned and must not be distributed as installable.
The Android README describes operator-supplied signing and verification.

## Windows x64 qualification source

This candidate branch also projects the current Python collection engine and
the Windows Tk wrapper under `source/collector/windows/`. The fixed package
builder at `source/script/build_windows_candidate.py` creates a disposable
dual-onedir candidate only on Windows x64/CPython 3.12. The branch's bounded
Windows runner checks private storage, synthetic protocol behavior, actual Tk
review and consent controls, frozen helper imports/empty residue, GUI startup,
and per-user WiX install/uninstall. It retains an **unsigned private draft MSI**,
not a public download. The rc13 draft is preserved as rollback while rc14
qualifies the narrow consent-row fit. No Windows signature, Apple USB/Trust run,
physical-device transfer or authenticated saved acknowledgment is implied.

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

This branch also carries historical macOS arm64 companion and frozen-helper
source; the current signed rc16 archive is in its own release. It includes the source and
build inputs matched to the selected artifact inventory; it does not claim a
byte-for-byte reproducible binary build or verified operation on an owner phone.

## Contents

- `source/`: project-authored Swift/Python companion, tests, exact dependency
  lock, logo and build/packaging scripts, plus the separate Android source folder.
  No private account/web implementation is included.
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

The 143-archive candidate includes the 102 Python sdists, CPython 3.12.12 and
its 20260203 standalone build recipe, OpenSSL 4.0.2 and 32 external Cargo
packages identified by cryptography, plus bzip2 1.0.8, Expat 2.6.3, mpdecimal
4.0.0, OpenSSL 3.5.5, SQLite 3.50.4 and xz 5.8.1 from the interpreter recipe.
The original app inventory verifies 5,615 files and seven native paths.
The package contains all enumerated source inputs and 278 notice files;
it does not equate manifest coverage with legal certainty or a binary rebuild.
