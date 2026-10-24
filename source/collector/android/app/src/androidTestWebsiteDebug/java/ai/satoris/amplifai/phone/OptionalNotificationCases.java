package ai.satoris.amplifai.phone;

import android.Manifest;
import android.app.Instrumentation;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.os.CancellationSignal;
import android.os.Process;
import android.os.SystemClock;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.ConnectException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.json.JSONObject;

/**
 * Selective T12 cases, never run by the default suite. Synthetic metadata only; no account Save.
 * Require fixture_scope=disposable-emulator, network_isolated=true and emulator hardware.
 * The external harness must disable WAN while preserving loopback: negative UI paths can send
 * production diagnostics. The flag records that precondition; it does not configure networking.
 * Permissions/channel changes and process kills are performed externally by the fixture operator.
 * Reflection seeds private review data and explicit negative bindings; it never grants permissions,
 * sets reviewed=true, supplies authentication authority, or invokes fake lifecycle callbacks.
 */
final class OptionalNotificationCases {
    private static final String CHANNEL = "phone-handoff";
    private static final String MARKER = "t12-instrumentation-process-marker.json";
    private static final long TIMEOUT = 8_000L;
    private static final long CHANGE_TIMEOUT = 45_000L;
    private static final long WATCHDOG_WINDOW = 3_500L; // Production watchdog runs at most 1s apart.
    private final Instrumentation instrumentation;
    private final Bundle arguments;
    private final Context target;

    private OptionalNotificationCases(Instrumentation instrumentation, Bundle arguments) {
        this.instrumentation = instrumentation;
        this.arguments = arguments;
        target = instrumentation.getTargetContext();
    }

    static String run(Instrumentation instrumentation, Bundle arguments) throws Exception {
        OptionalNotificationCases cases = new OptionalNotificationCases(instrumentation, arguments);
        cases.requireFixture();
        return switch (arguments.getString("scenario", "")) {
            case "t12_denied_start" -> cases.unavailableStart("denied");
            case "t12_channel_disabled_start" -> cases.unavailableStart("channel_disabled");
            case "t12_notification_change" -> cases.notificationChange();
            case "t12_cancel_preparation" -> cases.cancelPreparation(false);
            case "t12_background_preparation" -> cases.cancelPreparation(true);
            case "t12_missing_binding_recreation" -> cases.invalidBindingRecreation(false);
            case "t12_stale_binding_recreation" -> cases.invalidBindingRecreation(true);
            case "t12_source_revoke_wait" -> cases.sourceRevoke();
            case "t12_process_kill_seed" -> cases.killSeed("process");
            case "t12_process_kill_reopen" -> cases.killReopen("process");
            case "t12_notification_kill_seed" -> cases.killSeed("notification");
            case "t12_notification_kill_reopen" -> cases.killReopen("notification");
            case "t12_source_revoke_seed" -> cases.killSeed("source");
            case "t12_source_revoke_reopen" -> cases.killReopen("source");
            case "t12_background_return_ack" -> cases.backgroundReturnAck();
            default -> throw new AssertionError("Unknown selective T12 scenario");
        };
    }

    private void requireFixture() {
        require("disposable-emulator".equals(arguments.getString("fixture_scope")),
                "Explicit fixture_scope=disposable-emulator is required");
        require("true".equals(arguments.getString("network_isolated")),
                "Disable fixture WAN, preserve loopback, and explicitly set network_isolated=true");
        String hardware = Build.HARDWARE.toLowerCase(Locale.ROOT);
        require(hardware.equals("ranchu") || hardware.equals("goldfish") ||
                        hardware.equals("cutf_cvm") || hardware.equals("cuttlefish"),
                "Refusing non-emulator or unrecognized hardware");
    }

    private String unavailableStart(String condition) throws Exception {
        try (Fixture fixture = new Fixture(TransferSourceBinding.CONTACTS)) {
            requireNotificationCondition(condition);
            fixture.reviewAndApprove();
            assertStableActive(fixture, WATCHDOG_WINDOW);
            requireNotificationCondition(condition);
            require(!main(() -> fixture.binder.notificationsAvailable()),
                    "Notification-unavailable precondition changed unexpectedly");
            fixture.cancelWithControl();
            return "Actual Activity Review/Approve and foreground handoff stayed active with " + condition +
                    " notifications beyond two watchdog ticks, then its in-app Cancel removed the grant/listener/transfer; synthetic Contacts, no account Save";
        }
    }

