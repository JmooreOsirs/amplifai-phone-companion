# AMPLIFai Phone — Android

`2026.10.21-rc11` (`26102111`) adds account-bound native approval, a separate
Start action, contact selection and Review, and deliberate Continue. The
selected transfer stays immutable across retry, and the app reports delivery
separately from a matching saved-account receipt. See
[Native setup contract implementation](#native-setup-contract-implementation).
Website admission of this exact version is activated separately after package
qualification and public byte readback.

Each permission-gated source streams into a short-lived app-private SQLite
review store. The browser validates page hashes, source counts and the manifest
before account persistence under the approved run. Source cards distinguish a
readable zero from not read or unavailable. Measured row counts, elapsed time and
last movement remain distinct from estimates. Notification drawer visibility is
optional; the required foreground-service notification and in-app Cancel remain.
The existing manual reviewed pairing path is retained.

Support-code wire format is
`A1|build|reference|stage|category|elapsedSeconds|receivedBytes|retainedBytes|deviceStatus`.
This release uses exact version name `2026.10.21-rc11`; before pairing its bytes
are `0|0` and status is `-`. Website build admission is explicit.

Install RC11 over RC10 without uninstalling or clearing app data. The application
ID and publisher certificate are unchanged and the version code increases.
Android will not install an older version over a newer one through a normal
update. Physical-device permission, browser, normal installation and account-save
acceptance remain separate from synthetic emulator and protocol qualification.

## Website app updates

At launch, the website-distributed app performs one bounded anonymous HTTPS
request for `/phone/android-update-v1.txt`. The exact-order feed is signed by
the existing RSA-3072 Android release identity whose public certificate is
pinned in the app. It names a single newer package version, exact signed APK
URL, byte length, SHA-256 and signer. The app downloads only that allowlisted
APK into private no-backup storage, rejects unexpected redirects, partial or
altered bytes, wrong package/version/SDK and wrong signing certificate, then
lets Android verify and apply the same-signer update through PackageInstaller.
The request includes no account token, phone record, backup, or pairing code.
Offline or invalid feeds leave collection usable and show a failed check.

Automatic installation is **off until the owner checks its separate box**.
Android must also authorize this app as an install source. When both are true,
the app may ask Android 12+ to update without a new prompt only if Android's
documented self-update conditions hold; it always handles an Android approval
prompt when one is required. Android 8–11 and other ineligible devices require
the ordinary OS approval. If Android needs approval while the app is in the
background, reopen the companion and tap **Continue Android approval** to see
the OS prompt; the private install session remains guarded until Android
reports a terminal outcome or the installed version is observed. Unknown-apps
Settings is presented as an owner
choice, never changed by the app. An update waits during source disclosure or
permission prompts, reading, selection/review, transfer preparation, active
pairing/browser delivery, and an account save awaiting the matching readback
acknowledgment. Pending installation blocks new local work. Cancelling an
install prompt or verification leaves phone review available.

This self-updater is for the **website APK only**. The separate `play` build
flavor contains no signed website feed, APK downloader, PackageInstaller code,
update certificate asset, installer receiver/activity, or package-install
permissions. Its update status says Google Play manages updates. The Play
flavor retains contacts, call history and SMS history collection, review and
browser handoff; it is a source/build candidate, not an approved Play release.
Google's policy does not permit `REQUEST_INSTALL_PACKAGES` for this kind of
self-update, and the separate SMS/call-log policy and reviewer-login gates
remain unresolved.
See [official PackageInstaller user-action rules](https://developer.android.com/reference/android/content/pm/PackageInstaller.SessionParams#setRequireUserAction(int))
and [Google Play install-permission policy](https://support.google.com/googleplay/android-developer/answer/12085295).

## Owner flow

Use the signed APK's normal Android download/install flow. No developer mode, USB debugging, root, default SMS/dialer takeover, or security bypass is part of this product. If the installer or a restricted permission blocks progress, report that limitation; do not disable device protections.

1. Choose **Read contacts**, **Read call history**, and/or **Read SMS history** independently. Contacts are required for contact selection; calls and SMS are optional. Each source has its own Android consent request.
2. Select contacts, then **Review selected metadata**. **Select all contacts** verifies every stable local contact ID across every page before changing the selection; **Deselect all** affects the local selection only. Search and visible 100-row pages do not limit an all-page selection. Only selected contacts and matching available interaction metadata are eligible. Message bodies, attachments, MMS, and RCS are not collected.
3. Choose **Approve same-phone browser handoff**. Notification access is optional. The app still starts the required foreground service with a valid notification; if its shortcut is hidden, return from Recent apps to confirm the destination or use **Cancel browser handoff**. Android may stop the app after a permission change, requiring a fresh local review.
4. Use **Open AMPLIFai account in browser** on this same phone. For a current signed-in account, choose **Connect reviewed Android app**, check the account and course displayed by the app from server proof, and approve that destination. The browser then receives the reviewed preview without typing a code. The app's **One-use pairing code** is a five-minute fallback if discovery is unavailable. Each progressing page fetch, valid keepalive and completion extends the active browser session, bounded by inactivity and the visible Cancel action. Review each available source and approve its account save separately.
5. After delivery, browser-only users can **Cancel browser handoff**. Signed-in users separately save or decline each source, then use **Finalize reviewed sources**. The browser verifies durable account readback before the matching saved assertion; the companion only reports a confirmed save after the final bound receipt. Cancelling cannot retract already saved account data.

For a blocked permission or pre-pair handoff, **Copy safe support code** gives
the owner an optional code to submit through AMPLIFai contact. It contains only
the build, a random short reference, fixed stage/category, elapsed seconds and
zero-byte/status placeholders. It does not contain phone numbers, contact names,
message content, account identity, pairing secrets or raw exception text.

### Candidate contact-selection layout

The current source candidate opens a dedicated selection view after Contacts
becomes available. Search, page counts, Previous/Next and the applicable Review,
Handoff or Cancel action stay fixed while the contact choices scroll. Existing
native fonts, colors, buttons, checkboxes and side spacing remain in use. One
weighted choices region receives the remaining measured height, including on
short screens and with larger system text; a completed review replaces the
Review action with Handoff instead of stacking both actions.

IME visibility temporarily collapses the existing Sources & options button and
Previous/Next row. Search, the selected/page count, contact
choices and the applicable action remain available above the keyboard. The
navigation container retains its system inset, and the same secondary controls
return when the keyboard is dismissed. Fonts, control sizes and keyboard state
are unchanged.

With the keyboard open, the count reads **All contacts · Selected: N** and
the applicable action uses a short caption such as **Review** or **Continue**.
Its full accessible label is retained. Dismissing the keyboard restores the
page count and full action captions; both bulk-selection actions stay visible.

API30+ uses `WindowInsets.isVisible(Type.ime())`. The candidate API26–29 path
compares the original root window's system bottom inset with its stable bottom
inset from a decor-view listener before content ancestors consume it. The
comparison follows the pre-30 resize case in
[AndroidX's implementation](https://android.googlesource.com/platform/frameworks/support/+/refs/heads/androidx-main/core/core/src/main/java/androidx/core/view/WindowInsetsCompat.java).
It assumes resize mode: this Activity has shown native ScrollViews
and no soft-input-mode override, for which
[AOSP selects resize](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/oreo-release/core/java/android/view/ViewRootImpl.java).
The listener delegates to `view.onApplyWindowInsets(insets)` and returns that
result, preserving the decor's normal inset handling and descendant dispatch,
as supported by the [listener API](https://developer.android.com/reference/android/view/View.OnApplyWindowInsetsListener).
It is installed only on API26–29 during `onCreate` and cleared in `onDestroy`.
It adds no keyboard-height threshold, geometry heuristic or dependency.

The previous child-listener path failed the strict API29 compact-control gate:
root system bottom was 790px and stable bottom was 0px, while a
`fitsSystemWindows` ancestor applied 790px bottom padding and the controls
remained visible. Android 10's
[standard window layout](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android10-release/core/res/res/layout/screen_simple.xml)
has a `fitsSystemWindows` ancestor;
[View](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android10-release/core/java/android/view/View.java)
consumes those system insets, and the legacy
[ViewGroup dispatch](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android10-release/core/java/android/view/ViewGroup.java)
can stop at consumed insets. The decor listener observes the root before that
boundary. Its runtime qualification must use the unchanged strict fixture;
floating keyboards, pan mode and OEM behavior remain unqualified.

**Sources & options** opens the full setup, optional source controls, progress,
review details and privacy/help content. **Select all contacts** and **Deselect all**
stay visible beside search in contact selection. They affect all contacts across every page, including contacts hidden
by search; the selected count states this scope. **Return to contact
selection** restores the choices. Review and errors open their full text in the
details view. Source consent, available-zero versus unavailable coverage,
selection invalidation, bound saved receipts and temporary-store cleanup are
unchanged by the selection-layout work. RC11 includes this layout and the
account-bound native setup adapter below, with separate runtime and signed-package
qualification evidence.

Compile the candidate's synthetic Activity fixtures without running a device:

```sh
JAVA_HOME='/Applications/Android Studio.app/Contents/jbr/Contents/Home' \
ANDROID_HOME='/Users/jeffmoore/Library/Android/sdk' \
./gradlew --offline --no-daemon --no-configuration-cache \
  :app:testWebsiteDebugUnitTest :app:testPlayDebugUnitTest \
  :app:lintWebsiteRelease :app:lintPlayRelease \
  :app:compileWebsiteDebugAndroidTestJavaWithJavac
```

The runtime operator must install the matching candidate debug/test APKs on a
disposable, WAN-isolated emulator and configure each viewport before invoking
the existing instrumentation runner. Keep loopback available. The fixtures
reject physical phones and mismatched actual Activity width or font scale;
they never change device settings. Use `selection_layout_360` at 360dp/100%,
`selection_layout_320_large` at 320dp/150%, and `selection_layout_768` at
768dp/100%. For each configured scenario:

```sh
adb -s "$ANDROID_TEST_SERIAL" shell am instrument -w \
  -e scenario "$SELECTION_SCENARIO" \
  -e fixture_scope disposable-emulator -e network_isolated true \
  -e capture_screenshots true \
  ai.satoris.amplifai.phone.test/ai.satoris.amplifai.phone.LocalBridgeInstrumentedTest
```

Only 305 synthetic contacts and an available-empty call source are staged in
the private review store; no phone provider, handoff or account Save is used.
The fixtures measure actual window bounds and insets, require at least two
contact rows and 96dp of usable choices, compare fixed-control coordinates
before/after scrolling, and check native style values, paging/search, full
review/error text, review invalidation, all-page selection, Clear and bulk
Cancel. The result reports actual height, width and geometry; requested
emulator dimensions are not evidence of the rendered viewport. Matched-view
screenshots, these runtime checks and physical-device acceptance remain
separate from local compilation and unit/lint results.

The optional `capture_screenshots` flag writes PNGs (`populated`,
`scrolled`, `review`, `review-accessible`, `error-details`) after settled frames
and a `receipt.json` with relative filenames beneath the target app's external-files directory,
`instrumentation-selection-layout/<scenario>/`. The names are bounded and
reused for that scenario. UiAutomation or file-access failures are reported as
unavailable screenshots; they do not replace the geometry assertions. This
fixture does not stage a pending receipt or confirmed saved state. The runtime
operator should retain only artifacts named by the successful run's receipt.

Visibility failures name the control and record full/visible/window rectangles,
touch mode, native focusability, focus-request acceptance and scroll offsets.
The all-page/Clear fixture records the original focus request, sends an actual
Tab key event, then requires keyboard focus and full visibility. It does not
change production focusability or force a scroll position. The receipt also
records review text geometry after its asynchronous update. A failed run writes
`failure.png` when capture is available and a receipt with `outcome: failed`
before finishing the Activity; partial captures are not a passing result.

The separate `selection_ime_360`, `selection_ime_320_large` and
`selection_ime_768` scenarios use the same respective width/font requirements
and instrumentation command. They require API30+ and the emulator's installed
docked keyboard; they do not choose an IME, change keyboard settings or simulate
keyboard dimensions. An actual touchscreen event on Search must open that
keyboard, confirmed by visible IME insets. The receipt records its configured
identity, actual IME/system insets, window metrics and unobscured control/list
rectangles. Search and the applicable Review, Handoff or bulk Cancel action
must remain fully visible above the keyboard. The IME-specific list gate is at
least one complete contact row and 48dp of usable choices. The existing
two-row/96dp minimum applies before showing the keyboard and after dismissal;
the original no-IME scenarios retain that gate throughout selection. While the
IME is visible, navigation and paging must actually be `GONE`; checking their
controls prevents a usable but uncompacted viewport from passing this gate.

IME fixtures type a synthetic search through input events, delete the query,
dismiss with Back and verify that selected IDs are unchanged. They also check
that navigation/paging restore, reopen the keyboard and tap Review while it is
visible. The resulting review geometry and remaining IME visibility are
recorded; if the keyboard remains, a normal Back dismissal precedes the full
review reachability check. They return to selection with its Handoff action and
exercise synthetic bulk Cancel while the keyboard remains visible. They never
activate Handoff or read a phone provider. Capture names include `ime-review`,
`ime-filtered`, `ime-dismissed`, `ime-handoff`, `ime-cancel`, `review` and
`review-accessible`, `review-from-ime`, plus `failure` when needed. Both scenario
families retain the original review geometry/image after asynchronous text growth, report
`reviewInitiallyFullyVisible`, then use native keyboard focus and require the
full untruncated review text to be reachable. A partially visible initial
review is recorded as such, even when the later reachability check passes.

`selection_ime_api29_360` runs the same interaction and geometry gates on one
representative API29 emulator at 360dp/font100%. Its independent measurement uses
[UiAutomation interactive windows](https://developer.android.com/reference/android/app/UiAutomation#getWindows())
and [TYPE_INPUT_METHOD screen bounds](https://developer.android.com/reference/android/view/accessibility/AccessibilityWindowInfo#getBoundsInScreen(android.graphics.Rect)),
not the production bottom-versus-stable inset comparison. The fixture enables
`FLAG_RETRIEVE_INTERACTIVE_WINDOWS`, waits for actual window-change events, and
restores and verifies the original flags before writing either outcome receipt.
It records bounded window types/IDs/layers/focus/bounds, the focused application
window, real display size, visible window and actual root screen origin. Legacy
root-relative visibility rectangles are translated to screen coordinates. It
reads no window titles or node contents. The usable choices/control area is
clipped above the measured IME/display intersection. The receipt retains both
raw `imeWindow` and intersected `visibleImeWindow` bounds. The visible IME must
span the full display width and reach its bottom; the full-row, minimum-height
and action visibility gates still apply. An observed API29 keyboard reported
raw bounds `[0,1130][1080,2020]` on a `[0,0][1080,1920]` display while its image
showed a docked keyboard. The API supplies screen coordinates; the fixture
uses [Rect.intersect](https://developer.android.com/reference/android/graphics/Rect#intersect(android.graphics.Rect))
to measure the visible overlap without an assumed overscan or padding value.
API30+ fixtures retain their native inset/WindowMetrics observation path.
Legacy receipts also record current root system/stable top/bottom insets,
soft-input mode and a bounded ancestor chain with `fitsSystemWindows`, bounds
and padding before the IME, during each checked state and after dismissal.
These are diagnostics only; the pass oracle remains independent accessibility
window geometry plus actual control visibility. No listener is replaced or
added by these diagnostics.

The API29 scenario avoids API30 WindowMetrics/inset-type methods and uses
independent HashSet snapshots instead of API31 `Set.copyOf`. Production source
is unchanged by this fixture. Use the same revised test APK when comparing old
and repaired app APKs, and retain each run's app/test hashes, receipt and PNGs
separately. Compilation does not establish an API29 pass; the runtime operator
must execute the scenario on the isolated emulator.

## Native setup contract implementation

The reversible `phone-native-setup-v2` adapter work uses website commit
`8214edaf5ff547b300ef4610cbc4686bb368ba63`, contract revision 1. Frozen JSON
SHA-256 is `d2ff30a92fab809960dcfe0520e08585a7437026ab6c422deb66f78012984ac8`;
the normative Markdown SHA-256 is
`e7b738fc38609e97b014d37f6969605a2d16a46def062e2bfce4b4204b02c7b7`.
The exact JSON catalog is a test resource, including synthetic exchanges that
grant no build admission. Server routes and the public build allowlist remain
inactive.

The website candidate advertises read-only schema-2 capabilities on
`127.0.0.1:48752/v1/setup-capabilities` after explicit app launch. This carries
the actual installed version/build and fixed Android website identity; it is
routing information, not build admission or authority. The endpoint requires
the exact loopback Host and canonical account Origin, permits only GET and its
preflight, rejects bodies and duplicate headers, and returns no-store/nosniff.
A 4,096-byte header ceiling and a two-second absolute monotonic deadline bound
each client. Activity destruction closes the listener and active socket. Plain
launch/discovery requests no source permission and performs no collection or
account setup request. Play has no setup intent filter or discovery listener
and rejects an explicitly delivered setup intent.

Only the exact `amplifai-phone://setup` protocol/setup/bootstrap link is accepted.
Extra fields, duplicates and authority overrides are rejected; the Activity
clears the incoming URI. Bounded strict JSON validates the Android identity,
account/course/category snapshot and exact `phone-run-android-v1` disclosure
hash. The native approval checkbox is initially unchecked. Approval prepares a
run; **Start collection** remains a separate action with its own current server
admission. After verified Start, only approved sources enter the existing
Android permission/read path, in order. The two-letter country input freezes
at the first Start attempt. A denied or restricted source stays unavailable;
other approved sources can continue. Leaving the Activity cancels unfinished
reads and the remaining queue; returning does not restart them automatically.

Capabilities, request bodies and approval state stay in process memory. Claim,
approval and Start retries reuse their exact original operation identifiers
and bytes. An uncertain response never grants the next action. An identical
committed approval can be recovered after the ten-minute setup TTL. Its local
run deadline is capped from the original approval request's monotonic time by
the validated `expiresAt - approvedAt` duration, at most twelve hours; claim
uses the original claim request's ten-minute cap. A 60-second timestamp
plausibility allowance does not extend either cap. The original server expiry
also remains mandatory, and approval must predate the frozen claim expiry.
Cancellation drops authority and rejects late replies. Fixed HTTPS routes
reject redirects, wrong response types, oversized bodies and mismatched fields;
native code has no browser-cookie status/revoke route.

Native **Review** requests the beginning of the selected summary after the final
asynchronous text and footer have been laid out. It uses the existing details
ScrollView, preserving scrolling to the rest of the summary, account disclosure
and help. The one-shot reveal removes its callback when it runs or when review/lifecycle
state changes; it does not depend on a new focus transition in touch mode.
Android's [pre-draw callback](https://developer.android.com/reference/android/view/ViewTreeObserver.OnPreDrawListener#onPreDraw())
runs after views have been measured and assigned their frames. Legacy review
navigation and the qualified selection/IME layout are unchanged.
The API29 causal trace against installed `d09dfdb` showed the reveal reaching the
summary, followed immediately by an older `ScrollView.computeScroll` animation
moving it offscreen. The native reveal now uses `smoothScrollTo` to update the
framework scroller's destination. The framework replaces an older animation or
aborts a recent one before applying the new delta; plain `scrollTo` leaves that
animation active. See the [framework implementation](https://android.googlesource.com/platform/frameworks/base/+/android-10.0.0_r1/core/java/android/widget/ScrollView.java#1315).
Manual scrolling, legacy navigation, focus and IME handling retain their existing
behavior. The runtime owner qualified `323d466` with the unchanged causal fixture:
the negative setup scenario passed API29 standard/compact and API36 compact,
including the independent first-line visibility and full-text reachability gates.
The temporary scroll trace was then removed; both regression gates remain.

Selection stays editable until deliberate reviewed **Continue**. The service
requires actual current source permissions, freezes the selected pages and
binds that one handoff/digest to the approved run before serving them. A lost
bind response exposes **Retry Continue** for the same binding or Cancel; it
cannot substitute a selection or handoff. Run-bound transfers reject legacy
pairing codes and verify the expected run, approval, account, course and source
scope before connect and every release consume. The existing exact-release
retry and bound saved/readback receipt gates remain. Android payload records
keep their existing source IDs; the web-owned save adapter is responsible for
the contract's `android-native-<category>` account source identifiers.

The foreground service may retain that exact in-memory handoff across Activity
recreation, with no new source read. Process loss cannot recover capabilities
or silently collect again. Cancel stops local work first and directs the user
to their signed-in account for saved results or server-side stopping. Android
chooses the browser; the app promises no original browser profile or tab.
Active bound delivery opens the existing
`?continue=reviewed&enrollmentId=<uuid>&week=phone` account route. Before delivery
or after local cancellation, run controls use
`?phoneRunControl=<run-uuid>&enrollmentId=<uuid>&week=phone` without `continue`.
Only the completed saved/readback handshake retains a non-secret in-memory
report return with `enrollmentId`, `week=phone`, `completedHandoffId` and
`completedPayloadSha256`; this recovery link never re-enters continuation.
The website still checks the signed-in owner and durable result. These IDs
grant no authority and are not persisted with cleared capabilities.
Verified bind from deliberate Continue emits one `ACTION_VIEW` while the
initiating Activity remains visible. Duplicate readiness callbacks, Activity
recreation and background readiness do not reopen the browser. If Android
cannot open it, the existing Open account action remains an explicit retry.
Legacy separate source approval/Save flows and ephemeral review cleanup remain
in place. A prepared, started, bound or delivered state never establishes a
saved account report.

### Synthetic setup qualification

Compile/unit/lint only, from this Android directory:

```sh
JAVA_HOME='/Applications/Android Studio.app/Contents/jbr/Contents/Home' \
ANDROID_HOME='/Users/jeffmoore/Library/Android/sdk' \
./gradlew --offline --no-daemon --no-configuration-cache \
  :app:testWebsiteDebugUnitTest :app:testPlayDebugUnitTest \
  :app:lintWebsiteRelease :app:lintPlayRelease \
  :app:compileWebsiteDebugAndroidTestJavaWithJavac \
  :app:compilePlayDebugAndroidTestJavaWithJavac
```

The release/runtime owner builds `:app:assembleWebsiteDebug
:app:assembleWebsiteDebugAndroidTest` from the same clean commit and records
the app/test hashes. The outputs are
`app/build/outputs/apk/website/debug/app-website-debug.apk` and
`app/build/outputs/apk/androidTest/website/debug/app-website-debug-androidTest.apk`.
On an isolated disposable emulator, with Contacts permission **ungranted**:

```sh
adb -s <owned-emulator-serial> shell am instrument -w \
  -e scenario native_setup_v2 \
  -e fixture_scope disposable-emulator \
  -e network_isolated true \
  ai.satoris.amplifai.phone.test/ai.satoris.amplifai.phone.LocalBridgeInstrumentedTest
```

`native_setup_v2` drives the real Activity approval checkbox and actions,
separate Start, synthetic contact selection, asynchronous review and actual
Continue permission denial. It checks identical lost-response approval/Start
retries, then exercises adapter-level bind freeze and cancellation with a late
synthetic bind response. The browser-launch decision uses that bound synthetic
run and an intercepted Intent: it requires the exact continuation URL once and
rejects unbound, restored, background and duplicate readiness. Separate delayed
claim/approve/Start replies must not
revive a cancelled setup. It uses a fixed test clock, fake transport and a
synthetic callback that writes only its private fixture store, and an account
navigator that captures Intents without launching a browser. These replace
private in-memory fields through instrumentation reflection. No Intent, URI,
Bundle, preference, debug setting or release entry point selects those hooks;
production defaults retain real transport, time and permission-gated reads.
The fixture never grants permission, reads a provider, opens a browser or
performs an account Save. Its real Continue denial occurs before the service
bind boundary. The separate adapter check does not prove positive foreground
service delivery or account persistence.

The separate `native_setup_service_v2` scenario reuses the same Activity setup,
selection and review with **only READ_CONTACTS already granted by the runtime
owner on the disposable emulator**. Calls and SMS permissions must remain
ungranted. Use the same instrumentation command with
`-e scenario native_setup_service_v2`; the fixture never grants or revokes a
permission. The owner restores the permission state after this scenario and
keeps the ungranted `native_setup_v2` negative case.

Its real Continue builds the selected private-store PagedTransfer and starts
HandoffService. Synthetic authority loses the first committed BIND response;
the real Retry Continue must preserve identical request bytes, disabled selection
controls and the same single transfer database. Recognized SQLite `-journal`,
`-wal` and `-shm` companion files belong to that database; orphaned companions,
unknown names and a second database fail the gate. API29 and API36 runtime receipts
confirmed one database plus its same-base journal remained unchanged across the
first bind and exact retry. See
[SQLite's temporary-file behavior](https://www.sqlite.org/tempfiles.html#rollback_journals).
A successful bind emits one intercepted account
Intent. Synthetic VERIFY/CONNECT/CONSUME responses then authorize requests to the
real loopback listener. Its connect capability, release code and changed-code
negative case are canonical unpadded base64url encodings of distinct fixed
32-byte synthetic values. Repeating an arbitrary alphabet character 43 times
can produce noncanonical padding bits; native authority rejects those aliases
before CONNECT/CONSUME, as the focused unit regression verifies. The production
validator is unchanged. The fixture checks the discovered handoff/manifest against
the actual bind, an exact release retry, changed-release denial, and a hashed page
containing only selected synthetic contacts 1 and 3. Local completion must reach
`received` without a saved acknowledgment. The real Cancel action must close the
listener, clear authority and remove transfer files; a later connection/bind is
denied. This tests native service delivery with synthetic authority, not backend
admission, authenticated browser behavior or account persistence. No new
production injection hook is needed: the service receives the same approved run
object created through the existing private transport hook. The shared loopback
fixture reader uses the charset-name overload available before API29; the
[Charset overload](https://developer.android.com/reference/java/io/ByteArrayOutputStream#toString(java.nio.charset.Charset))
was added in API33.

Both scenarios assert the first rendered review line is visible immediately
after the app's asynchronous Review result, before any fixture scroll/focus
request, and capture `review.png` there. They then use the native focus-rectangle
scroll mechanism to verify every untruncated line can be fully reached, ending
with `review-end.png`. Initial visibility and full-text reachability are separate
gates; a later fixture scroll cannot repair a failed initial gate.

Artifacts use the target application's external-files directory through
`Instrumentation.getTargetContext()`, matching the selection fixture. The test
package context points at a different package's storage and failed directory
creation in the API29 instrumented process. The bounded test-only directory is
`native-setup-fixture/<scenario>/` (normally
`/sdcard/Android/data/ai.satoris.amplifai.phone/files/native-setup-fixture/native_setup_v2/`
or the sibling `native_setup_service_v2/`).
A missing storage root fails explicitly; directory creation failure reports the
target path, and Activity cleanup also runs if fixture construction fails.
See the [framework context distinction](https://developer.android.com/reference/android/app/Instrumentation#getTargetContext()).
The bounded `receipt.json` contains `scenario`, `api`, measured configuration
`widthDp`/`heightDp`/`fontScale`, `passed`, the retry/cancellation/permission gate
booleans, `continueCoverage`, synthetic operation and intercepted Intent counts,
`controls` with screen/visible/window rectangles and root offsets, and relative
`screenshots` names. `reviewInitial` records first-line/visible/viewport geometry,
scroll position, focus and touch mode; `reviewLinesReachable` counts fully reached
lines. The service case adds delivery/cleanup gates, actual non-secret
handoff/digest binding, and loopback method/path/status entries. It never writes
capabilities, tokens or request/response bodies. Failure adds a type and bounded
assertion detail before Activity finish. Fixture UI assertions are captured on
the main thread and rethrown on the instrumentation thread, allowing the failure
screenshot and receipt to be written before cleanup. A synthetic assertion
self-check records `mainAssertionPropagated`. The service scenario records
`pendingTransfer` and `boundTransfer` with actual bind counts, file counts and
at most eight relative synthetic transfer filenames before its invariant checks.
It requires the same database filename across retry and still requires all
transfer files, including companions, to be removed on Cancel.
Optional UiAutomation PNGs are
`disclosure`, `prepared`, `selection`, `review`, `review-end`, `stopped`, and
`failure` when applicable; the service case also captures `binding-pending`,
`service-ready` and `received`. Unavailable capture is recorded.
Only files named by that run's receipt belong to its evidence. API29 visibility
is translated from root-relative to screen coordinates, with no fixed inset.

Local compilation is not installed runtime evidence. The runtime owner must
qualify these changed setup screens and controls on the exact committed bytes;
the native Review navigation and extra Cancel action warrant a compact viewport
check. The service scenario additionally needs the explicitly scoped emulator
Contacts grant.
Existing selection/IME evidence is retained for unchanged behavior. Server
activation, real permission/source collection, authenticated delivery/saving,
physical-device acceptance and production build admission remain separate.

## Permission and coverage boundaries

- `READ_SMS` and `READ_CALL_LOG` are hard-restricted permissions: an installer allowlist and an actual runtime grant are separate conditions. The official installer API defaults to allowlisting requested restricted permissions, but an OEM installer can apply different restrictions. A normal sideload can work; installation alone does not prove source access. [Permission reference](https://developer.android.com/reference/android/Manifest.permission#READ_SMS), [installer API](https://developer.android.com/reference/android/content/pm/PackageInstaller.SessionParams#setWhitelistedRestrictedPermissions(java.util.Set%3Cjava.lang.String%3E)).
- This website APK's normal installer path and Google Play eligibility are separate. Google's [SMS/call-log policy](https://support.google.com/googleplay/android-developer/answer/10208820?hl=en) requires a declared, reviewed permitted use or exception; this app does not force a default SMS or dialer role or claim a Play exception.
- Covered downloaded/local-file apps on Android 15/16 can also encounter Restricted Settings for the SMS permission group. This build does not guide users around it; unavailable SMS must leave contacts and permitted calls usable. [Android 16 security requirements](https://source.android.com/docs/compatibility/16/android-16-cdd#225_security_model).
- Grants are checked before provider queries, after permission results, before handoff, and when returning to the Activity. Revoked local sources are dropped and their handoff cancelled. Provider refusal is unavailable, not an empty successful read.
- A readable source with zero observed records remains available and distinct from denied/not-read history. `missingSources` identifies unavailable history; unavailable sources cannot export stale interactions. Available metadata is observed coverage, never proof of completeness.
- Provider scans stream into an atomic private store instead of stopping at the former 100,000/256,000 aggregate scan bounds. Contact phone values have no former 20-per-contact cap. Source counts and selected record counts must fit the versioned protocol's signed 32-bit count fields; a single selected record must fit a 128 KiB page. These are explicit errors, never silent truncation. Page count is driven by the selected records, not a fixed 32 MiB handoff cap. A manifest/control message remains bounded to 8,192 ASCII bytes, with sample labels omitted when too long while full contact names remain in record pages.

## Bounded handoff lifecycle

An unexported, owner-started `dataSync` foreground service serves immutable reviewed pages from app-private no-backup storage while the Activity yields to the same-phone browser. The service performs no provider queries. Leaving the Activity cancels unfinished provider reads; completion after cancellation is discarded. Transfer pages are deleted on handoff cancellation, replacement, completion or process restart. The local review store closes and is deleted when its Activity is destroyed; a process restart removes any orphaned review file before another read.

The current source candidate's reviewed handoff binds only `127.0.0.1:48751` and checks the exact Host and `https://amplifai.satoris.ai` Origin. The website candidate's separate read-only setup discovery uses port 48752 as described above. Account proof and browser destinations use that same canonical authority; proof requests reject redirects. The prior RC10 artifact retains its original authority. Pairing limits attempts, requires the one-use bearer token and ordered pages, and binds an immutable handoff ID and SHA-256 manifest. The code expires after five minutes without pairing. An authorized v2 session has a two-hour sliding inactivity window while the browser fetches, reviews, saves and verifies pages. A requested page is not proof the browser validated it; native `/v1/complete` records source delivery or explicit decline, and the separate bound saved/readback handshake confirms account persistence. Cancellation, five bad codes, saved confirmation, expiry or I/O failure closes the listener and clears the grant. OS termination can shorten the window; it never asserts a save from page delivery alone.

Before source completion, an exact `/v3/release` retry revalidates the original intent, capability and release grant with the server, then returns the existing token and bound manifest. The first release request remains fixed even if the consume response is lost. A changed request, expired or cancelled handoff, or rejected server proof cannot recover a token. Release retries do not extend the transfer deadline or grant another save. New setup/disclosure contracts and published package identities remain separate qualification work.

The foreground-service notification contains no contacts or pairing code; it may show the count of pages offered. Denied app/channel notification visibility does not by itself stop the approved handoff, but actual foreground promotion failure, process death or timeout does. Android's Active apps surface and the in-app Cancel remain recovery controls. Activity recreation does not own or close an active bridge. `START_NOT_STICKY`, no boot receiver, no persisted code/token and orphan scratch cleanup mean process loss cannot silently restart collection or transfer. The handoff service requests foreground-service/data-sync and optional notification permissions in addition to contacts/call/SMS/Internet permissions. The separate website updater declares Android's package-install permissions, but performs no package operation until its safety gate and owner/OS approval conditions are met. [Foreground-service types](https://developer.android.com/develop/background-work/services/fgs/service-types), [notification permission](https://developer.android.com/develop/ui/compose/notifications/notification-permission).

## Local build and controlled signing

Use the installed Android SDK/JBR and existing Gradle dependencies. No signing values, keystore, owner data, or APK belongs in Git.

```sh
JAVA_HOME='/Applications/Android Studio.app/Contents/jbr/Contents/Home' \
ANDROID_HOME='/Users/jeffmoore/Library/Android/sdk' \
./gradlew --offline --no-daemon --no-configuration-cache \
  :app:testWebsiteDebugUnitTest :app:testPlayDebugUnitTest \
  :app:lintWebsiteRelease :app:lintPlayRelease \
  :app:assembleWebsiteRelease :app:bundlePlayRelease
```

Run from `collector/android`. The current Android plugin exposes debug JVM
tests, not a `testReleaseUnitTest` task; both production variants are compiled
and linted by their release builds. `websiteRelease` retains the existing
application ID, version and controlled signing path. `playRelease` has the
same app identity and source consent/ACK behavior but is kept out of the
website APK download and update feed. Its local AAB is a qualification artifact;
Play upload signing, policy declarations and reviewer access must be resolved
before any submission. Do not publish either unsigned local output.

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

Partial configuration is rejected. This Android project disables the configuration cache and rejects an explicit `--configuration-cache` before reading any signing values: even failed configuration can otherwise be cached. Use `--no-daemon --no-configuration-cache`; never echo credentials, include passwords in command arguments, or use a build scan. With no variables the release remains **unsigned** for local validation; never publish `app-website-release-unsigned.apk` as an installable download. The existing controlled Keychain wrapper's `build` action now builds only `websiteRelease`, preserving its prior signing scope. Verify `app/build/outputs/apk/website/release/app-website-release.apk` with `apksigner`, its version/application ID/non-debuggable manifest and pinned signer before website publication.

For a **local Play candidate only**, `swift script/android_release_signing.swift build-play` signs `app/build/outputs/bundle/playRelease/app-play-release.aab` with the same existing Android rc8 release identity, without printing or exporting its password. Verify the AAB's JAR signature and SHA-256, then compare its certificate fingerprint with the expected upload certificate recorded in Google Play **before** any Console upload. Google Play may distribute APKs under a different app-signing key; compatibility with website installs must be decided from the Play app-signing certificate, not inferred from this local AAB. The current Play listing, reviewer access and restricted SMS/call-log policy are separate gates. Signing/publication are operator-owned, not implied by this README.

After verifying the exact APK, the existing Keychain-held release identity can
sign the update feed without a password in shell arguments:

```sh
swift script/android_release_signing.swift sign-feed \
  /absolute/path/to/verified.apk \
  https://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/android-2026.10.21-rc11/AMPLIFai-Phone-Android-2026.10.21-rc11.apk \
  /absolute/path/to/android-update-v1.txt
```

Run that command from the repository root. Publish the exact signed feed at
`/phone/android-update-v1.txt` only after the immutable APK/source/checksum
release exists and its anonymous download hash matches. Never reuse a release
tag for different bytes.

## Remaining physical acceptance

JVM socket tests and emulator runs do not establish physical-device acceptance. Use the actual published signed APK through the normal download/install path, with owner-approved synthetic test contacts/history where practical. Record device model, Android/OEM version, browser version, APK hash and per-source outcome; never capture bodies, credentials, or pairing tokens in evidence.

The API 36 emulator instrumentation covers the retained v1 pair/ACK contract and v2 private store, optional-source rollback, ordered pages, declined-source revocation, long active transfer, saved-ACK retries, 25,700 selected contacts over 257 pages, and 100,001 contact rows plus 256,001 matching call rows over 2,561 pages. This is Android runtime and local protocol evidence, not same-phone Chrome, cloud saving, normal installer permission or physical-device acceptance. Keep code/token and synthetic record identity out of logs.

- Samsung and Pixel: install without developer tools; confirm contacts-only, contacts+calls, and permitted SMS independently. Denied/installer-restricted sources must stay unavailable and must not block permitted sources. A genuinely empty readable history must say zero observed, not denied.
- Android 13+: deny notifications and disable the channel before approval; confirm foreground promotion still succeeds, the in-app Cancel works and no permission prompt appears during approval. Disable the channel during a live handoff and return from Recent apps to Cancel. A runtime permission revoke may terminate the process; verify fresh-process orphan cleanup and no resurrected code. Confirm older API 26–28 two-argument and API 29+ typed foreground-service behavior when those devices are available.
- Switch app → same-phone Chrome and complete small and paged transfers; verify the account's separate consent/receipt flow. Browser local-network denial must remain an honest transport failure, not source-access denial or successful pairing.
- Recreate the Activity while ready; the existing code/session survives without re-collection. Cancel in-app or from an available notification, finish the Activity, remove the app task, use Android's Active apps Stop, force process death, and wait past five minutes before initial pairing: no new read, replay, silent restart, or stale usable code. Once paired, verify progress and keepalive extend only the active session's two-hour inactivity deadline, including during account save/readback. Check expiry, cancellation and notification visibility separately.
- Revoke a previously read permission, return, and confirm that source is dropped and an existing handoff is cancelled before a new review. Simulate cancellation during a slow provider query: no completed source is retained.
- Confirm exact Host/Origin rejection, bad-code exhaustion, token replay rejection, ordered-page retry, one-use delivery, wrong and correct saved acknowledgements, and listener closure only after acknowledgement/cancel/expiry. JVM tests cover the local protocol; these do not establish browser/OEM interoperability.

Record actual physical permission and account results separately from local checks. Never infer complete history from a readable provider or infer a saved account from page delivery.
