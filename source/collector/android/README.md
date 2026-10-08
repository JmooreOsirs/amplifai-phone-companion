# AMPLIFai Phone — Android owner/partner test

`2026.10.08-rc4` (`26100804`) is an owner-test source candidate. Relative to
the signed October 7 rc3, it copies the exact support-code wire value and
requires a fresh, unchecked per-source approval before each Android provider
read, even when an OS permission was previously granted. The payload-bound
saved-account acknowledgement and five-minute handoff limit remain. October 7
rc3 is the in-place-update rollback. This candidate still needs a signed build,
normal published-download and physical-device acceptance. SMS/call coverage
remains conditional on ordinary installer and device permissions.

Support-code wire format is `A1|build|reference|stage|category|elapsedSeconds|receivedBytes|retainedBytes|deviceStatus`.
For this Android release, `build` is the exact dotted version name
`2026.10.08-rc4`, bytes are `0|0` before pairing, and status is `-`. The
numeric Mac build values remain distinct and backward-compatible; the website
derives platform only from an explicit allowlist of exact build values.

The published September 28 rc2 fixes an Android-only pairing crash in that day's
rc1. Preserve rc2 and October 7 rc3 as recovery history; never recommend the
broken September 28 rc1. Install a future qualified candidate as an in-place
update, without uninstalling or clearing app data. Updates retain the application
ID and signing certificate with a higher version code. After updating, open the
app, review the selected sources and approve a fresh one-use browser handoff.

## Owner flow

Use the signed APK's normal Android download/install flow. No developer mode, USB debugging, root, default SMS/dialer takeover, or security bypass is part of this product. If the installer or a restricted permission blocks progress, report that limitation; do not disable device protections.

1. Choose **Read contacts**, **Read call history**, and/or **Read SMS metadata** independently. Contacts are required for contact selection; calls and SMS are optional. Each source has its own Android consent request.
2. Select contacts, then **Review selected metadata**. Only selected contacts and matching available interaction metadata are eligible. Message bodies, attachments, MMS, and RCS are not collected.
3. Choose **Approve same-phone browser handoff**. On Android 13+, allow notifications and press Approve again. Disabled app/channel notifications block handoff because its visible Cancel control is required.
4. Use **Open AMPLIFai account in browser** on this same phone. Enter the **One-use pairing code** within five minutes. Review each browser source and approve its account save separately. This Android app makes no cloud upload.
5. After delivery, browser-only users can **Cancel browser handoff**. Signed-in users separately save or decline each source, then use **Confirm saved sources and finish**; only a matching browser acknowledgement closes the companion as saved. Cancelling or notification dismissal cannot retract metadata already delivered or separately saved.

For a blocked permission or pre-pair handoff, **Copy safe support code** gives
the owner an optional code to submit through AMPLIFai contact. It contains only
the build, a random short reference, fixed stage/category, elapsed seconds and
zero-byte/status placeholders. It does not contain phone numbers, contact names,
message content, account identity, pairing secrets or raw exception text.

## Permission and coverage boundaries