    private String notificationChange() throws Exception {
        String condition = notificationCondition();
        try (Fixture fixture = new Fixture(TransferSourceBinding.CONTACTS)) {
            require(main(() -> fixture.binder.notificationsAvailable()),
                    "Start with notifications enabled; change them only after the ready marker");
            fixture.reviewAndApprove();
            String session = fixture.session();
            status("await_notification_change", condition, null);
            await(() -> notificationConditionHolds(condition), CHANGE_TIMEOUT,
                    "No actual Android notification permission/channel change was observed");
            assertStableActive(fixture, WATCHDOG_WINDOW);
            require(session.equals(fixture.session()), "Notification change replaced the service session");
            requireNotificationCondition(condition);
            // Android Settings is foreground during an owner-controlled channel change.
            // Return to the same Activity before invoking its actual in-app Cancel control.
            fixture.foreground();
            fixture.cancelWithControl();
            return "Actual notification change observed in the surviving process; same handoff stayed active beyond two watchdog ticks and its in-app Cancel removed the grant/listener/transfer; no account Save";
        }
    }

    private String cancelPreparation(boolean background) throws Exception {
        try (Fixture fixture = new Fixture(TransferSourceBinding.CONTACTS)) {
            fixture.review();
            ExecutorService executor = main(() -> (ExecutorService) get(fixture.activity, "executor"));
            CountDownLatch entered = new CountDownLatch(1);
            CountDownLatch release = new CountDownLatch(1);
            AtomicReference<Throwable> workerFailure = new AtomicReference<>();
            executor.execute(() -> {
                entered.countDown();
                try {
                    if (!release.await(15, TimeUnit.SECONDS))
                        workerFailure.set(new AssertionError("Preparation barrier was not released"));
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    workerFailure.set(interrupted);
                }
            });
            require(entered.await(5, TimeUnit.SECONDS), "Activity executor did not enter the barrier");
            try {
                fixture.click("handoffButton");
                require(main(() -> (Boolean) get(fixture.activity, "preparingTransfer")),
                        "Approve did not queue actual transfer preparation");
                CancellationSignal signal = main(() -> (CancellationSignal) get(fixture.activity,
                        "transferPreparationCancellation"));
                require(signal != null && !signal.isCanceled(), "Preparation had no live cancellation signal");
                if (background) fixture.background();
                else fixture.click("cancelHandoff");
                require(signal.isCanceled(), "Actual Cancel/onStop did not cancel queued preparation");
            } finally { release.countDown(); }
            await(() -> !main(() -> (Boolean) get(fixture.activity, "preparingTransfer")), TIMEOUT,
                    "Cancelled preparation never settled");
            require(workerFailure.get() == null, "Activity executor barrier failed");
            if (background) fixture.foreground();
            HandoffService.LocalBinder current = awaitBinder(fixture.activity);
            require(!main(() -> current.snapshot().active()), "Cancelled preparation started a service grant");
            require(main(() -> current.snapshot().code()) == null && main(() -> current.recoverSources()) == null,
                    "Cancelled preparation exposed a code or source grant");
            require(!main(() -> (Boolean) get(fixture.activity, "reviewed")), "Cancelled review remained approved");
            require(main(() -> fixture.store.count("contacts")) == 1,
                    "Cancelling preparation destroyed the Activity's local review");
            require(transferFiles().length == 0, "Cancelled preparation left private transfer residue");
            requireListenerClosed();
            return "Executor-latched actual " + (background ? "task background/onStop" : "Cancel control") +
                    " cancelled queued preparation before service startup, preserved local review and removed transfer residue; synthetic data only";
        }
    }

