# Desktop update contract

The released collector, review, and Save/ACK behavior remains the acceptance baseline. Updates replace app files in place and retain the existing app identity and data root. An update cannot install while collection, residue review, unsaved review, pairing, or Save/ACK is unresolved.

## Running-app status

The app owns an IPv4 loopback status server at `127.0.0.1:48752`, separate from the reviewed-data bridge at port 48751. Exactly one status server can own the port; a second app reports the conflict and cannot collect until the other app closes. Never take over a listener or delete another app.

Allowed origins are exactly `https://amplifai-database-engine.vercel.app` and `https://amplifai.satoris.ai`. Require exact Host `127.0.0.1:48752`, one Origin header, bounded requests, no redirects, `no-store`, and explicit local-network preflight. No account, phone, path, or review data is returned.

`GET /v1/status` returns at most 4096 bytes:

```json
{"schema":1,"product":"amplifai-phone","platform":"macos","architecture":"arm64","channel":"macos-arm64","bundleId":"ai.satoris.amplifai.phone.candidate","version":"2026.10.14-rc25","build":"26101425","protocol":1,"busy":false,"updateState":"current","availableVersion":null,"availableBuild":null,"updateCheckToken":"43-character-base64url-capability","tokenExpiresInSeconds":60}
```

Platforms: `macos`, `windows`. Architectures: `arm64`, `x86_64`. Channels: `macos-arm64`, `macos-intel-test`, `windows-x64-test`. Update states: `checking`, `current`, `available`, `deferred`, `installing`, `failed`, `manual`. Build is a digit string (1–12 digits); version is a bounded release label. The token is random, one-use, origin-bound, expires after 60 seconds, and does not authorize installation.

`POST /v1/check-update` accepts only `{"schema":1,"token":"..."}` (1024 bytes maximum). Consume a valid capability before action. Return `202 {"checking":true}` for an accepted native check, `409 {"deferred":true}` while busy, or `403 {}` for denied/expired/reused capability. The native wrapper rechecks its live safety gate before starting any update action. The website validates a strict schema and offers a native check or a CPU-matched official download; unreachable does not prove uninstalled.

The Mac frozen helper supports a distinct `companion-status` mode with fixed, non-secret identity arguments. It performs no collection or workspace read. It accepts bounded JSON status commands on stdin and emits only bounded JSON events on stdout. Closing stdin terminates the service. Windows uses the same status implementation in its wrapper process.

## Feeds and trust

- Apple silicon retains `https://amplifai-database-engine.vercel.app/phone/macos/appcast.xml`, bundle ID `ai.satoris.amplifai.phone.candidate`, and the existing EdDSA public key.
- Intel uses a separate signed `/phone/macos/intel-appcast.xml` feed and bundle ID `ai.satoris.amplifai.phone.intel-candidate`. The current feed-disabled Intel Test needs one matched manual bootstrap.
- Windows uses signed `/phone/windows/update-v1.json` plus detached base64 Ed25519 signature `/phone/windows/update-v1.json.sig`. The manifest contains schema, platform, architecture, channel, version, build, installerVersion, installerUrl, installerBytes, installerSha256, installerEdSignature, and installerTrust (`unsigned-test` or `authenticode`). Sign exact UTF-8 bytes and verify before parsing. Installer URL is an immutable asset below `https://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/`; HTTPS redirects are restricted to GitHub's release asset hosts. Verify bounded byte count, SHA-256, and Ed25519 signature before offering installation. The existing public key is `vtvaNfb6+dVMP4tz6PeLMXTeVQH7lK7o1zzy4WeqUGs=`.
- An unsigned Windows Test is never installed silently. Only a user-selected action may invoke the verified local MSI with the normal Windows Installer UI, after the app's live idle/no-unsaved-work gate passes. Keep stable per-user UpgradeCode and increment MSI version; do not remove session data or bypass Windows policy. Full unattended trusted installation is a later gate requiring qualified signing and real-PC evidence.

A missing, malformed, unsigned, wrong-channel, wrong-CPU, lower-build, timed-out, or mismatched update leaves the installed app usable and reports the failure. Release owner publishes package bytes before feed promotion and keeps the previous feed/package for rollback. The existing website owner alone publishes feed/static-site changes.