- `READ_SMS` and `READ_CALL_LOG` are hard-restricted permissions: an installer allowlist and an actual runtime grant are separate conditions. The official installer API defaults to allowlisting requested restricted permissions, but an OEM installer can apply different restrictions. A normal sideload can work; installation alone does not prove source access. [Permission reference](https://developer.android.com/reference/android/Manifest.permission#READ_SMS), [installer API](https://developer.android.com/reference/android/content/pm/PackageInstaller.SessionParams#setWhitelistedRestrictedPermissions(java.util.Set%3Cjava.lang.String%3E)).
- Google Play is the normal distribution path for most users, but this exact full-history APK is not Play-ready: it is not a default SMS/Phone/Assistant handler, and Google's [current SMS/call-log policy](https://support.google.com/googleplay/android-developer/answer/10208820?hl=en) requires a declared, reviewed permitted use or exception. The enterprise CRM exception lists call-log permissions for CRM, not `READ_SMS`; neither a signed APK nor successful sideload establishes Play eligibility. Keep the broad-release path and any reduced-permission variant separate from this owner-test artifact.
- Covered downloaded/local-file apps on Android 15/16 can also encounter Restricted Settings for the SMS permission group. This build does not guide users around it; unavailable SMS must leave contacts and permitted calls usable. [Android 16 security requirements](https://source.android.com/docs/compatibility/16/android-16-cdd#225_security_model).
- Grants are checked before provider queries, after permission results, before handoff, and when returning to the Activity. Revoked local sources are dropped and their handoff cancelled. Provider refusal is unavailable, not an empty successful read.
- A readable source with zero observed records remains available and distinct from denied/not-read history. `missingSources` identifies unavailable history; unavailable sources cannot export stale interactions. Available metadata is observed coverage, never proof of completeness.
- Provider scans stop before a partial result can be reviewed when they exceed 100,000 contact-phone rows or 256,000 call/SMS rows per source. More than 20 distinct normalized phones on one contact also stops the source rather than silently dropping a value. A 32 MB selected-metadata handoff bound can reject an unusually large review; reducing the selected contacts is the only supported retry. None of these conditions silently exports incomplete history.

## Bounded handoff lifecycle

An unexported, owner-started `dataSync` foreground service holds only a reviewed in-memory payload while the Activity yields to the same-phone browser. The service performs no provider queries. Leaving the Activity cancels unfinished provider reads; completion after cancellation is discarded.

The bridge binds only `127.0.0.1:48751`, checks the exact Host and `https://amplifai-database-engine.vercel.app` Origin, limits pairing attempts, and requires the one-use bearer token and ordered page contract. Pairing binds an immutable handoff ID and SHA-256 of the reviewed payload. Delivery consumes the metadata read and clears its payload buffer, but keeps the foreground service and listener alive for a matching, token-authenticated saved acknowledgement. Cancellation, five bad codes, saved acknowledgement, expiry or I/O failure closes the listener/active socket and clears the grant. A monotonic clock includes device sleep. The service removes its notification and stops; five minutes is an upper bound for useful authorization, not a guaranteed browser-transfer window under OS termination.

The notification contains no contacts or pairing code. Disabling notifications, removing the app's task, or service timeout stops the transfer. Activity recreation does not own or close an active bridge. `START_NOT_STICKY`, no boot receiver, and no persisted payload/code mean process loss cannot silently restart collection or transfer. The service requests only foreground-service/data-sync and notification permissions in addition to the existing contacts/call/SMS/Internet permissions. [Foreground-service types](https://developer.android.com/develop/background-work/services/fgs/service-types), [notification permission](https://developer.android.com/develop/ui/compose/notifications/notification-permission).

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

Pairing release regression: a prior Android rc1 crashed its first valid `POST /v1/pair` on the Pixel 8 emulator with `PatternSyntaxException`; September 28 rc2 repaired it. The formerly incomplete official ARM64 Android 37.2 image was installed on October 7. On the API 37 `sdk_gphone16k_arm64` emulator, the debug APK launched, the ordinary contacts and notification prompts were approved, one synthetic contact was read/selected/reviewed, and the visible foreground handoff started. An Android instrumentation run passed pairing, delivery, wrong-ack rejection, saved acknowledgement and listener closure. Separately, a loopback API probe against the actual foreground service matched the received payload hash, saw the Activity's received state, rejected a wrong digest, accepted the matching acknowledgment and saw the terminal UI state. This is Android OS/runtime evidence, not same-phone Chrome, cloud-save, signed installer, OEM or physical-device acceptance. Host-JVM tests alone could not catch the original regex crash. Keep code/token and synthetic contact identity out of logs.

- Samsung and Pixel: install without developer tools; confirm contacts-only, contacts+calls, and permitted SMS independently. Denied/installer-restricted sources must stay unavailable and must not block permitted sources. A genuinely empty readable history must say zero observed, not denied.
- Android 13+: deny notifications (no listener); allow and explicitly approve again (visible notification + Cancel). Disable app/channel notifications or dismiss the notification during transfer (stop). Confirm older API 26–28 two-argument and API 29+ typed foreground-service behavior when those devices are available.
- Switch app → same-phone Chrome and complete small and paged transfers; verify the account's separate consent/receipt flow. Browser local-network denial must remain an honest transport failure, not source-access denial or successful pairing.
- Recreate the Activity while ready; the existing code/session survives without re-collection. Cancel from the notification, finish the Activity, remove the app task, force process death, and wait past five minutes including screen-off time: no new read, replay, silent restart, or stale usable code.
- Revoke a previously read permission, return, and confirm that source is dropped and an existing handoff is cancelled before a new review. Simulate cancellation during a slow provider query: no completed source is retained.
- Confirm exact Host/Origin rejection, bad-code exhaustion, token replay rejection, ordered-page retry, one-use delivery, wrong and correct saved acknowledgements, and listener closure only after acknowledgement/cancel/expiry. JVM tests cover the local protocol; these do not establish browser/OEM interoperability.

Until those checks pass, describe the release only as an owner/partner **test build** with source coverage conditional on the device's normal permission rules.