    private String invalidBindingRecreation(boolean stale) throws Exception {
        try (Fixture fixture = new Fixture(TransferSourceBinding.CONTACTS)) {
            fixture.reviewAndApprove();
            File transfer = fixture.transferFile();
            main(() -> {
                set(fixture.service, "sourceBinding", stale
                        ? new TransferSourceBinding(UUID.randomUUID().toString(), TransferSourceBinding.CONTACTS)
                        : null);
                return null;
            });
            fixture.recreate();
            HandoffService.LocalBinder recreated = awaitBinder(fixture.activity);
            await(() -> !main(() -> recreated.snapshot().active()), TIMEOUT,
                    "Recreated Activity accepted a synthetic missing/stale source binding");
            require(main(() -> recreated.snapshot().code()) == null &&
                            main(() -> recreated.recoverSources()) == null,
                    "Invalid binding retained a code or recovered authority");
            HandoffService currentService = (HandoffService) get(recreated, "this$0");
            require(main(() -> get(currentService, "sourceBinding")) == null,
                    "Terminal invalid binding was not cleared");
            require(!transfer.exists(), "Invalid binding left the frozen transfer file");
            require(!main(() -> (Boolean) get(fixture.activity, "savedAckForCurrentReview")),
                    "Invalid binding claimed a saved review");
            requireListenerClosed();
            return "Actual Activity recreation rejected a deliberately synthetic " + (stale ? "stale" : "missing") +
                    " binding and removed listener/transfer; this is not a process-death or real permission-revocation test";
        }
    }

    private String sourceRevoke() throws Exception {
        int source = revokedSource();
        try (Fixture fixture = new Fixture(TransferSourceBinding.CONTACTS | source)) {
            fixture.reviewAndApprove();
            File transfer = fixture.transferFile();
            status("await_source_revoke_survival", sourceName(source), null);
            await(() -> !permissionGranted(source), CHANGE_TIMEOUT,
                    "No actual captured-source permission revoke was observed");
            await(() -> !main(() -> fixture.binder.snapshot().active()), TIMEOUT,
                    "Service survived captured-source revoke with an active grant");
            require(main(() -> fixture.binder.snapshot().code()) == null &&
                            main(() -> fixture.binder.recoverSources()) == null,
                    "Revoked source retained code/binding");
            require(!transfer.exists(), "Revocation left the frozen transfer file");
            requireListenerClosed();
            fixture.recreate();
            HandoffService.LocalBinder recreated = awaitBinder(fixture.activity);
            require(!main(() -> recreated.snapshot().active()) && main(() -> recreated.snapshot().code()) == null &&
                            main(() -> recreated.recoverSources()) == null,
                    "Recreated Activity restored a grant after actual captured-source revocation");
            require(!main(() -> (Boolean) get(fixture.activity, "savedAckForCurrentReview")) &&
                            !main(() -> (Boolean) get(fixture.activity, "reviewed")),
                    "Recreated Activity restored reviewed/saved state after actual revocation");
            requireListenerClosed();
            return "Actual " + sourceName(source) +
                    " permission revoke observed in the surviving process; grant/listener/transfer revoked and actual Activity recreation restored no grant/code/saved state. If Android kills the process, use source_revoke_seed/reopen";
        }
    }

    private String killSeed(String kind) throws Exception {
        String nonce = requiredNonce();
        File marker = markerFile();
        require(!marker.exists(), "A prior test-only kill marker exists; inspect it before resetting the disposable fixture");
        int source = kind.equals("source") ? revokedSource() : TransferSourceBinding.CONTACTS;
        try (Fixture fixture = new Fixture(TransferSourceBinding.CONTACTS | source)) {
            if (kind.equals("notification")) {
                require(Build.VERSION.SDK_INT >= 33, "Notification kill branch requires Android 13+");
                require(target.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED,
                        "Notification kill seed requires actual granted notification permission");
            }
            fixture.reviewAndApprove();
            File review = (File) get(fixture.store, "file");
            File transfer = fixture.transferFile();
            require(review.exists() && transfer.exists(), "Kill seed did not have live private files");
            JSONObject receipt = new JSONObject().put("schema", 1).put("nonce", nonce)
                    .put("kind", kind).put("pid", Process.myPid()).put("source", source)
                    .put("review", review.getCanonicalPath()).put("transfer", transfer.getCanonicalPath());
            try (FileOutputStream output = new FileOutputStream(marker)) {
                output.write(receipt.toString().getBytes(StandardCharsets.UTF_8));
                output.getFD().sync();
            }
            status("await_external_process_kill", kind, nonce);
            // Deliberately do not finish(), stop the Service, call onDestroy(), or clear orphan files.
            // The external harness must kill this target PID (or revoke and observe Android kill).
            SystemClock.sleep(CHANGE_TIMEOUT);
            throw new AssertionError("Seed process was not externally killed; seed alone is never a passing case");
        }
    }

