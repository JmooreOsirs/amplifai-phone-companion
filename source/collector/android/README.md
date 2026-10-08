# AMPLIFai Phone — Android

`2026.10.08-rc6` (`26100806`) streams each permission-gated source into a
short-lived, app-private SQLite review store, then prepares immutable selected
pages for the same-phone browser. The browser validates page hashes, source
counts and the manifest before a separately approved, per-account save. The
companion reports pages offered and waits for a matching saved/readback receipt.
Published rc5 remains the in-place-update rollback. Physical-device permission,
browser and authenticated account-save acceptance are still unverified.

Support-code wire format is `A1|build|reference|stage|category|elapsedSeconds|receivedBytes|retainedBytes|deviceStatus`.
For this Android release, `build` is the exact dotted version name
`2026.10.08-rc6`, bytes are `0|0` before pairing, and status is `-`. The
numeric Mac build values remain distinct and backward-compatible; the website
derives platform only from an explicit allowlist of exact build values.

Install rc6 as an in-place update, without uninstalling or clearing app data.
Updates retain the application
ID and signing certificate with a higher version code. After updating, open the
app, review the selected sources and approve a fresh one-use browser handoff.

## Owner flow

Use the signed APK's normal Android download/install flow. No developer mode, USB debugging, root, default SMS/dialer takeover, or security bypass is part of this product. If the installer or a restricted permission blocks progress, report that limitation; do not disable device protections.

1. Choose **Read contacts**, **Read call history**, and/or **Read SMS metadata** independently. Contacts are required for contact selection; calls and SMS are optional. Each source has its own Android consent request.
2. Select contacts, then **Review selected metadata**. Only selected contacts and matching available interaction metadata are eligible. Message bodies, attachments, MMS, and RCS are not collected.
3. Choose **Approve same-phone browser handoff**. On Android 13+, allow notifications and press Approve again. Disabled app/channel notifications block handoff because its visible Cancel control is required.
4. Use **Open AMPLIFai account in browser** on this same phone. Enter the **One-use pairing code** within five minutes. Each progressing page fetch, valid keepalive and completion extends the active browser session, bounded by inactivity and the visible Cancel action. Review each available source and approve its account save separately.
5. After delivery, browser-only users can **Cancel browser handoff**. Signed-in users separately save or decline each source, then use **Confirm saved sources and finish**. The browser verifies durable account readback before the matching saved assertion; the companion only reports a confirmed save after the final bound receipt. Cancelling cannot retract already saved account data.

For a blocked permission or pre-pair handoff, **Copy safe support code** gives
the owner an optional code to submit through AMPLIFai contact. It contains only
the build, a random short reference, fixed stage/category, elapsed seconds and
zero-byte/status placeholders. It does not contain phone numbers, contact names,
message content, account identity, pairing secrets or raw exception text.

## Permission and coverage boundaries

