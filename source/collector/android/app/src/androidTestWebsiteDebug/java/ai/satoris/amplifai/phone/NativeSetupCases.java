package ai.satoris.amplifai.phone;

import android.app.Instrumentation;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Rect;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ScrollView;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.ConnectException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.EnumMap;
import java.util.HashSet;
import java.util.Set;
import java.util.Map;
import java.util.TreeSet;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BooleanSupplier;

/** Disposable synthetic setup UI. No provider reads, permission requests, browser or account saves. */
final class NativeSetupCases {
    private static final Instant NOW = Instant.parse("2026-10-21T22:00:00Z");
    private static final String SETUP = "44444444-4444-4444-8444-444444444444";
    private static final String APPROVAL = "99999999-9999-4999-8999-999999999999";
    private static final String RUN = "88888888-8888-4888-8888-888888888888";
    private static final String ENROLLMENT = "66666666-6666-4666-8666-666666666666";
    private final Instrumentation instrumentation;
    private final MainActivity activity;
    private final SyntheticAuthority authority = new SyntheticAuthority();
    private final AtomicInteger collections = new AtomicInteger();
    private final AtomicInteger accountIntents = new AtomicInteger();
    private final AtomicReference<Intent> interceptedAccountIntent = new AtomicReference<>();
    private final JSONObject receipt = new JSONObject();
    private final JSONArray screenshots = new JSONArray();
    private final JSONObject controls = new JSONObject();
    private final File output;
    private final boolean serviceFlow;
    private final boolean completionFlow;
    private final JSONArray loopbackRequests = new JSONArray();

    static String run(Instrumentation instrumentation, Bundle arguments) throws Exception {
        require("disposable-emulator".equals(arguments.getString("fixture_scope")) &&
                "true".equals(arguments.getString("network_isolated")), "Use a disposable WAN-isolated emulator");
        require(Build.FINGERPRINT.startsWith("generic") || Build.HARDWARE.contains("ranchu") || Build.HARDWARE.contains("goldfish"),
                "Native setup fixture must not run on a physical device");
        MainActivity activity = (MainActivity) instrumentation.startActivitySync(new Intent(instrumentation.getTargetContext(), MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK));
        try {
            NativeSetupCases fixture = new NativeSetupCases(instrumentation, activity, arguments.getString("scenario"));
            try {
                fixture.verify();
                fixture.receipt.put("passed", true);
                return fixture.completionFlow
                        ? "synthetic service ACK and cleanup completed while the Activity was stopped; return restored only confirmed report presentation"
                        : fixture.serviceFlow
                        ? "synthetic native Continue reached real foreground service binding, exact retry, selected-page delivery and received; Cancel removed authority and transfer files; no provider reads or account Save"
                        : "synthetic native approval, separate Start, selection/review/Continue availability, exact retries and late-response cancellation passed; no real collection or account Save";
            } catch (Exception | AssertionError failure) {
                fixture.receipt.put("passed", false).put("failure", failure.getClass().getSimpleName());
                if (failure instanceof AssertionError && failure.getMessage() != null)
                    fixture.receipt.put("failureDetail", failure.getMessage().substring(0, Math.min(400, failure.getMessage().length())));
                try { fixture.capture("failure"); }
                catch (Exception | AssertionError captureFailure) {
                    failure.addSuppressed(captureFailure);
                    fixture.receipt.put("failureCaptureFailed", captureFailure.getClass().getSimpleName());
                }
                throw failure;
            } finally {
                fixture.authority.release.countDown();
                if (fixture.serviceFlow) fixture.main(() -> {
                    HandoffService.LocalBinder handoff = fixture.get("handoff", HandoffService.LocalBinder.class);
                    if (handoff != null) handoff.cancel();
                });
                fixture.receipt.put("screenshots", fixture.screenshots).put("controls", fixture.controls)
                        .put("providerReads", 0).put("permissionRequests", 0).put("browserOpens", 0)
                        .put("accountSaves", 0).put("syntheticCollectionCallbacks", fixture.collections.get())
                        .put("interceptedAccountIntents", fixture.accountIntents.get())
                        .put("loopbackRequests", fixture.loopbackRequests);
                try (FileOutputStream file = new FileOutputStream(new File(fixture.output, "receipt.json"))) {
                    file.write(fixture.receipt.toString(2).getBytes(StandardCharsets.UTF_8));
                }
            }
        } finally {
            instrumentation.runOnMainSync(activity::finish);
            instrumentation.waitForIdleSync();
        }
    }

    private NativeSetupCases(Instrumentation instrumentation, MainActivity activity, String scenario) throws Exception {
        this.instrumentation = instrumentation; this.activity = activity;
        this.completionFlow = "native_setup_completion_v2".equals(scenario);
        this.serviceFlow = completionFlow || "native_setup_service_v2".equals(scenario);
        authority.deliveryEnabled = serviceFlow;
        File root = instrumentation.getTargetContext().getExternalFilesDir("native-setup-fixture");
        require(root != null, "Target app external fixture storage is unavailable");
        output = new File(root, scenario);
        require(output.isDirectory() || output.mkdirs(), "Cannot create target app fixture output: " + output.getAbsolutePath());
        receipt.put("scenario", scenario).put("api", Build.VERSION.SDK_INT)
                .put("widthDp", activity.getResources().getConfiguration().screenWidthDp)
                .put("heightDp", activity.getResources().getConfiguration().screenHeightDp)
                .put("fontScale", activity.getResources().getConfiguration().fontScale)
                .put("continueCoverage", completionFlow
                        ? "Actual service delivery, synthetic exact Save ACK and receipt, cleanup while Activity is stopped, and confirmed report return. No hosted account Save or provider reads."
                        : serviceFlow
                        ? "Actual Continue dispatch uses the parent-granted emulator Contacts permission, real PagedTransfer and HandoffService. Authority and data are synthetic, navigation is intercepted. Local received is followed by Cancel, without a saved acknowledgment."
                        : "Actual Continue dispatch denies missing Android permission before binding. Selection freeze/late bind cancellation use the adapter directly with synthetic transport. Browser launch decision uses a bound synthetic run and intercepted Intent only. Service delivery still needs real permission authority.");
    }