    private String killReopen(String kind) throws Exception {
        String nonce = requiredNonce();
        File marker = markerFile();
        require(marker.isFile() && marker.length() > 0 && marker.length() <= 4096,
                "Missing or oversized test-only process marker");
        JSONObject receipt;
        receipt = new JSONObject(new String(java.nio.file.Files.readAllBytes(marker.toPath()), StandardCharsets.UTF_8));
        require(receipt.getInt("schema") == 1 && kind.equals(receipt.getString("kind")) &&
                        nonce.equals(receipt.getString("nonce")), "Kill marker kind/nonce mismatch");
        require(receipt.getInt("pid") != Process.myPid(),
                "Reopen requires a different PID; recreating Activity or constructing another object is insufficient");
        if (kind.equals("source")) require(!permissionGranted(receipt.getInt("source")),
                "Source-revoke kill reopen requires actual permission denial");
        if (kind.equals("notification")) requireNotificationCondition("denied");
        File review = validatedOldFile(receipt.getString("review"), "review");
        File transfer = validatedOldFile(receipt.getString("transfer"), "transfer");
        require(review.exists() && transfer.exists(),
                "Old live files must exist before cold Activity/Service startup; graceful cleanup is not kill evidence");
        MainActivity activity = launch();
        try {
            HandoffService.LocalBinder binder = awaitBinder(activity);
            require(!main(() -> binder.snapshot().active()) && main(() -> binder.snapshot().code()) == null,
                    "Cold service restored an old grant or code");
            require(main(() -> binder.recoverSources()) == null, "Cold service restored captured-source authority");
            SanitizedStore fresh = main(() -> (SanitizedStore) get(activity, "store"));
            require(fresh != null && fresh.count("contacts") == 0 && fresh.count("calls") == 0 &&
                            fresh.count("messages") == 0, "Cold Activity restored prior review data");
            require(main(() -> ((Set<?>) get(activity, "selectedIds")).isEmpty()) &&
                            !main(() -> (Boolean) get(activity, "reviewed")) &&
                            !main(() -> (Boolean) get(activity, "savedAckForCurrentReview")) &&
                            !main(() -> ((Button) get(activity, "handoffButton")).isEnabled()),
                    "Cold Activity restored selection, review, approval or saved state");
            for (File old : new File[]{review, transfer}) for (String suffix : new String[]{"", "-wal", "-shm", "-journal"})
                require(!new File(old.getPath() + suffix).exists(), "Cold lifecycle retained old private source files");
            requireListenerClosed();
            require(marker.delete(), "Passed kill marker could not be consumed");
            return "Different PID and pre-start orphan files verified; real MainActivity/Service cold startup removed old files and restored no code, source binding, review or saved state. External kill mechanism must be recorded by the harness";
        } finally { main(() -> { activity.finish(); return null; }); }
    }

