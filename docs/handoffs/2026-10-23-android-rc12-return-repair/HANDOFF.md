# Android RC12 transfer and return fixes

These changes repair four native defects found while testing Android RC12 against the canonical account website. They do not change the website, database, collection permissions, release admission, or source-save authorization.

Base: canonical Android RC12 source `cccef9fe593d5ab72e36c648596a3ff617e876c2`, version `2026.10.23-rc12`, build `26102312`. Website contract inspected: `ad3c3e0`.

## Changes to apply

1. **Return a valid pairing lifetime.** `LocalBridge.pairResponse` caps the advertised pairing lifetime at 300 seconds. Account release had renewed the separate review window to two hours before returning the response, causing the website's existing schema to reject it. The review window, token, exact release binding, and retry validation are unchanged.
2. **Use the server's claim and consume order.** `NativeRunAuthority` first claims the connect intent, then verifies its exact run, approval, account, course, and source scope. It caches only that verified intent/capability in memory. After browser release, consume uses the server's existing checked-consume endpoint; it does not rerun a pending-intent check that deliberately rejects released intents. Expiry, revocation, exact binding, and lost-response retries remain enforced.
3. **Return to the specific collection.** `NativeAccountReturn` adds the existing non-secret `phoneRunId` to the reviewed-return URL. The website already supports this route and authorizes the exact run/enrollment before discovery. Distinct collections no longer reopen an identical legacy continuation URL. Control and completed-report return URLs are unchanged.
4. **Keep confirmed completion visible after returning from the browser.** `NativeCompletionState`, `HandoffService`, and `MainActivity` retain only non-secret completed-report routing context in process memory. Recording requires the existing exact saved-acknowledgement completion path and successful cleanup. A new setup/read/handoff clears it; attempt generations reject stale callbacks. Restoring this display never restores collection authority, source data, or permissions. Process death loses the display hint; the authenticated report remains authoritative.

## Verification

Local candidate 4:

- `testWebsiteDebugUnitTest`, `lintWebsiteDebug`, and `assembleWebsiteDebug` passed.
- **68 unit tests across 13 suites; zero failures, errors, or skips.**
- Lint: zero errors, 118 warnings (117 existing and one untranslated display-copy warning).
- Pairing-expiry, native lifecycle-ordering, and return-route regressions fail against the prior implementations and pass with their repairs. Five completion-state tests cover completion/cleanup gates, mismatched context, new attempts, stale callbacks, and absence of persisted authority.
- Independent source reviews found no blocking issues. Unit tests model completion-state recovery; live Android service recreation is a separate check.

Build command from `source/collector/android`, with the installed JDK and Android SDK configured:

```sh
./gradlew --no-daemon --no-configuration-cache testWebsiteDebugUnitTest lintWebsiteDebug assembleWebsiteDebug
```

Candidate 4 APK SHA-256: `a530181c82619f6feb3966ade285a89878cde20244e7f15326b80aa1ec97cf3a`.

This is a locally debug-signed test build, visibly labeled **AMPLIFai RC12 test fix**. Package/version metadata remained RC12 for the current server admission check. The cosmetic debug resource and APK are excluded from this production patch. This hash does not identify an official release.

## BrowserStack evidence and remaining checks

Tests used a BrowserStack Pixel 8 Pro running Android 14 and Firefox on that same phone. Ordinary Android call-log and SMS permissions succeeded. A separate disposable-device helper inserted fictional provider records; it did not place calls, send messages, or use a network. Its temporary default-SMS role was restored to Messages. Repeating the fixture insertion added zero duplicate records.

The app read six calls and four inbox/sent SMS records. Android's non-default SMS provider view excluded the fixture draft before the app read it. Selecting one fixture contact retained exactly **one contact, three calls, and two SMS records**; the other fixture contact's history was excluded.

Candidate 2 completed the normal claim → verify → release → consume sequence, all three source saves, and the server phone ACK. The report showed 1/3/2 and persisted. Returning to Firefox initially required a refresh; that led to fix 3. Returning to the native app after success showed stale Ready/not-current text; that led to fix 4.

Candidate 3 used the new pending-run return route without a refresh. Its first attempt showed a generic local-transfer failure after hosted intent creation and before a hosted claim. One normal **Check now** retry completed the report with 1/3/2 without a refresh or phone reread. This is not a clean first-attempt result; no speculative protocol change was made for that transient failure.

**Candidate 4 passed the normal first-attempt flow** with **70 contacts, six calls and four SMS records**. Continue returned automatically to Firefox; all three source saves returned 201 and server phone ACK returned 200 at October 24 02:52:06.401 UTC. No refresh, Check now, or repeated collection was required. Returning to the native app around 02:55 UTC displayed **Account save confirmed / Confirmed** and **Open saved account and report**, verifying the actual post-save service/activity lifecycle. Opening **Open saved account and report** and then refreshing Firefox preserved the completed report and the same **70/6/4** counts. The two page loads returned report/source GET 200 and normal completion POST 200, with no additional source-import POST, connect intent, or phone ACK. This verifies report reopening and reload without re-import or increased counts; it does not claim sign-out/sign-in persistence or repeated-import deduplication. Local evidence includes screenshots 39–42 and the candidate 4 request log.

BrowserStack's Chrome routing issue is separate: same-device loopback was intercepted by its infrastructure proxy even with network tracking disabled. Firefox reached the phone's local service. These tests do not prove BrowserStack Chrome compatibility, actual carrier calls/SMS, MMS/RCS extraction, other Android versions, or a production-signed release.

## Release handoff

Apply the focused changes to the canonical RC12 source, run the checks, and complete the remaining live check above. Build and sign the next Android release using the existing official signing identity and coordinated release-admission metadata. Do not publish the debug APK or copy its debug-only launcher label into the production release. No database migration or authorization bypass is required.