    private void verify() throws Exception {
        AssertionError expected = new AssertionError("Synthetic main-thread assertion");
        try { main(() -> { throw expected; }); }
        catch (AssertionError propagated) {
            require(propagated == expected, "Main-thread assertion identity changed");
            receipt.put("mainAssertionPropagated", true);
        }
        require(receipt.optBoolean("mainAssertionPropagated"), "Main-thread assertion was swallowed");
        await(() -> get("handoff", HandoffService.LocalBinder.class) != null, "Handoff controls did not bind");
        main(() -> {
            require(get("nativeSetup", NativeSetupSession.class) == null, "Plain launch created setup authority");
            require(get("store", SanitizedStore.class).count("contacts") == 0, "Fixture requires an empty private store");
            require(get("permissionPendingSource", Integer.class) == 0, "Plain launch requested source permission");
            boolean contactsGranted = permissionGranted(android.Manifest.permission.READ_CONTACTS);
            require(contactsGranted == serviceFlow, serviceFlow
                    ? "Service fixture requires parent-granted Contacts permission on the disposable emulator"
                    : "Continue-denial fixture requires Contacts permission to remain ungranted");
            if (serviceFlow) require(!permissionGranted(android.Manifest.permission.READ_CALL_LOG) &&
                    !permissionGranted(android.Manifest.permission.READ_SMS), "Grant only Contacts for the service fixture");
            set("authorityTransport", authority);
            set("setupClock", (java.util.function.Supplier<Instant>) () -> NOW);
            set("nativeCollectionStart", (Runnable) this::seedSyntheticCollection);
            set("accountNavigator", (java.util.function.Consumer<Intent>) intent -> {
                accountIntents.incrementAndGet(); interceptedAccountIntent.set(new Intent(intent));
            });
            set("diagnosticAttempts", 8); // Keep the intentional permission-negative fixture entirely synthetic.
            get("country", EditText.class).setText("US");
        });
        verifyDiscovery();
        openSetup(); awaitApproval();
        main(() -> {
            require(!get("setupAgreement", CheckBox.class).isChecked(), "Approval checkbox was preselected");
            require(!get("setupAction", Button.class).isEnabled(), "Unchecked approval is actionable");
            require(NativeSetupContract.AGREEMENT.contentEquals(get("setupAgreement", CheckBox.class).getText()), "Disclosure agreement drifted");
            require(get("setupDestination", TextView.class).getText().toString().contains("fixture@example.test"), "Account label missing");
        });
        capture("disclosure");
        click("setupAgreement"); click("setupAction");
        await(() -> !get("setupBusy", Boolean.class), "Uncertain approval did not settle");
        require(get("nativeSetup", NativeSetupSession.class).state() == NativeSetupSession.State.APPROVAL_PENDING, "Lost reply granted approval");
        require(collections.get() == 0, "Approval started collection");
        click("setupAction");
        await(() -> get("nativeRun", NativeRunAuthority.class) != null, "Exact approval retry did not prepare run");
        main(() -> require("Start collection".contentEquals(get("setupAction", Button.class).getText()), "Approval skipped separate Start"));
        require(collections.get() == 0, "Prepared receipt read a source");
        require(get("nativeAccountReturn", NativeAccountReturn.class).href(false).equals(LocalBridge.ACCOUNT_ORIGIN +
                "/phone/account?phoneRunControl=" + RUN + "&enrollmentId=" + ENROLLMENT + "&week=phone"), "Prepared account controls lost their binding");
        capture("prepared");
        click("setupAction");
        await(() -> !get("setupBusy", Boolean.class), "Uncertain Start did not settle");
        require(collections.get() == 0 && get("nativeRun", NativeRunAuthority.class).state() == NativeRunAuthority.State.START_PENDING,
                "Lost Start reply collected metadata");
        click("setupAction");
        await(() -> collections.get() == 1 && get("choices", LinearLayout.class).getChildCount() == 30, "Synthetic collection did not reach choices");
        main(() -> {
            require(get("permissionPendingSource", Integer.class) == 0, "Fixture requested Android source access");
            LinearLayout choices = get("choices", LinearLayout.class);
            ((CheckBox) choices.getChildAt(0)).performClick(); ((CheckBox) choices.getChildAt(2)).performClick();
            require(!get("handoffButton", Button.class).isShown(), "Continue appeared before review");
        });
        capture("selection");
        click("reviewButton");
        await(() -> get("reviewed", Boolean.class), "Selected local review did not finish");
        main(() -> {
            require(get("handoffButton", Button.class).isEnabled(), "Reviewed selection did not enable Continue");
            require("Continue with selected contacts".contentEquals(get("handoffButton", Button.class).getText()), "Wrong native Continue action");
            require(get("review", TextView.class).getText().toString().contains("2 selected contacts"), "Review selection differs");
        });
        verifyReviewVisibility();
        receipt.put("approvalExactRetry", authority.approvalRetried).put("startExactRetry", authority.startRetried);
        if (serviceFlow) { verifyServiceHandoff(); return; }
        click("handoffButton");
        main(() -> {
            require(!get("reviewed", Boolean.class) && !get("handoff", HandoffService.LocalBinder.class).snapshot().active(),
                    "Continue bypassed the actual Android permission gate");
            require(get("status", TextView.class).getText().toString().contains("A source permission changed"),
                    "Continue permission denial was not actionable");
        });
        require(authority.bindCalls == 0, "Denied Continue contacted the bind authority");
        require(accountIntents.get() == 0, "Approval, Start or permission-denied Continue opened the browser");
        receipt.put("continuePermissionDeniedBeforeBind", true);
        verifyBoundCancellation();
        for (NativeAuthorityTransport.Route route : Arrays.asList(NativeAuthorityTransport.Route.CLAIM,
                NativeAuthorityTransport.Route.APPROVE, NativeAuthorityTransport.Route.START)) verifyLateCancellation(route);
        capture("stopped");
    }

    private boolean permissionGranted(String permission) {
        return activity.checkSelfPermission(permission) == android.content.pm.PackageManager.PERMISSION_GRANTED;
    }