    private String backgroundReturnAck() throws Exception {
        try (Fixture fixture = new Fixture(TransferSourceBinding.CONTACTS)) {
            fixture.reviewAndApprove();
            String session = fixture.session();
            String code = main(() -> fixture.binder.snapshot().code());
            fixture.background();
            assertStableActive(fixture, WATCHDOG_WINDOW);
            fixture.foreground();
            require(session.equals(fixture.session()) && code.equals(main(() -> fixture.binder.snapshot().code())),
                    "Task return replaced the approved handoff");
            JSONObject pair = json(success(request("POST", "/v1/pair", "Content-Type: application/json\r\n",
                    new JSONObject().put("code", code).toString()), "manual pair"));
            String auth = "Authorization: Bearer " + pair.getString("token") + "\r\n";
            String handoffId = pair.getString("handoffId");
            String digest = pair.getString("payloadSha256");
            require(pair.getBoolean("confirmReceiptRequired") &&
                            digest.equals(PagedTransfer.sha256(pair.getString("manifestJson"))),
                    "Service pairing returned an unbound manifest or skipped receipt confirmation");
            String saved = new JSONObject().put("handoffId", handoffId).put("payloadSha256", digest)
                    .put("saved", true).toString();
            require(request("POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n", saved)
                    .startsWith("HTTP/1.1 409"), "Early saved ACK was accepted");
            assertNotSaved(fixture);
            JSONObject page = json(success(request("GET", "/v2/source/contacts?cursor=0", auth, ""), "contact page"));
            require(page.getString("pageSha256").equals(PagedTransfer.sha256(page.getString("chunk"))),
                    "Service page digest mismatch");
            success(request("POST", "/v1/complete", auth, ""), "delivery completion");
            await(() -> "received".equals(main(() -> fixture.binder.snapshot().state())), TIMEOUT,
                    "Service did not expose received-only state");
            assertNotSaved(fixture);
            String wrong = new JSONObject().put("handoffId", handoffId).put("payloadSha256", "0".repeat(64))
                    .put("saved", true).toString();
            require(request("POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n", wrong)
                    .startsWith("HTTP/1.1 409"), "Wrong manifest ACK was accepted");
            String wrongHandoff = new JSONObject().put("handoffId", UUID.randomUUID().toString())
                    .put("payloadSha256", digest).put("saved", true).toString();
            require(request("POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n", wrongHandoff)
                    .startsWith("HTTP/1.1 409"), "Wrong handoff ACK was accepted");
            assertNotSaved(fixture);
            require(json(success(request("POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n", saved),
                    "bound ACK")).getBoolean("acknowledged"), "Correct bound ACK was not acknowledged");
            assertNotSaved(fixture); // Paged bridge closes only after final receipt confirmation.
            String receipt = new JSONObject().put("handoffId", handoffId).put("payloadSha256", digest)
                    .put("received", true).toString();
            success(request("POST", "/v1/confirm-ack-received", auth + "Content-Type: application/json\r\n", receipt),
                    "final receipt");
            await(() -> "completed".equals(main(() -> fixture.binder.snapshot().state())), TIMEOUT,
                    "Service did not complete after the bound final receipt");
            require(main(() -> (Boolean) get(fixture.activity, "savedAckForCurrentReview")),
                    "Current review did not observe its final receipt");
            return "Actual task background/return retained the service; synthetic loopback client rejected early/wrong ACKs and completed only after matching ACK plus final receipt. No real browser, provider capture, account Save or return-login was exercised";
        }
    }

    private void assertNotSaved(Fixture fixture) throws Exception {
        require(!main(() -> (Boolean) get(fixture.activity, "savedAckForCurrentReview")),
                "Activity claimed saved before bound final receipt");
        require(main(() -> !visibleButton(fixture.activity.getWindow().getDecorView(), "Open saved account and report")),
                "Saved-account result control appeared before bound final receipt");
    }

    private void assertStableActive(Fixture fixture, long duration) throws Exception {
        String session = fixture.session();
        int pid = Process.myPid();
        long until = SystemClock.elapsedRealtime() + duration;
        do {
            require(Process.myPid() == pid && main(() -> fixture.binder.snapshot().active()) &&
                            session.equals(fixture.session()), "Approved handoff stopped or changed while notification was optional");
            Thread.sleep(100);
        } while (SystemClock.elapsedRealtime() < until);
    }

    private final class Fixture implements AutoCloseable {
        MainActivity activity;
        final SanitizedStore store;
        final HandoffService.LocalBinder binder;
        final HandoffService service;