- `READ_SMS` and `READ_CALL_LOG` are hard-restricted permissions: an installer allowlist and an actual runtime grant are separate conditions. The official installer API defaults to allowlisting requested restricted permissions, but an OEM installer can apply different restrictions. A normal sideload can work; installation alone does not prove source access. [Permission reference](https://developer.android.com/reference/android/Manifest.permission#READ_SMS), [installer API](https://developer.android.com/reference/android/content/pm/PackageInstaller.SessionParams#setWhitelistedRestrictedPermissions(java.util.Set%3Cjava.lang.String%3E)).
- This website APK's normal installer path and Google Play eligibility are separate. Google's [SMS/call-log policy](https://support.google.com/googleplay/android-developer/answer/10208820?hl=en) requires a declared, reviewed permitted use or exception; this app does not force a default SMS or dialer role or claim a Play exception.
- Covered downloaded/local-file apps on Android 15/16 can also encounter Restricted Settings for the SMS permission group. This build does not guide users around it; unavailable SMS must leave contacts and permitted calls usable. [Android 16 security requirements](https://source.android.com/docs/compatibility/16/android-16-cdd#225_security_model).
- Grants are checked before provider queries, after permission results, before handoff, and when returning to the Activity. Revoked local sources are dropped and their handoff cancelled. Provider refusal is unavailable, not an empty successful read.
- A readable source with zero observed records remains available and distinct from denied/not-read history. `missingSources` identifies unavailable history; unavailable sources cannot export stale interactions. Available metadata is observed coverage, never proof of completeness.
- Provider scans stream into an atomic private store instead of stopping at the former 100,000/256,000 aggregate scan bounds. Contact phone values have no former 20-per-contact cap. Source counts and selected record counts must fit the versioned protocol's signed 32-bit count fields; a single selected record must fit a 128 KiB page. These are explicit errors, never silent truncation. Page count is driven by the selected records, not a fixed 32 MiB handoff cap. A manifest/control message remains bounded to 8,192 ASCII bytes, with sample labels omitted when too long while full contact names remain in record pages.

## Bounded handoff lifecycle

An unexported, owner-started `dataSync` foreground service serves immutable reviewed pages from app-private no-backup storage while the Activity yields to the same-phone browser. The service performs no provider queries. Leaving the Activity cancels unfinished provider reads; completion after cancellation is discarded. Transfer pages are deleted on handoff cancellation, replacement, completion or process restart. The local review store closes and is deleted when its Activity is destroyed; a process restart removes any orphaned review file before another read.

The bridge binds only `127.0.0.1:48751`, checks the exact Host and `https://amplifai-database-engine.vercel.app` Origin, limits pairing attempts, and requires the one-use bearer token and ordered pages. Pairing binds an immutable handoff ID and SHA-256 manifest. The code expires after five minutes without pairing. An authorized v2 session has a two-hour sliding inactivity window while the browser fetches, reviews, saves and verifies pages. A requested page is not proof the browser validated it; native `/v1/complete` records source delivery or explicit decline, and the separate bound saved/readback handshake confirms account persistence. Cancellation, five bad codes, saved confirmation, expiry or I/O failure closes the listener and clears the grant. OS termination can shorten the window; it never asserts a save from page delivery alone.

The notification contains no contacts or pairing code; it may show the count of pages offered. Disabling notifications, removing the app's task, or service timeout stops the transfer. Activity recreation does not own or close an active bridge. `START_NOT_STICKY`, no boot receiver, no persisted code/token and orphan scratch cleanup mean process loss cannot silently restart collection or transfer. The service requests only foreground-service/data-sync and notification permissions in addition to contacts/call/SMS/Internet permissions. [Foreground-service types](https://developer.android.com/develop/background-work/services/fgs/service-types), [notification permission](https://developer.android.com/develop/ui/compose/notifications/notification-permission).

## Local build and controlled signing

Use the installed Android SDK/JBR and existing Gradle dependencies. No signing values, keystore, owner data, or APK belongs in Git.

```sh
JAVA_HOME='/Applications/Android Studio.app/Contents/jbr/Contents/Home' \
ANDROID_HOME='/Users/jeffmoore/Library/Android/sdk' \
./gradlew --offline --no-daemon --no-configuration-cache \
  :app:testDebugUnitTest :app:lintRelease :app:assembleRelease
```

Run from `collector/android`. The current Android plugin exposes debug JVM tests, not a `testReleaseUnitTest` task; the same production sources are additionally compiled and linted by the release build.

This module contains only Java sources and explicitly sets `android.enableKotlin`
to false in its build configuration. AGP otherwise injects the Kotlin standard
library even without Kotlin source files. The module-level `enableKotlin = false`
setting removes that unused runtime and its annotations dependency; it does not
change Gradle's Kotlin build-script language or Android platform APIs. See
[AGP's documented Java-only module option](https://developer.android.com/build/migrate-to-built-in-kotlin).
The release runtime classpath must remain empty unless a future dependency is
deliberately introduced and its runtime/source notices are reviewed.

The authorized release operator supplies all four environment variables privately for that process:

- `AMPLIFAI_ANDROID_KEYSTORE_PATH`
- `AMPLIFAI_ANDROID_KEY_ALIAS`
- `AMPLIFAI_ANDROID_STORE_PASSWORD`
- `AMPLIFAI_ANDROID_KEY_PASSWORD`

Partial configuration is rejected. This Android project disables the configuration cache and rejects an explicit `--configuration-cache` before reading any signing values: even failed configuration can otherwise be cached. Use `--no-daemon --no-configuration-cache`; never echo credentials, include passwords in command arguments, or use a build scan. With no variables the release remains **unsigned** for local validation; never publish `app-release-unsigned.apk` as an installable download. With the complete approved configuration, verify `app/build/outputs/apk/release/app-release.apk` using the installed `apksigner`, confirm version/application ID/non-debuggable manifest and signer fingerprint, then record its SHA-256 and exact published URL in the canonical release ledger. Signing/publication are operator-owned, not implied by this README.

## Remaining physical acceptance

JVM socket tests and emulator runs do not establish physical-device acceptance. Use the actual published signed APK through the normal download/install path, with owner-approved synthetic test contacts/history where practical. Record device model, Android/OEM version, browser version, APK hash and per-source outcome; never capture bodies, credentials, or pairing tokens in evidence.

The API 36 emulator instrumentation covers the retained v1 pair/ACK contract and v2 private store, optional-source rollback, ordered pages, declined-source revocation, long active transfer, saved-ACK retries, 25,700 selected contacts over 257 pages, and 100,001 contact rows plus 256,001 matching call rows over 2,561 pages. This is Android runtime and local protocol evidence, not same-phone Chrome, cloud saving, normal installer permission or physical-device acceptance. Keep code/token and synthetic record identity out of logs.

- Samsung and Pixel: install without developer tools; confirm contacts-only, contacts+calls, and permitted SMS independently. Denied/installer-restricted sources must stay unavailable and must not block permitted sources. A genuinely empty readable history must say zero observed, not denied.
- Android 13+: deny notifications (no listener); allow and explicitly approve again (visible notification + Cancel). Disable app/channel notifications or dismiss the notification during transfer (stop). Confirm older API 26–28 two-argument and API 29+ typed foreground-service behavior when those devices are available.
- Switch app → same-phone Chrome and complete small and paged transfers; verify the account's separate consent/receipt flow. Browser local-network denial must remain an honest transport failure, not source-access denial or successful pairing.
- Recreate the Activity while ready; the existing code/session survives without re-collection. Cancel from the notification, finish the Activity, remove the app task, force process death, and wait past five minutes before initial pairing: no new read, replay, silent restart, or stale usable code. Once paired, verify progress and keepalive extend only the active session's two-hour inactivity deadline, including during account save/readback. Check expiry, cancellation and notification-loss behavior separately.
- Revoke a previously read permission, return, and confirm that source is dropped and an existing handoff is cancelled before a new review. Simulate cancellation during a slow provider query: no completed source is retained.
- Confirm exact Host/Origin rejection, bad-code exhaustion, token replay rejection, ordered-page retry, one-use delivery, wrong and correct saved acknowledgements, and listener closure only after acknowledgement/cancel/expiry. JVM tests cover the local protocol; these do not establish browser/OEM interoperability.

Record actual physical permission and account results separately from local checks. Never infer complete history from a readable provider or infer a saved account from page delivery.