    private void verifyReviewVisibility() throws Exception {
        TextView text = get("review", TextView.class);
        ScrollView scroll = get("detailsScroll", ScrollView.class);
        await(() -> text.getLayout() != null && !text.isLayoutRequested() && !scroll.isLayoutRequested(),
                "Final review text did not finish layout");
        settled();
        main(() -> {
            Rect bounds = screenBounds(text);
            Rect first = reviewLine(text, 0); first.offset(bounds.left, bounds.top);
            Rect visible = visibleBounds(text);
            try {
                receipt.put("reviewInitial", new JSONObject().put("firstLine", first.toShortString())
                        .put("visible", visible.toShortString()).put("viewport", visibleBounds(scroll).toShortString())
                        .put("scrollY", scroll.getScrollY()).put("focused", text.isFocused())
                        .put("touchMode", text.isInTouchMode()).put("fullyVisible", visible.contains(bounds)));
            } catch (org.json.JSONException impossible) { throw new AssertionError(impossible); }
            require(visible.contains(first), "Review did not reveal its beginning after asynchronous text: first=" + first + " visible=" + visible);
            require(text.getLayout().getLineCount() > 0 && text.getLayout().getLineCount() <= 128, "Review line count is outside fixture bounds");
        });
        capture("review"); // No fixture scroll or focus has rescued the initial product position.
        int lines = text.getLayout().getLineCount();
        for (int line = 0; line < lines; line++) {
            int index = line;
            main(() -> {
                require(text.getLayout().getEllipsisCount(index) == 0, "Selected review text was truncated");
                // The native focus rectangle mechanism must reach every complete text line.
                text.requestRectangleOnScreen(reviewLine(text, index), true);
            });
            settled();
            main(() -> {
                Rect lineBounds = reviewLine(text, index);
                Rect bounds = screenBounds(text); lineBounds.offset(bounds.left, bounds.top);
                require(visibleBounds(text).contains(lineBounds), "Review line cannot be fully reached: " + index);
            });
        }
        receipt.put("reviewStartVisibleWithoutFixtureScroll", true).put("reviewLinesReachable", lines);
        capture("review-end");
    }

    private Rect reviewLine(TextView text, int line) {
        return new Rect(text.getTotalPaddingLeft(), text.getTotalPaddingTop() + text.getLayout().getLineTop(line),
                text.getWidth() - text.getTotalPaddingRight(), text.getTotalPaddingTop() + text.getLayout().getLineBottom(line));
    }

    private Rect screenBounds(View view) {
        int[] location = new int[2]; view.getLocationOnScreen(location);
        return new Rect(location[0], location[1], location[0] + view.getWidth(), location[1] + view.getHeight());
    }

    private Rect visibleBounds(View view) {
        Rect visible = new Rect(), window = new Rect();
        if (!view.getGlobalVisibleRect(visible)) { visible.setEmpty(); return visible; }
        int[] origin = new int[2]; view.getRootView().getLocationOnScreen(origin); visible.offset(origin[0], origin[1]);
        view.getWindowVisibleDisplayFrame(window);
        if (!visible.intersect(window)) visible.setEmpty();
        return visible;
    }

    private void verifyServiceHandoff() throws Exception {
        require(transferFiles().isEmpty(), "Service fixture requires no prior private transfer files");
        HandoffService.LocalBinder handoff = get("handoff", HandoffService.LocalBinder.class);
        NativeRunAuthority run = get("nativeRun", NativeRunAuthority.class);
        click("handoffButton");
        await(() -> "binding_pending".equals(handoff.snapshot().state()), "Lost bind response did not reach real service retry state");
        await(() -> get("choices", LinearLayout.class).getChildCount() == 30, "Frozen contact controls did not finish rendering");
        main(() -> {
            require(handoff.nativeRun() == run && run.state() == NativeRunAuthority.State.BIND_PENDING,
                    "Real service did not retain the approved run");
            require(!get("choices", LinearLayout.class).getChildAt(0).isEnabled() &&
                    !get("clearSelectedContacts", Button.class).isEnabled() && !get("selectAllContacts", Button.class).isEnabled(),
                    "Uncertain binding left selection controls editable");
            require(get("selectedIds", Set.class).equals(new HashSet<>(Arrays.asList(1L, 3L))), "Binding changed the selected IDs");
            require("Retry Continue".contentEquals(get("handoffButton", Button.class).getText()), "Lost bind response has no actual retry action");
            require(accountIntents.get() == 0, "Uncertain binding opened account navigation");
        });
        String frozenFile = requireSingleTransfer("pendingTransfer");
        require(authority.bindCalls == 1, "Uncertain service binding made " + authority.bindCalls + " bind requests instead of one");
        capture("binding-pending");
        click("handoffButton");
        await(() -> "ready".equals(handoff.snapshot().state()), "Exact real service retry did not start the loopback listener");
        require(frozenFile.equals(requireSingleTransfer("boundTransfer")), "Exact service retry replaced the frozen transfer database");
        require(authority.bindRetried && authority.bindCalls == 2, "Bind retry changed its original request");
        main(() -> {
            Intent intent = interceptedAccountIntent.get();
            require(accountIntents.get() == 1 && intent != null && Intent.ACTION_VIEW.equals(intent.getAction()), "Successful Continue did not emit exactly one account intent");
            require((LocalBridge.ACCOUNT_ORIGIN + "/phone/account?continue=reviewed&phoneRunId=" + RUN + "&enrollmentId=" + ENROLLMENT + "&week=phone")
                    .equals(intent.getDataString()), "Real Continue lost account return binding");
            invoke("showHandoff", new Class<?>[]{HandoffService.Snapshot.class}, handoff.snapshot());
            require(accountIntents.get() == 1, "Repeated ready service state launched another account intent");
            require(handoff.recoverSources() != null && handoff.recoverSources().capturedSources() == TransferSourceBinding.CONTACTS,
                    "Service source permission binding differs from the synthetic Contacts store");
        });
        capture("service-ready");
        verifyServiceDelivery(handoff, run);
    }

