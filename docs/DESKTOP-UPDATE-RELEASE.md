# Desktop update release preparation

The release owner qualifies packages and corresponding source, publishes immutable package bytes, and then hands locally prepared feed files to the existing website owner. Feed preparation never publishes, deploys, installs, changes keys, or establishes physical-device acceptance.

## Trust and compatible feeds

| Channel | Feed | Bundle / installer | Planned build | CPU |
| --- | --- | --- | --- | --- |
| `macos-arm64` | `/phone/macos/appcast.xml` | `ai.satoris.amplifai.phone.candidate` | `26101527` / `2026.10.15-rc27` | `arm64` |
| `macos-intel-test` | `/phone/macos/intel-appcast.xml` | `ai.satoris.amplifai.phone.intel-candidate` | `261015261` / `2026.10.15-rc26-intel1` | `x86_64` |
| `windows-x64-test` | `/phone/windows/update-v1.json` + `.sig` | per-user MSI `1.0.19` | `26101519` / `2026.10.15-rc19` | `x86_64` |

Use the public key already embedded in the companion: `vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs=`. The existing Keychain account is `ai.satoris.amplifai.phone.candidate`; Sparkle's default `ed25519` account is absent. A harmless synthetic signature was verified independently against this public key. No product key was generated, exported, imported, or rotated.

The first ARM migration item **must omit `sparkle:channel`**. Released rc23/rc24 hosts use Sparkle's default channel and would not discover a named `macos-arm64` Sparkle channel. The feed retains signed `amplifai:channel=macos-arm64` provenance and `sparkle:hardwareRequirements=arm64`. Intel has both `sparkle:channel=macos-intel-test` and exact `x86_64` hardware provenance. Its feed-disabled predecessor needs one manual bootstrap. Both new Mac feeds specify `sparkle:minimumSystemVersion=14.0.0`.

Sparkle only interprets `arm64` as a hardware restriction; exact Intel CPU enforcement also lives in the native delegate and package validator. Feed preparation rejects universal or opposite-CPU main executables, wrong bundle identities, wrong feed URLs, wrong public keys, and missing signed-feed settings. Keep `SURequireSignedFeed=true`, `SUVerifyUpdateBeforeExtraction=true`, and `SUSignedFeedFailureExpirationInterval=0` in the actual archive.

## Pinned tools