        Fixture(int sources) throws Exception {
            for (int source : new int[]{1, 2, 4}) if ((sources & source) != 0)
                require(permissionGranted(source), "Pregrant actual " + sourceName(source) + " permission on the disposable fixture");
            require(transferFiles().length == 0, "Fixture starts with transfer residue; inspect/reset it first");
            activity = launch();
            binder = awaitBinder(activity);
            service = (HandoffService) get(binder, "this$0");
            require(main(() -> binder.storageAvailable() && !binder.snapshot().active()),
                    "Fixture requires clean, idle service storage");
            store = main(() -> (SanitizedStore) get(activity, "store"));
            require(store != null && store.count("contacts") == 0, "Fixture requires an empty new Activity review store");
            ExecutorService executor = main(() -> (ExecutorService) get(activity, "executor"));
            executor.submit(() -> {
                store.begin("contacts");
                store.contact(1, "T12 synthetic fixture", "+15551234567");
                store.commit("contacts", 1, 0);
                if ((sources & TransferSourceBinding.CALLS) != 0) {
                    store.begin("calls");
                    store.interaction("calls", 1, "+15551234567", 1_750_000_000_000L, "incoming", 10L);
                    store.commit("calls", 1, 0);
                }
                if ((sources & TransferSourceBinding.MESSAGES) != 0) {
                    store.begin("messages");
                    store.interaction("messages", 1, "+15551234567", 1_750_000_000_000L, "incoming", null);
                    store.commit("messages", 1, 0);
                }
            }).get(5, TimeUnit.SECONDS);
            main(() -> {
                set(activity, "contactsAvailable", true);
                set(activity, "callsAvailable", (sources & TransferSourceBinding.CALLS) != 0);
                set(activity, "messagesAvailable", (sources & TransferSourceBinding.MESSAGES) != 0);
                @SuppressWarnings("unchecked") Set<Long> selected = (Set<Long>) get(activity, "selectedIds");
                selected.add(1L);
                Method refresh = MainActivity.class.getDeclaredMethod("refreshActions");
                refresh.setAccessible(true);
                refresh.invoke(activity); // Render synthetic source state; Review/Approve remain real clicks.
                return null;
            });
        }

        void click(String field) throws Exception {
            main(() -> {
                Button button = (Button) get(activity, field);
                require(button != null && button.isEnabled() && button.getVisibility() == View.VISIBLE,
                        "Required actual Activity control is unavailable: " + field);
                require(button.performClick(), "Actual control did not handle click: " + field);
                return null;
            });
        }

        void review() throws Exception {
            click("reviewButton");
            await(() -> main(() -> (Boolean) get(activity, "reviewed")), TIMEOUT,
                    "Actual Review control did not complete local review");
            require(main(() -> ((Button) get(activity, "handoffButton")).isEnabled()),
                    "Actual review did not enable Approve browser handoff");
        }

        void reviewAndApprove() throws Exception {
            review();
            click("handoffButton");
            await(() -> "ready".equals(main(() -> binder.snapshot().state())), TIMEOUT,
                    "Actual Approve control did not start the foreground handoff");
            require(main(() -> binder.snapshot().code()) != null, "Ready service did not expose its one-use code");
        }

        void cancelWithControl() throws Exception {
            click("cancelHandoff");
            await(() -> !main(() -> binder.snapshot().active()), TIMEOUT,
                    "Actual in-app Cancel left an active handoff");
            require(main(() -> binder.snapshot().code()) == null && main(() -> binder.recoverSources()) == null,
                    "Actual in-app Cancel retained its code/source grant");
            require(transferFiles().length == 0, "Actual in-app Cancel left private transfer residue");
            requireListenerClosed();
        }

        String session() throws Exception {
            String session = main(() -> (String) get(service, "sessionId"));
            require(session != null, "No active service session");
            return session;
        }

        File transferFile() throws Exception {
            LocalBridge bridge = main(() -> (LocalBridge) get(service, "bridge"));
            require(bridge != null, "Service bridge is absent");
            PagedTransfer transfer = (PagedTransfer) get(bridge, "transfer");
            require(transfer != null, "Service does not own a frozen paged transfer");
            return (File) get(transfer, "file");
        }

        void background() throws Exception {
            require(main(() -> activity.moveTaskToBack(true)), "Actual Activity task did not move to background");
            await(() -> !main(() -> (Boolean) get(activity, "started")), TIMEOUT,
                    "Actual background transition did not reach onStop");
        }

        void foreground() throws Exception {
            boolean activeBeforeReturn = main(() -> binder.snapshot().active());
            target.startActivity(new Intent(target, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK |
                    Intent.FLAG_ACTIVITY_REORDER_TO_FRONT | Intent.FLAG_ACTIVITY_SINGLE_TOP));
            await(() -> main(() -> (Boolean) get(activity, "started") && get(activity, "handoff") != null),
                    TIMEOUT, "Actual task return did not rebind its Activity");
            if (activeBeforeReturn) require(main(() -> get(activity, "handoff")) == binder,
                    "Task return bound a replacement service while an approved handoff was active");
        }