    private void verifyServiceDelivery(HandoffService.LocalBinder handoff, NativeRunAuthority run) throws Exception {
        JSONObject discovery = responseJson(request("GET", "/v3/discover", "", ""), 200);
        String handoffId = discovery.getString("handoffId"), digest = discovery.getString("payloadSha256");
        require(handoffId.equals(authority.boundHandoff) && digest.equals(authority.boundDigest), "Service listener differs from the immutable bind request");
        require(status(request("GET", "/v2/source/contacts?cursor=0", "", "")) == 403, "Unreleased service exposed a selected page");
        String connect = SetupJson.encode(SetupJson.object("intentId", SyntheticAuthority.INTENT,
                "capability", SyntheticAuthority.CONNECT_CAPABILITY, "handoffId", handoffId, "payloadSha256", digest));
        responseJson(request("POST", "/v3/connect", "Content-Type: application/json\r\n", connect), 200);
        await(() -> "destination_approved".equals(handoff.snapshot().state()), "Approved native run did not reach service destination state");
        String release = SetupJson.encode(SetupJson.object("intentId", SyntheticAuthority.INTENT,
                "capability", SyntheticAuthority.CONNECT_CAPABILITY, "releaseCode", SyntheticAuthority.RELEASE_CODE));
        JSONObject released = responseJson(request("POST", "/v3/release", "Content-Type: application/json\r\n", release), 200);
        require(released.getLong("expiresInSeconds") > 0 && released.getLong("expiresInSeconds") <= 300,
                "Service advertised the review lifetime as pairing expiry");
        JSONObject retried = responseJson(request("POST", "/v3/release", "Content-Type: application/json\r\n", release), 200);
        for (String key : new String[]{"token", "handoffId", "payloadSha256", "manifestJson"})
            require(released.getString(key).equals(retried.getString(key)), "Exact service release retry changed " + key);
        require(authority.verifyCalls == 1 && authority.connectCalls == 1 && authority.consumeCalls == 2,
                "Service release retry did not revalidate synthetic current authority");
        String changed = SetupJson.encode(SetupJson.object("intentId", SyntheticAuthority.INTENT,
                "capability", SyntheticAuthority.CONNECT_CAPABILITY, "releaseCode", syntheticCapability('Z')));
        require(status(request("POST", "/v3/release", "Content-Type: application/json\r\n", changed)) == 403 && authority.consumeCalls == 2,
                "Changed release request reused the service grant");
        String manifest = released.getString("manifestJson");
        require(digest.equals(PagedTransfer.sha256(manifest)) && handoffId.equals(released.getString("handoffId")), "Released transfer lost manifest binding");
        JSONObject manifestFields = new JSONObject(manifest);
        require(manifestFields.getJSONObject("sources").getJSONObject("contacts").getInt("rowsIncluded") == 2 &&
                manifestFields.getJSONObject("sources").getJSONObject("calls").getInt("pageCount") == 0 &&
                manifestFields.getJSONObject("sources").getJSONObject("messages").getInt("pageCount") == 0,
                "Service manifest changed the selected or unavailable source counts");
        JSONArray missing = manifestFields.getJSONArray("missingSources");
        require(missing.length() == 2 && "calls".equals(missing.getString(0)) && "messages".equals(missing.getString(1)),
                "Unread optional sources were reported as observed empty history");
        String authorization = "Authorization: Bearer " + released.getString("token") + "\r\n";
        JSONObject page = responseJson(request("GET", "/v2/source/contacts?cursor=0", authorization, ""), 200);
        String chunk = page.getString("chunk");
        require(PagedTransfer.sha256(chunk).equals(page.getString("pageSha256")) && page.isNull("nextCursor"), "Selected service page integrity changed");
        JSONArray rows = new JSONArray(chunk);
        require(rows.length() == 2 && rows.getJSONObject(0).getLong("sourceId") == 1 && rows.getJSONObject(1).getLong("sourceId") == 3 &&
                "Synthetic setup contact 1".equals(rows.getJSONObject(0).getString("name")) &&
                "Synthetic setup contact 3".equals(rows.getJSONObject(1).getString("name")), "Service delivered unselected or non-synthetic contacts");
        responseJson(request("POST", "/v1/complete", authorization, ""), 200);
        await(() -> "received".equals(handoff.snapshot().state()), "Selected pages did not reach real service received state");
        main(() -> require(!get("savedAckForCurrentReview", Boolean.class) && handoff.accountReturn().completedHandoffId() == null,
                "Local received claimed an account Save"));
        capture("received");
        if (completionFlow) {
            verifyCompletionReturn(handoff, run, authorization, handoffId, digest);
            return;
        }
        click("cancelHandoff");
        await(() -> !handoff.snapshot().active() && run.state() == NativeRunAuthority.State.CLOSED,
                "Cancel did not close service and run authority");
        require(transferFiles().isEmpty() && !PagedTransfer.cleanupRequired(), "Cancel left private frozen transfer files");
        try (Socket socket = new Socket()) {
            try {
                socket.connect(new InetSocketAddress("127.0.0.1", LocalBridge.PORT), 500);
                throw new AssertionError("Cancelled service listener still accepts late connections");
            } catch (ConnectException expected) { /* The actual listener has closed. */ }
        }
        int callsBefore = authority.calls;
        try { run.bind(handoffId, digest); throw new AssertionError("Cancelled run accepted late exact binding"); }
        catch (IllegalArgumentException expected) { /* No authority request may escape after local Cancel. */ }
        require(authority.calls == callsBefore && accountIntents.get() == 1, "Cancel allowed a late authority request or navigation");
        receipt.put("serviceContinue", true).put("serviceBindExactRetry", true).put("serviceSelectionFrozen", true)
                .put("boundContinuationIntentOnce", true).put("serviceReleaseExactRetry", true).put("changedReleaseDenied", true)
                .put("selectedRowsDelivered", rows.length()).put("receivedWithoutSavedAcknowledgment", true)
                .put("cancelRemovedTransferFiles", true).put("cancelClosedListener", true).put("cancelDeniedLateBind", true)
                .put("handoffId", handoffId).put("payloadSha256", digest)
                .put("syntheticVerifyCalls", authority.verifyCalls).put("syntheticConnectCalls", authority.connectCalls)
                .put("syntheticConsumeCalls", authority.consumeCalls);
        capture("stopped");
    }