The official [Sparkle 2.10.0 release](https://github.com/sparkle-project/Sparkle/releases/tag/2.10.0) tool archive has SHA-256 `c2bf58aa8387266ac179357b1415d6f2635f044da8be41042af32425dae6da0c` and 16,319,840 bytes. The pinned [Package.swift](https://github.com/sparkle-project/Sparkle/blob/2.10.0/Package.swift) independently identifies version 2.10.0 and its SPM artifact checksum `17e28312b8e18ab7cdbbe09a6fb28cc55a5479ec6c371dbc07cdecd2a14fd959`. Repository `Package.resolved` pins commit `eef1a539a373c1f1a320624b1130fc5de7b2e100`.

The verified local tool directory is:

```text
/Users/jeffmoore/Documents/🗣️ Voice Orchestrators/outputs/release-checks/2026-10-14/desktop-update-feeds/sparkle-2.10.0
```

The generator also checks the official `bin/sign_update` binary SHA-256 `43c249771bafc3aa581228abae00731a012d324691b8292860896635050be76b` before invoking it. The tool interfaces were checked with `--help` and pinned source. A harmless `generate_appcast` archive audit timed out after 120 seconds without output; separate binary Keychain consent is a possible cause, not a verified diagnosis. Preparation uses the [official manual signing route](https://sparkle-project.org/documentation/publishing/): validate the actual archive's metadata/CPU, author deterministic XML, then use `sign_update` for package and feed signatures.

`sign_update --account ai.satoris.amplifai.phone.candidate -p FILE` signs exact non-XML bytes and returns a public base64 signature. When `FILE` ends in `.xml`, the official tool writes Sparkle's embedded signature block; the release generator never constructs that block. `--disable-signing-warning` preserves deterministic authored XML bytes. A Keychain denial or timeout stops preparation and requires the human to resolve native consent; scripts do not automate it.

An existing OpenSSL with Ed25519 support performs independent verification using only a temporary public DER key. On this Mac `/opt/homebrew/bin/openssl` is available. This is release tooling, not a new companion runtime dependency.

## Exact release descriptor

Pass one JSON descriptor to `source/script/desktop_update_release.py`. Common fields are `schema`, `platform`, `architecture`, `channel`, `version`, `build`, `installerUrl`, `package`, `source`, `sourceBaseCommit`, and `baselineSourceArchiveSha256`. Mac adds `bundleId`. Windows adds `installerVersion` and `installerTrust=unsigned-test`.

`package` and `source` are each exact records containing **only** `name`, integer `bytes`, and lowercase `sha256`. Compute them from the completed local package and corresponding-source archive. `sourceBaseCommit` and `baselineSourceArchiveSha256` must match the channel's accepted baseline in `DESKTOP-UPDATE-SOURCE-PINS.json`; the current `source.sha256` binds the new corresponding-source bytes. Versions/builds must match the approved planned builds in that file and exceed the prior released build.

`installerUrl` must contain the immutable exact package filename below `https://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/`, without query, fragment, credentials, encoded path parts, or traversal. The release owner must qualify the source offer and actual MSI/App/DMG before creating the descriptor. Preparation authenticates and binds these byte records; it does not replace notarization, architecture closure, MSI inspection, or source-coverage qualification.

```sh
python3 source/script/desktop_update_release.py \
  --spec /absolute/path/to/qualified-descriptor.json \
  --package /absolute/path/to/qualified-package.dmg \
  --source /absolute/path/to/corresponding-source.zip \
  --sparkle-tools '/Users/jeffmoore/Documents/🗣️ Voice Orchestrators/outputs/release-checks/2026-10-14/desktop-update-feeds/sparkle-2.10.0' \
  --openssl /opt/homebrew/bin/openssl \
  --previous-feed /absolute/path/to/retained-appcast.xml \
  --output /absolute/path/to/new-feed-output
```

Use `.msi` and the Windows descriptor for Windows. Omit `--previous-feed` only when no previous feed exists. Output directories must be new. Inputs are read only; Mac DMGs are mounted read only without opening Finder and detached on exit.

## Output and rollback

Mac output contains the channel's `appcast.xml` or `intel-appcast.xml` and `release-receipt.json`. Windows output contains strict canonical UTF-8 `update-v1.json`, detached base64 `update-v1.json.sig`, and `release-receipt.json`. Windows signs exact manifest bytes before parsing, separately signs the MSI bytes, and verifies SHA-256 and size. A 64 KiB Mac feed cap, 16 KiB Windows manifest cap, 128-byte sidecar cap, and 256 MiB installer/package cap bound inputs. The generator's installer limit is deliberately within the native Windows 512 MiB ceiling. JSON rejects duplicate/unknown fields, non-finite values, wrong types, wrong channels/CPU, and nonincreasing app/MSI builds. The current unsigned Test generator cannot claim Authenticode trust.

When provided, the previous feed is copied byte for byte into `rollback/`; its hash and size are recorded. A previous Windows `.sig` is retained alongside it when present. Those are custody copies, not claims that the old feed was qualified. Keep the old immutable package/source assets and previous website deployment. To roll back, the website owner restores the exact retained feed and sidecar together or restores the prior deployment; do not reserialize or resign the retained feed, and do not overwrite immutable GitHub package assets. A feed rollback prevents future offers; it cannot downgrade an already updated app.

Before promotion, the release owner verifies the uploaded exact package/source bytes against the descriptor and receipt. The website owner deploys the exact output feed bytes and verifies public readback against `feedSha256`/`feedBytes`. Do not edit signed XML or JSON after preparation. Failure leaves the installed companion usable. An unsigned Windows Test requires a user-selected normal MSI UI install after the wrapper's live idle gate.

## Verification evidence and limits

`python3 -m unittest desktop_update_tests -v` passes 15 focused tests. They use only the publicly published RFC 8032 test seed in temporary files; they never access the product Keychain. Coverage includes exact-byte tampering, authentication before parsing, malformed/duplicate schemas, strict byte bounds, wrong CPU/channel/bundle/source pins, lower app/MSI builds, installer size/hash/signature mismatch, deterministic XML, signature block length/tail boundaries, bounded ZIP metadata, exact rollback custody, and immutable output protection.

`desktop_update_sparkle_probe.m` is a release-only executable that uses the actual pinned Sparkle parser and version comparator with old ARM host builds and default-channel settings. The feed must first pass independent signature verification. It reports parsed CPU/OS/build/default-channel eligibility. It does not launch the installed companion, download a package, or install an update. Actual old-host network discovery and native installation remain separate release acceptance evidence.

Receipts are under `outputs/release-checks/2026-10-14/desktop-update-feeds/` in Voice Orchestrators. `signer-availability.json` establishes public-key match. Files under `fixture-only/` are harmless synthetic fixtures and must never be published. Real candidate signing/notarization, signed uploaded byte checks, Intel/Windows hardware installation, and website publication belong to their existing owners.

This scoped Apex security/packaging pass adds no dependency, production key, secret, installed-app mutation, external release mutation, or second executor. Source and release bytes remain separate acceptance layers.