        void recreate() throws Exception {
            Instrumentation.ActivityMonitor monitor = instrumentation.addMonitor(MainActivity.class.getName(), null, false);
            try {
                MainActivity old = activity;
                main(() -> { old.recreate(); return null; });
                MainActivity next = (MainActivity) instrumentation.waitForMonitorWithTimeout(monitor, TIMEOUT);
                require(next != null && next != old, "Actual Activity recreation did not create a replacement");
                activity = next;
                awaitBinder(activity);
            } finally { instrumentation.removeMonitor(monitor); }
        }

        @Override public void close() throws Exception {
            main(() -> {
                HandoffService.LocalBinder current = activity == null ? null
                        : (HandoffService.LocalBinder) get(activity, "handoff");
                if (current != null) current.cancel(); else binder.cancel();
                if (activity != null) activity.finish();
                return null;
            });
            instrumentation.waitForIdleSync();
        }
    }

    private MainActivity launch() {
        MainActivity activity = (MainActivity) instrumentation.startActivitySync(new Intent(target, MainActivity.class)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK));
        instrumentation.waitForIdleSync();
        return activity;
    }

    private HandoffService.LocalBinder awaitBinder(MainActivity activity) throws Exception {
        await(() -> main(() -> get(activity, "handoff") != null), TIMEOUT, "Actual Activity did not bind HandoffService");
        return main(() -> (HandoffService.LocalBinder) get(activity, "handoff"));
    }

    private int revokedSource() {
        return switch (arguments.getString("source", "")) {
            case "contacts" -> TransferSourceBinding.CONTACTS;
            case "calls" -> TransferSourceBinding.CALLS;
            case "messages" -> TransferSourceBinding.MESSAGES;
            default -> throw new AssertionError("source must explicitly be contacts, calls or messages");
        };
    }

    private boolean permissionGranted(int source) {
        String permission = switch (source) {
            case TransferSourceBinding.CONTACTS -> Manifest.permission.READ_CONTACTS;
            case TransferSourceBinding.CALLS -> Manifest.permission.READ_CALL_LOG;
            case TransferSourceBinding.MESSAGES -> Manifest.permission.READ_SMS;
            default -> throw new AssertionError("Invalid captured source");
        };
        return target.checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    private static String sourceName(int source) {
        return switch (source) {
            case TransferSourceBinding.CONTACTS -> "Contacts";
            case TransferSourceBinding.CALLS -> "Calls";
            case TransferSourceBinding.MESSAGES -> "SMS";
            default -> throw new AssertionError("Invalid captured source");
        };
    }

    private String notificationCondition() {
        String condition = arguments.getString("notification_target", "");
        require(condition.equals("denied") || condition.equals("channel_disabled"),
                "notification_target must explicitly be denied or channel_disabled");
        return condition;
    }

    private boolean notificationConditionHolds(String condition) {
        if (condition.equals("denied")) return Build.VERSION.SDK_INT >= 33 &&
                target.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED;
        NotificationChannel channel = target.getSystemService(NotificationManager.class).getNotificationChannel(CHANNEL);
        return channel != null && channel.getImportance() == NotificationManager.IMPORTANCE_NONE;
    }

    private void requireNotificationCondition(String condition) {
        require(notificationConditionHolds(condition), "Actual notification precondition not met: " + condition);
    }

    private String requiredNonce() {
        String nonce = arguments.getString("fixture_nonce", "");
        require(nonce.matches("[A-Za-z0-9_-]{8,80}"), "fixture_nonce must be a fresh 8-80 character fixture identifier");
        return nonce;
    }

    private File markerFile() { return new File(target.getNoBackupFilesDir(), MARKER); }

    private File validatedOldFile(String path, String kind) throws Exception {
        File file = new File(path).getCanonicalFile();
        require(file.getParentFile().equals(target.getNoBackupFilesDir().getCanonicalFile()) &&
                        file.getName().matches("phone-" + kind + "-[0-9a-f-]{36}\\.sqlite"),
                "Test marker contains a path outside the exact private fixture file contract");
        return file;
    }

    private File[] transferFiles() {
        File[] files = target.getNoBackupFilesDir().listFiles((dir, name) ->
                name.matches("phone-transfer-[0-9a-f-]{36}\\.sqlite(?:-wal|-shm|-journal)?"));
        require(files != null, "Private fixture directory cannot be listed");
        return files;
    }

    private void status(String state, String detail, String nonce) {
        Bundle marker = new Bundle();
        marker.putString("t12_state", state);
        marker.putString("t12_detail", detail);
        marker.putInt("t12_pid", Process.myPid());
        if (nonce != null) marker.putString("t12_nonce", nonce);
        instrumentation.sendStatus(1, marker);
    }

    private static boolean visibleButton(View root, String label) {
        if (root instanceof Button button && label.contentEquals(button.getText())) return button.isShown();
        if (root instanceof ViewGroup group) for (int index = 0; index < group.getChildCount(); index++)
            if (visibleButton(group.getChildAt(index), label)) return true;
        return false;
    }

    private static String request(String method, String path, String headers, String body) throws Exception {
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress("127.0.0.1", LocalBridge.PORT), 3_000);
            socket.setSoTimeout(3_000);
            byte[] payload = body.getBytes(StandardCharsets.UTF_8);
            String request = method + " " + path + " HTTP/1.1\r\nHost: 127.0.0.1:" + LocalBridge.PORT +
                    "\r\nOrigin: " + LocalBridge.ACCOUNT_ORIGIN + "\r\n" + headers +
                    "Content-Length: " + payload.length + "\r\nConnection: close\r\n\r\n";
            socket.getOutputStream().write(request.getBytes(StandardCharsets.US_ASCII));
            socket.getOutputStream().write(payload);
            ByteArrayOutputStream response = new ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int count;
            while ((count = socket.getInputStream().read(buffer)) != -1) {
                require(response.size() + count <= 512 * 1024, "Synthetic loopback response exceeded its test bound");
                response.write(buffer, 0, count);
            }
            return response.toString(StandardCharsets.UTF_8);
        }
    }

    private static void requireListenerClosed() throws Exception {
        try (Socket socket = new Socket()) {
            socket.connect(new InetSocketAddress("127.0.0.1", LocalBridge.PORT), 1_000);
            throw new AssertionError("Old loopback listener still accepts connections");
        } catch (ConnectException expected) { /* A timeout or unrelated socket failure is not closed-listener proof. */ }
    }

    private static String success(String response, String operation) {
        require(response.startsWith("HTTP/1.1 200"), "Synthetic loopback operation failed: " + operation);
        return response;
    }

    private static JSONObject json(String response) throws Exception {
        int boundary = response.indexOf("\r\n\r\n");
        require(boundary >= 0, "Synthetic response has no HTTP body");
        return new JSONObject(response.substring(boundary + 4));
    }

    private interface Checked<T> { T get() throws Exception; }

    private <T> T main(Checked<T> action) throws Exception {
        AtomicReference<T> result = new AtomicReference<>();
        AtomicReference<Throwable> failure = new AtomicReference<>();
        instrumentation.runOnMainSync(() -> {
            try { result.set(action.get()); }
            catch (Throwable problem) { failure.set(problem); }
        });
        Throwable problem = failure.get();
        if (problem instanceof Exception exception) throw exception;
        if (problem instanceof Error error) throw error;
        if (problem != null) throw new AssertionError(problem);
        return result.get();
    }

    private static Object get(Object target, String name) throws Exception {
        Field field = target.getClass().getDeclaredField(name);
        field.setAccessible(true);
        return field.get(target);
    }

    private static void set(Object target, String name, Object value) throws Exception {
        Field field = target.getClass().getDeclaredField(name);
        field.setAccessible(true);
        field.set(target, value);
    }

    private static void await(Checked<Boolean> condition, long timeout, String message) throws Exception {
        long deadline = SystemClock.elapsedRealtime() + timeout;
        do {
            if (condition.get()) return;
            Thread.sleep(40);
        } while (SystemClock.elapsedRealtime() < deadline);
        throw new AssertionError(message);
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