    private void verifyCompletionReturn(HandoffService.LocalBinder handoff, NativeRunAuthority run,
                                        String authorization, String handoffId, String digest) throws Exception {
        main(() -> require(activity.moveTaskToBack(true), "Synthetic Activity did not enter background"));
        await(() -> !get("started", Boolean.class) && get("handoff", HandoffService.LocalBinder.class) == null,
                "Activity observer remained bound in background");
        String headers = authorization + "Content-Type: application/json\r\n";
        String acknowledgement = "{\"handoffId\":\"" + handoffId + "\",\"payloadSha256\":\"" + digest + "\",\"saved\":true}";
        require(status(request("POST", "/v1/acknowledge-save", headers,
                acknowledgement.replace(digest, repeated('0', 64)))) == 409, "Mismatched save acknowledgement was accepted");
        responseJson(request("POST", "/v1/acknowledge-save", headers, acknowledgement), 200);
        responseJson(request("POST", "/v1/acknowledge-save", headers, acknowledgement), 200);
        require(handoff.snapshot().active(), "Save acknowledgement skipped receipt confirmation");
        String confirmation = "{\"handoffId\":\"" + handoffId + "\",\"payloadSha256\":\"" + digest + "\",\"received\":true}";
        responseJson(request("POST", "/v1/confirm-ack-received", headers, confirmation), 200);
        await(() -> !handoff.snapshot().active() && run.state() == NativeRunAuthority.State.CLOSED,
                "Confirmed completion did not close service authority");
        require(transferFiles().isEmpty() && !PagedTransfer.cleanupRequired(), "Confirmed completion left transfer files");
        main(() -> activity.startActivity(new Intent(activity, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT)));
        await(() -> get("started", Boolean.class) && get("handoff", HandoffService.LocalBinder.class) != null,
                "Activity did not bind after returning");
        await(() -> {
            NativeAccountReturn target = get("nativeAccountReturn", NativeAccountReturn.class);
            return target != null && handoffId.equals(target.completedHandoffId());
        }, "Confirmed presentation was not restored after background completion");
        main(() -> {
            require(!get("reviewed", Boolean.class), "Completion restored local review authority");
            require("Open saved account and report".contentEquals(get("handoffButton", Button.class).getText()),
                    "Completed return still shows the pending handoff action");
            require(get("handoffButton", Button.class).isEnabled(), "Saved report action is disabled");
            require(!get("handoff", HandoffService.LocalBinder.class).snapshot().active(), "Completed display restarted the service");
        });
        // Activity state and two Choreographer callbacks do not mean the system's
        // task-return animation has finished compositing its previous snapshot.
        await(() -> activity.hasWindowFocus(), "Returned Activity did not regain window focus");
        instrumentation.getUiAutomation().waitForIdle(500, 5000);
        main(() -> {
            JSONArray labels = new JSONArray();
            collectVisibleLabels(activity.getWindow().getDecorView(), labels);
            try { receipt.put("confirmedReturnVisibleLabels", labels); }
            catch (org.json.JSONException impossible) { throw new AssertionError(impossible); }
        });
        capture("confirmed-return");
        click("handoffButton");
        main(() -> {
            Intent intent = interceptedAccountIntent.get();
            require(accountIntents.get() == 2 && intent != null &&
                    intent.getDataString().contains("completedHandoffId=" + handoffId) &&
                    intent.getDataString().contains("completedPayloadSha256=" + digest), "Saved report return lost its exact completion binding");
        });
        receipt.put("completionWhileUnbound", true).put("exactAckAndReceipt", true)
                .put("completedReportReturn", true).put("completionRemovedTransferFiles", true)
                .put("restoredCollectionAuthority", false);
    }

    private Set<String> transferFiles() {
        File[] files = activity.getNoBackupFilesDir().listFiles((directory, name) -> name.startsWith("phone-transfer"));
        require(files != null, "Private fixture transfer directory is unavailable");
        Set<String> names = new HashSet<>();
        for (File file : files) names.add(file.getName());
        return names;
    }

    private String requireSingleTransfer(String stage) throws Exception {
        Set<String> files = transferFiles(), bases = new HashSet<>();
        JSONArray names = new JSONArray();
        for (String name : new TreeSet<>(files)) if (names.length() < 8) names.put(name);
        receipt.put(stage, new JSONObject().put("bindCalls", authority.bindCalls)
                .put("fileCount", files.size()).put("files", names));
        require(files.size() > 0 && files.size() <= 4, "Expected one transfer database family at " + stage + "; files=" + names);
        for (String name : files) {
            String base = name.replaceFirst("-(?:wal|shm|journal)$", "");
            require(base.matches("phone-transfer-[0-9a-f-]{36}\\.sqlite") && files.contains(base) &&
                    new File(activity.getNoBackupFilesDir(), name).isFile(),
                    "Unexpected or orphaned transfer file at " + stage + ": " + name);
            bases.add(base);
        }
        require(bases.size() == 1, "Multiple frozen transfer databases at " + stage + "; files=" + names);
        return bases.iterator().next();
    }

    private String request(String method, String path, String headers, String body) throws Exception {
        String response = LocalBridgeInstrumentedTest.request(LocalBridge.PORT, method, path, headers, body);
        loopbackRequests.put(new JSONObject().put("method", method).put("path", path).put("status", status(response)));
        return response;
    }
    private static int status(String response) {
        require(response.length() >= 12 && response.startsWith("HTTP/1.1 "), "Loopback response has no HTTP status");
        return Integer.parseInt(response.substring(9, 12));
    }
    private static JSONObject responseJson(String response, int expected) throws Exception {
        require(status(response) == expected, "Unexpected loopback HTTP status: " + status(response));
        int body = response.indexOf("\r\n\r\n");
        require(body >= 0, "Loopback response has no header boundary");
        return new JSONObject(response.substring(body + 4));
    }

    private void seedSyntheticCollection() {
        collections.incrementAndGet();
        NativeRunAuthority run = get("nativeRun", NativeRunAuthority.class);
        run.beginRead("contacts");
        SanitizedStore store = get("store", SanitizedStore.class);
        store.begin("contacts");
        for (long id = 1; id <= 30; id++) store.contact(id, "Synthetic setup contact " + id, "+1555000" + String.format(java.util.Locale.ROOT, "%04d", id));
        store.commit("contacts", 30, 0);
        set("contactsAvailable", true); set("callsAvailable", false); set("messagesAvailable", false);
        String[] states = get("sourceDispositions", String[].class); states[0] = "Available"; states[1] = states[2] = "Unavailable";
        invoke("showCoverage"); invoke("renderChoices"); invoke("showSelection", new Class<?>[]{boolean.class}, true);
    }

    private void verifyBoundCancellation() throws Exception {
        NativeRunAuthority run = get("nativeRun", NativeRunAuthority.class);
        main(() -> {
            set("nativeContinuePending", true); invoke("openNativeContinuation");
            require(accountIntents.get() == 0, "Unbound Start opened continuation");
            set("nativeContinuePending", false);
        });
        run.bind("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", repeated('a', 64));
        verifyBrowserLaunchDecision();
        authority.block(NativeAuthorityTransport.Route.BIND);
        AtomicReference<Throwable> result = new AtomicReference<>();
        Thread bind = new Thread(() -> {
            try { run.bind("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", repeated('a', 64)); }
            catch (Throwable failure) { result.set(failure); }
        }, "synthetic-bind-response");
        bind.start(); require(authority.entered.await(5, TimeUnit.SECONDS), "Synthetic bind did not start");
        main(() -> { invoke("renderChoices"); invoke("showSelection", new Class<?>[]{boolean.class}, true); });
        await(() -> get("choices", LinearLayout.class).getChildCount() == 30, "Frozen choices did not render");
        main(() -> require(!get("choices", LinearLayout.class).getChildAt(0).isEnabled() &&
                !get("clearSelectedContacts", Button.class).isEnabled(), "Bind attempt left selection editable"));
        click("cancelHandoff");
        authority.release.countDown(); bind.join(5000);
        require(!bind.isAlive() && result.get() instanceof IllegalArgumentException && run.state() == NativeRunAuthority.State.CLOSED,
                "Late bind response recovered cancelled authority");
        receipt.put("bindCancellation", true);
        require(get("nativeAccountReturn", NativeAccountReturn.class).href(false).contains("phoneRunControl=" + RUN),
                "Local cancellation lost the non-secret account control context");
        receipt.put("cancelledAccountControlRetained", true);
    }

    private void verifyBrowserLaunchDecision() throws Exception {
        main(() -> {
            invoke("openNativeContinuation");
            require(accountIntents.get() == 0, "Restored bound state navigated without initiating Continue");
            set("nativeContinuePending", true); set("started", false);
            invoke("openNativeContinuation");
            set("started", true); invoke("openNativeContinuation");
            require(accountIntents.get() == 0, "Background readiness replayed navigation on return");
            set("nativeContinuePending", true); invoke("openNativeContinuation");
            Intent intent = interceptedAccountIntent.get();
            require(accountIntents.get() == 1 && intent != null && Intent.ACTION_VIEW.equals(intent.getAction()), "Bound continuation did not emit one ACTION_VIEW");
            require((LocalBridge.ACCOUNT_ORIGIN + "/phone/account?continue=reviewed&phoneRunId=" + RUN + "&enrollmentId=" + ENROLLMENT + "&week=phone")
                    .equals(intent.getDataString()), "Continuation intent lost its immutable account binding");
            invoke("openNativeContinuation");
            require(accountIntents.get() == 1, "Ready replay opened another browser intent");
        });
        receipt.put("boundContinuationIntentOnce", true).put("backgroundAndReplayNavigationDenied", true);
    }

    private void verifyLateCancellation(NativeAuthorityTransport.Route route) throws Exception {
        if (route == NativeAuthorityTransport.Route.CLAIM) authority.block(route);
        openSetup();
        if (route != NativeAuthorityTransport.Route.CLAIM) {
            awaitApproval();
            if (route == NativeAuthorityTransport.Route.APPROVE) authority.block(route);
            click("setupAgreement"); click("setupAction");
            if (route == NativeAuthorityTransport.Route.START) {
                await(() -> !get("setupBusy", Boolean.class) && get("nativeRun", NativeRunAuthority.class) != null, "Preparation did not settle");
                authority.block(route); click("setupAction");
            }
        }
        require(authority.entered.await(5, TimeUnit.SECONDS), "Expected delayed synthetic route was not called");
        NativeSetupSession session = get("nativeSetup", NativeSetupSession.class);
        click("setupCancel"); authority.release.countDown();
        get("executor", ExecutorService.class).submit(() -> {}).get(5, TimeUnit.SECONDS);
        instrumentation.waitForIdleSync();
        require(session.state() == NativeSetupSession.State.CLOSED && collections.get() == 1, "Late response resumed cancelled setup");
        main(() -> require(!get("setupAction", Button.class).isShown(), "Cancelled setup exposed Start"));
        receipt.put("cancelLate" + route.name(), true);
    }

    private void verifyDiscovery() throws Exception {
        SetupDiscovery discovery = get("setupDiscovery", SetupDiscovery.class);
        require(discovery != null, "Website discovery unavailable");
        try (Socket socket = new Socket("127.0.0.1", discovery.port())) {
            socket.setSoTimeout(3000);
            socket.getOutputStream().write(("GET /v1/setup-capabilities HTTP/1.1\r\nHost: 127.0.0.1:" + discovery.port() +
                    "\r\nOrigin: " + LocalBridge.ACCOUNT_ORIGIN + "\r\n\r\n").getBytes(StandardCharsets.US_ASCII));
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(); byte[] buffer = new byte[1024]; int count;
            while ((count = socket.getInputStream().read(buffer)) != -1) bytes.write(buffer, 0, count);
            String response = bytes.toString(StandardCharsets.UTF_8.name());
            require(response.startsWith("HTTP/1.1 200") && response.contains("android-website") && !response.contains("ownerId"), "Discovery exposed authority or wrong identity");
        }
        require(authority.calls == 0 && collections.get() == 0, "Plain discovery performed account or source work");
        receipt.put("plainLaunchDiscoveryOnly", true);
    }

    private void openSetup() {
        main(() -> {
            invoke("onNewIntent", new Class<?>[]{Intent.class}, new Intent(Intent.ACTION_VIEW,
                    Uri.parse("amplifai-phone://setup?protocol=phone-native-setup-v2&setup=" + SETUP + "&bootstrap=" + repeated('A', 43))));
            require(activity.getIntent().getData() == null, "Activity retained the bootstrap URI");
        });
    }
    private void awaitApproval() throws Exception {
        await(() -> !get("setupBusy", Boolean.class) && get("nativeSetup", NativeSetupSession.class).state() == NativeSetupSession.State.AWAITING_APPROVAL,
                "Claim did not display native approval");
    }
    private void click(String field) throws Exception {
        View view = get(field, View.class); reveal(field, view);
        main(() -> { require(view.isEnabled() && view.isShown(), "Action unavailable: " + field); view.performClick(); });
    }
    private void reveal(String identity, View view) throws Exception {
        settled();
        main(() -> view.requestRectangleOnScreen(new Rect(0, 0, view.getWidth(), view.getHeight()), true));
        settled();
        main(() -> {
            int[] location = new int[2]; view.getLocationOnScreen(location);
            Rect bounds = new Rect(location[0], location[1], location[0] + view.getWidth(), location[1] + view.getHeight());
            Rect visible = new Rect(), window = new Rect();
            boolean shown = view.getGlobalVisibleRect(visible);
            int[] origin = new int[2]; view.getRootView().getLocationOnScreen(origin);
            visible.offset(origin[0], origin[1]); // Global visibility is relative to the root, including on API29.
            view.getWindowVisibleDisplayFrame(window);
            try {
                controls.put(identity, new JSONObject().put("bounds", bounds.toShortString()).put("visible", visible.toShortString())
                        .put("window", window.toShortString()).put("enabled", view.isEnabled()).put("shown", shown)
                        .put("rootX", origin[0]).put("rootY", origin[1]));
            } catch (org.json.JSONException impossible) { throw new AssertionError(impossible); }
            require(shown && visible.equals(bounds), "Control clipped: " + identity + " bounds=" + bounds + " visible=" + visible);
        });
    }
    private void capture(String name) throws Exception {
        settled();
        try {
            Bitmap bitmap = instrumentation.getUiAutomation().takeScreenshot();
            if (bitmap == null) { receipt.put("screenshotUnavailable", true); return; }
            try (FileOutputStream file = new FileOutputStream(new File(output, name + ".png"))) {
                require(bitmap.compress(Bitmap.CompressFormat.PNG, 100, file), "PNG capture failed");
            } finally { bitmap.recycle(); }
            screenshots.put(name + ".png");
        } catch (SecurityException unavailable) { receipt.put("screenshotUnavailable", true); }
    }
    private void collectVisibleLabels(View view, JSONArray labels) {
        if (!view.isShown()) return;
        if (view instanceof TextView text && text.getText().length() > 0) {
            Rect bounds = new Rect();
            if (view.getGlobalVisibleRect(bounds)) {
                try { labels.put(new JSONObject().put("text", text.getText().toString())
                        .put("bounds", bounds.toShortString()).put("alpha", view.getAlpha())); }
                catch (org.json.JSONException impossible) { throw new AssertionError(impossible); }
            }
        }
        if (view instanceof ViewGroup group)
            for (int index = 0; index < group.getChildCount(); index++) collectVisibleLabels(group.getChildAt(index), labels);
    }
    private void settled() throws Exception {
        CountDownLatch frame = new CountDownLatch(1);
        main(() -> activity.getWindow().getDecorView().postOnAnimation(() ->
                activity.getWindow().getDecorView().postOnAnimation(frame::countDown)));
        require(frame.await(3, TimeUnit.SECONDS), "Activity frames did not settle");
        instrumentation.waitForIdleSync();
    }
    private void await(BooleanSupplier condition, String message) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + 7000;
        while (SystemClock.elapsedRealtime() < deadline) {
            AtomicReference<Boolean> ready = new AtomicReference<>(false);
            main(() -> ready.set(condition.getAsBoolean()));
            if (ready.get()) { instrumentation.waitForIdleSync(); return; }
            SystemClock.sleep(20);
        }
        throw new AssertionError(message);
    }
    private void main(Runnable action) {
        AtomicReference<Throwable> failure = new AtomicReference<>();
        instrumentation.runOnMainSync(() -> {
            try { action.run(); }
            catch (RuntimeException | Error caught) { failure.set(caught); }
        });
        if (failure.get() instanceof Error) throw (Error) failure.get();
        if (failure.get() instanceof RuntimeException) throw (RuntimeException) failure.get();
    }
    private <T> T get(String name, Class<T> type) {
        try { Field field = MainActivity.class.getDeclaredField(name); field.setAccessible(true); return type.cast(field.get(activity)); }
        catch (ReflectiveOperationException failure) { throw new AssertionError(failure); }
    }
    private void set(String name, Object value) {
        try { Field field = MainActivity.class.getDeclaredField(name); field.setAccessible(true); field.set(activity, value); }
        catch (ReflectiveOperationException failure) { throw new AssertionError(failure); }
    }
    private void invoke(String name) { invoke(name, new Class<?>[0]); }
    private void invoke(String name, Class<?>[] types, Object... args) {
        try { Method method = MainActivity.class.getDeclaredMethod(name, types); method.setAccessible(true); method.invoke(activity, args); }
        catch (ReflectiveOperationException failure) { throw new AssertionError(failure); }
    }
    private static String repeated(char value, int count) { char[] text = new char[count]; Arrays.fill(text, value); return new String(text); }
    private static String syntheticCapability(char value) {
        byte[] bytes = new byte[32]; Arrays.fill(bytes, (byte) value);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }
    private static void require(boolean accepted, String message) { if (!accepted) throw new AssertionError(message); }

    private static final class SyntheticAuthority implements NativeAuthorityTransport {
        static final String INTENT = "12345678-1234-4234-8234-123456789abc";
        static final String CONNECT_CAPABILITY = syntheticCapability('K');
        static final String RELEASE_CODE = syntheticCapability('R');
        volatile int calls, bindCalls, verifyCalls, connectCalls, consumeCalls;
        volatile boolean approvalRetried, startRetried, bindRetried;
        boolean deliveryEnabled;
        volatile String boundHandoff, boundDigest;
        private Object approvedCapability, startedOperation;
        private byte[] firstBind, firstConsume;
        private boolean loseApproval = true, loseStart = true;
        private final Map<Route, byte[]> retryBodies = new EnumMap<>(Route.class);
        private Map<String, Object> claimed;
        private volatile Route blocked;
        volatile CountDownLatch entered = new CountDownLatch(0), release = new CountDownLatch(0);
        void block(Route route) { entered = new CountDownLatch(1); release = new CountDownLatch(1); blocked = route; }
        @Override public byte[] post(Route route, byte[] request) throws Failure {
            calls++;
            if (route == Route.BIND) bindCalls++;
            Map<String, Object> fields = SetupJson.parse(request, 4096);
            if (route == blocked) {
                blocked = null; entered.countDown();
                try { require(release.await(5, TimeUnit.SECONDS), "Synthetic delayed response timed out"); }
                catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); throw new Failure(true); }
            }
            if (route == Route.CLAIM) { claimed = fields; return wire(claim(fields)); }
            if (route == Route.APPROVE) {
                approvedCapability = fields.get("runCapability");
                if (loseApproval) { loseApproval = false; retryBodies.put(route, request); throw new Failure(true); }
                if (retryBodies.containsKey(route)) { require(Arrays.equals(retryBodies.remove(route), request), "Approval retry changed bytes"); approvalRetried = true; }
                Map<String, Object> result = destination();
                result.putAll(SetupJson.object("protocol", NativeSetupContract.PROTOCOL, "setupId", SETUP, "requestId", fields.get("requestId"),
                        "setupSnapshotSha256", repeated('e', 64), "runId", RUN, "approvalId", APPROVAL, "approvalLocation", "native",
                        "disclosureVersion", NativeSetupContract.DISCLOSURE_VERSION, "disclosureSha256", NativeSetupContract.DISCLOSURE_SHA256,
                        "supplementVersion", NativeSetupContract.SUPPLEMENT_VERSION, "supplementSha256", NativeSetupContract.SUPPLEMENT_SHA256,
                        "client", fields.get("client"), "approvedAt", NOW.toString(), "expiresAt", NOW.plusSeconds(3600).toString(), "preparation", preparation(null)));
                return wire(result);
            }
            if (route == Route.START) {
                startedOperation = fields.get("operationId");
                if (loseStart) { loseStart = false; retryBodies.put(route, request); throw new Failure(true); }
                if (retryBodies.containsKey(route)) { require(Arrays.equals(retryBodies.remove(route), request), "Start retry changed bytes"); startRetried = true; }
                return wire(preparation(SetupJson.object("operationId", fields.get("operationId"), "admittedAt", NOW.toString())));
            }
            if (route == Route.BIND) {
                if (deliveryEnabled) {
                    require(RUN.equals(fields.get("runId")) && approvedCapability.equals(fields.get("capability")) &&
                            APPROVAL.equals(fields.get("expectedApprovalId")) && startedOperation.equals(fields.get("operationId")),
                            "Service binding lost prepared Start authority");
                    if (firstBind == null) {
                        firstBind = request.clone();
                        boundHandoff = (String) fields.get("handoffId"); boundDigest = (String) fields.get("payloadSha256");
                        throw new Failure(true); // Server committed this binding; its first response was lost.
                    }
                    require(Arrays.equals(firstBind, request), "Service bind retry changed bytes");
                    bindRetried = true;
                }
                Map<String, Object> result = destination(); result.remove("ownerId"); result.remove("courseId");
                result.putAll(SetupJson.object("protocol", "phone-start-v1", "approvalId", APPROVAL, "operationId", fields.get("operationId")));
                return wire(result);
            }
            require(deliveryEnabled && bindRetried, "Delivery authority requested without a confirmed synthetic binding");
            require(INTENT.equals(fields.get("intentId")) && boundHandoff.equals(fields.get("handoffId")) &&
                    boundDigest.equals(fields.get("payloadSha256")), "Delivery changed the bound handoff or payload");
            if (route == Route.VERIFY) {
                require(RUN.equals(fields.get("runId")) && approvedCapability.equals(fields.get("capability")) &&
                        APPROVAL.equals(fields.get("expectedApprovalId")), "Currentness proof changed run authority");
                verifyCalls++;
                return wire(SetupJson.object("approvalId", APPROVAL, "enrollmentId", ENROLLMENT,
                        "destinationEmail", "fixture@example.test", "destinationCourse", "Synthetic native setup course",
                        "categories", new ArrayList<>(Arrays.asList("contacts", "calls", "messages"))));
            }
            require(CONNECT_CAPABILITY.equals(fields.get("capability")), "Connect capability differs from synthetic browser grant");
            if (route == Route.CONNECT) {
                connectCalls++;
                return wire(SetupJson.object("destinationEmail", "fixture@example.test", "destinationCourse", "Synthetic native setup course"));
            }
            if (route == Route.CONSUME) {
                require(RELEASE_CODE.equals(fields.get("releaseCode")), "Release code differs from synthetic browser grant");
                if (firstConsume == null) firstConsume = request.clone();
                else require(Arrays.equals(firstConsume, request), "Consumed service release retry changed bytes");
                consumeCalls++;
                return wire(SetupJson.object("consumed", true));
            }
            throw new AssertionError("Unexpected synthetic route: " + route);
        }
        private Map<String, Object> claim(Map<String, Object> request) {
            Map<String, Object> result = destination();
            result.putAll(SetupJson.object("protocol", NativeSetupContract.PROTOCOL, "setupId", SETUP, "requestId", request.get("requestId"),
                    "state", "pending_approval", "client", request.get("client"), "expiresAt", NOW.plusSeconds(600).toString(), "setupSnapshotSha256", repeated('e', 64),
                    "disclosure", SetupJson.object("version", NativeSetupContract.DISCLOSURE_VERSION, "sentences", Arrays.asList(NativeSetupContract.SENTENCE),
                            "agreement", NativeSetupContract.AGREEMENT, "policyVersion", NativeSetupContract.POLICY_VERSION, "policySha256", NativeSetupContract.POLICY_SHA256,
                            "privacyHref", NativeSetupContract.PRIVACY_PATH, "supplementVersion", NativeSetupContract.SUPPLEMENT_VERSION,
                            "supplementSha256", NativeSetupContract.SUPPLEMENT_SHA256, "supplementHref", NativeSetupContract.SUPPLEMENT_PATH,
                            "platform", "android", "channel", "android-website", "sha256", NativeSetupContract.DISCLOSURE_SHA256)));
            return result;
        }
        private Map<String, Object> preparation(Object operation) {
            Map<String, Object> result = destination(); result.remove("ownerId"); result.remove("courseId");
            result.putAll(SetupJson.object("protocol", "phone-start-v1", "approvalId", APPROVAL, "disclosureVersion", NativeSetupContract.DISCLOSURE_VERSION,
                    "expiresAt", NOW.plusSeconds(3600).toString(), "state", "current", "operation", operation)); return result;
        }
        private Map<String, Object> destination() {
            require(claimed != null, "No synthetic claim");
            return SetupJson.object("ownerId", "55555555-5555-4555-8555-555555555555", "enrollmentId", ENROLLMENT,
                    "courseId", "77777777-7777-4777-8777-777777777777", "destinationEmail", "fixture@example.test",
                    "destinationCourse", "Synthetic native setup course", "categories", new ArrayList<>(Arrays.asList("contacts", "calls", "messages")));
        }
        private static byte[] wire(Object fields) { return SetupJson.encode(fields).getBytes(StandardCharsets.UTF_8); }
    }
}
