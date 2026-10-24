package ai.satoris.amplifai.phone;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.os.Binder;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.SystemClock;

import java.io.IOException;
import java.util.Arrays;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Consumer;

/** Holds a reviewed transfer while the owner uses the same-phone browser. */
public final class HandoffService extends Service {
    private static final String CHANNEL = "phone-handoff";
    private static final String START = "ai.satoris.amplifai.phone.START_HANDOFF";
    private static final String CANCEL = "ai.satoris.amplifai.phone.CANCEL_HANDOFF";
    private static final String SESSION = "session";
    private static final int NOTIFICATION_ID = 1;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final LocalBinder binder = new LocalBinder();
    private LocalBridge bridge;
    private NativeRunAuthority nativeRun;
    private NativeAccountReturn accountReturn;
    private long completionAttempt;
    private final ExecutorService authorityExecutor = Executors.newSingleThreadExecutor();
    private boolean bindingActive;
    private String transferDigest;
    private boolean storageReady;
    private byte[] pendingPayload;
    private PagedTransfer pendingTransfer;
    private long pagesProvided;
    private long pagesTotal;
    private String sessionId;
    // Only an approved, fully frozen PagedTransfer creates this binding. Never restore it from an Intent or Bundle.
    private TransferSourceBinding sourceBinding;
    private long deadline;
    private Consumer<Snapshot> observer;
    private Snapshot snapshot = new Snapshot("idle", null, 0, 0);

    public record Snapshot(String state, String code, long pagesProvided, long pagesTotal) {
        public boolean active() { return state.equals("starting") || state.equals("binding_pending") || state.equals("ready") ||
                state.equals("destination") || state.equals("destination_approved") ||
                state.equals("destination_denied") || state.equals("transferring") || state.equals("received"); }
    }

    public final class LocalBinder extends Binder {
        public void observe(Consumer<Snapshot> listener) {
            if (listener != null) validateActiveSources();
            observer = listener;
            if (listener != null) listener.accept(snapshot);
        }
        public Snapshot snapshot() { return snapshot; }
        public TransferSourceBinding recoverSources() {
            return validateActiveSources() && snapshot.active() ? sourceBinding : null;
        }
        public boolean storageAvailable() { return storageReady; }
        public boolean notificationsAvailable() { return HandoffService.this.notificationsAvailable(); }
        public LocalBridge.Destination pendingDestination() {
            return validateActiveSources() && bridge != null ? bridge.pendingDestination() : null;
        }
        public boolean decideDestination(String intentId, boolean approved) {
            if (!validateActiveSources() || bridge == null || !bridge.decideDestination(intentId, approved)) return false;
            publish(approved ? "destination_approved" : "destination_denied", null);
            refreshNotification();
            return true;
        }
        public void approve(byte[] payload) { prepare(payload); }
        public void approve(PagedTransfer transfer) { prepare(transfer); }
        public void approve(PagedTransfer transfer, NativeRunAuthority run) { prepare(transfer, run); }
        public NativeRunAuthority nativeRun() { return nativeRun; }
        public NativeAccountReturn accountReturn() { return accountReturn; }
        public NativeAccountReturn completedReturn(NativeAccountReturn expected, boolean newSetup) {
            return storageReady ? NativeCompletionState.PROCESS.recover(expected, newSetup || snapshot.active()) : null;
        }
        public void retryBinding() { if (snapshot.state().equals("binding_pending")) bindAndStart(); }
        public void cancel() { finish(LocalBridge.EndReason.CANCELLED); }
    }

    @Override public void onCreate() {
        super.onCreate();
        try {
            // A new service has no live grant. Rebinding an existing service does not run this cleanup.
            PagedTransfer.clearOrphaned(this);
            storageReady = true;
        } catch (RuntimeException cleanupFailed) {
            snapshot = new Snapshot("failed", null, 0, 0);
        }
        NotificationChannel channel = new NotificationChannel(CHANNEL, getString(R.string.handoff_channel),
                NotificationManager.IMPORTANCE_LOW);
        channel.setDescription(getString(R.string.handoff_channel_description));
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
    }

    @Override public IBinder onBind(Intent intent) { return binder; }

    private boolean notificationsAvailable() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        NotificationChannel channel = manager.getNotificationChannel(CHANNEL);
        return manager.areNotificationsEnabled() && channel != null &&
                channel.getImportance() != NotificationManager.IMPORTANCE_NONE &&
                (Build.VERSION.SDK_INT < 33 ||
                        checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED);
    }

    private boolean sourcePermissionGranted(int source) {
        String permission = switch (source) {
            case TransferSourceBinding.CONTACTS -> Manifest.permission.READ_CONTACTS;
            case TransferSourceBinding.CALLS -> Manifest.permission.READ_CALL_LOG;
            case TransferSourceBinding.MESSAGES -> Manifest.permission.READ_SMS;
            default -> null;
        };
        return permission != null && checkSelfPermission(permission) == PackageManager.PERMISSION_GRANTED;
    }

    private boolean validateActiveSources() {
        if (!snapshot.active()) return true;
        if (nativeRun != null && !nativeRun.current()) { finish(LocalBridge.EndReason.CANCELLED); return false; }
        if ((pendingTransfer != null || bridge != null) &&
                TransferSourceBinding.permitted(sourceBinding, sessionId, this::sourcePermissionGranted)) return true;
        // Missing, stale or revoked bindings cannot recover an earlier grant or destination approval.
        finish(LocalBridge.EndReason.CANCELLED);
        if (storageReady) publish("source_unavailable", null);
        return false;
    }

    private void prepare(byte[] payload) {
        if (payload.length == 0 || payload.length > 32 * 1024 * 1024) {
            throw new IllegalArgumentException("handoff_payload_size");
        }
        finish(LocalBridge.EndReason.CANCELLED);
        accountReturn = null;
        if (!storageReady) throw new IllegalStateException("handoff_storage_unavailable");
        completionAttempt = NativeCompletionState.PROCESS.beginAttempt();
        sessionId = UUID.randomUUID().toString();
        pagesProvided = 0;
        pagesTotal = 0;
        // No payload goes into an Intent, saved instance state, a file, or a restart request.
        pendingPayload = Arrays.copyOf(payload, payload.length);
        deadline = SystemClock.elapsedRealtime() + LocalBridge.LIFETIME_MILLIS;
        publish("starting", null);
        if (!validateActiveSources() || sessionId == null)
            throw new IllegalStateException("handoff_source_binding_unavailable");
        try {
            startForegroundService(new Intent(this, HandoffService.class).setAction(START).putExtra(SESSION, sessionId));
        } catch (RuntimeException unavailable) {
            finish(LocalBridge.EndReason.FAILED);
            throw new IllegalStateException("handoff_start_unavailable");
        }
    }

    private void prepare(PagedTransfer transfer) {
        prepare(transfer, null);
    }

    private void prepare(PagedTransfer transfer, NativeRunAuthority run) {
        finish(LocalBridge.EndReason.CANCELLED);
        accountReturn = null;
        if (!storageReady) throw new IllegalStateException("handoff_storage_unavailable");
        if (run != null && (run.state() != NativeRunAuthority.State.STARTED || !run.current()))
            throw new IllegalStateException("handoff_run_unavailable");
        if (run != null) {
            int allowed = TransferSourceBinding.CONTACTS;
            if (run.approval().scope().categories().contains("calls")) allowed |= TransferSourceBinding.CALLS;
            if (run.approval().scope().categories().contains("messages")) allowed |= TransferSourceBinding.MESSAGES;
            if ((transfer.capturedSources() & ~allowed) != 0) throw new IllegalStateException("handoff_run_sources_changed");
        }
        String nextSession = UUID.randomUUID().toString();
        TransferSourceBinding nextBinding = new TransferSourceBinding(nextSession, transfer.capturedSources());
        if (!TransferSourceBinding.permitted(nextBinding, nextSession, this::sourcePermissionGranted))
            throw new IllegalStateException("handoff_source_permission_changed");
        completionAttempt = NativeCompletionState.PROCESS.beginAttempt();
        sessionId = nextSession;
        sourceBinding = nextBinding;
        pagesProvided = 0;
        pagesTotal = (long) transfer.pageCount("contacts") + transfer.pageCount("calls") + transfer.pageCount("messages");
        pendingTransfer = transfer;
        nativeRun = run;
        if (run != null) accountReturn = new NativeAccountReturn(run.approval().runId(), run.approval().scope().enrollmentId(), null, null);
        transferDigest = transfer.manifestSha256();
        deadline = SystemClock.elapsedRealtime() + LocalBridge.LIFETIME_MILLIS;
        publish("starting", null);
        if (!validateActiveSources() || sessionId == null)
            throw new IllegalStateException("handoff_source_binding_unavailable");
        try {
            startForegroundService(new Intent(this, HandoffService.class).setAction(START).putExtra(SESSION, sessionId));
        } catch (RuntimeException unavailable) {
            finish(LocalBridge.EndReason.FAILED);
            throw new IllegalStateException("handoff_start_unavailable");
        }
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null || sessionId == null) {
            finish(LocalBridge.EndReason.CANCELLED);
            return START_NOT_STICKY;
        }
        if (!sessionId.equals(intent.getStringExtra(SESSION))) return START_NOT_STICKY;
        if (CANCEL.equals(intent.getAction())) {
            finish(LocalBridge.EndReason.CANCELLED);
        } else if (START.equals(intent.getAction()) && (pendingPayload != null || pendingTransfer != null)) {
            startReviewedTransfer();
        }
        return START_NOT_STICKY;
    }

    private void startReviewedTransfer() {
        String activeSession = sessionId;
        try {
            if (!validateActiveSources()) return;
            if (SystemClock.elapsedRealtime() >= deadline) {
                finish(LocalBridge.EndReason.FAILED);
                return;
            }
            // Required even when POST_NOTIFICATIONS or the channel is disabled. A real promotion
            // failure is handled below before any listener/code becomes available.
            Notification notification = notification();
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
            } else {
                startForeground(NOTIFICATION_ID, notification);
            }
            Consumer<LocalBridge.EndReason> closed = reason -> handler.post(() -> {
                if (activeSession.equals(sessionId)) finish(reason);
            });
            Runnable received = () -> handler.post(() -> {
                if (activeSession.equals(sessionId)) {
                    publish("received", null);
                    refreshNotification();
                }
            });
            bridge = pendingTransfer != null
                    ? new LocalBridge(pendingTransfer, LocalBridge.ACCOUNT_ORIGIN, LocalBridge.PORT,
                    SystemClock::elapsedRealtimeNanos, closed, received, pageProgress -> handler.post(() -> {
                        if (!activeSession.equals(sessionId) || !snapshot.active()) return;
                        pagesProvided = Math.max(pagesProvided, pageProgress[0]);
                        pagesTotal = pageProgress[1];
                        publish("transferring", null);
                        refreshNotification();
                    }), nativeRun == null ? new AccountConnectProof() : nativeRun, () -> handler.post(() -> {
                        if (!activeSession.equals(sessionId) || bridge == null) return;
                        if (nativeRun == null && bridge.pendingDestination() == null) return;
                        publish(nativeRun == null ? "destination" : "destination_approved", null);
                        refreshNotification();
                    }))
                    : new LocalBridge(pendingPayload, LocalBridge.ACCOUNT_ORIGIN, LocalBridge.PORT,
                    SystemClock::elapsedRealtimeNanos, reason -> handler.post(() -> {
                        if (activeSession.equals(sessionId)) finish(reason);
                    }), () -> handler.post(() -> {
                        if (activeSession.equals(sessionId)) {
                            publish("received", null);
                            refreshNotification();
                        }
                    }));
            pendingTransfer = null;
            clearPendingPayload();
            handler.post(checkLifetime);
            if (nativeRun != null) bindAndStart();
            else { bridge.start(); publish("ready", bridge.code()); }
        } catch (IOException | RuntimeException unavailable) {
            finish(LocalBridge.EndReason.FAILED);
        }
    }

    private void bindAndStart() {
        if (bindingActive || nativeRun == null || bridge == null || !validateActiveSources()) return;
        bindingActive = true;
        String activeSession = sessionId;
        NativeRunAuthority run = nativeRun;
        LocalBridge waiting = bridge;
        String digest = transferDigest;
        publish("starting", null);
        authorityExecutor.execute(() -> {
            boolean retryable = false;
            boolean success = false;
            try { run.bind(waiting.handoffId(), digest); success = true; }
            catch (NativeAuthorityTransport.Failure unavailable) { retryable = unavailable.retryable; }
            catch (IllegalArgumentException stopped) { /* No current run authority. */ }
            boolean bound = success, retry = retryable;
            handler.post(() -> {
                if (!activeSession.equals(sessionId) || waiting != bridge) return;
                bindingActive = false;
                if (!validateActiveSources()) return;
                if (bound) { waiting.start(); publish("ready", null); }
                else if (retry) publish("binding_pending", null);
                else finish(LocalBridge.EndReason.FAILED);
            });
        });
    }

    private void refreshNotification() {
        // Drawer visibility is optional. Promotion above must still supply a valid FGS notification.
        // A permission change between this check and notify must not revoke an approved transfer.
        try {
            if (notificationsAvailable())
                getSystemService(NotificationManager.class).notify(NOTIFICATION_ID, notification());
        } catch (SecurityException visibilityChanged) {
            // In-app Cancel and destination confirmation remain available through Recent apps.
        }
    }

    private Notification notification() {
        Intent cancel = new Intent(this, HandoffService.class).setAction(CANCEL).putExtra(SESSION, sessionId);
        PendingIntent cancelAction = PendingIntent.getService(this, 0, cancel,
                PendingIntent.FLAG_CANCEL_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent open = PendingIntent.getActivity(this, 0,
                new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_upload)
                .setContentTitle(getString(R.string.handoff_notification_title))
                .setContentText(snapshot.state().equals("transferring")
                        ? "Pages offered to browser: " + snapshot.pagesProvided() + " / " + snapshot.pagesTotal()
                        : nativeRun != null ? "Selected metadata for the approved account. Return to the app to check status or Cancel."
                        : snapshot.state().equals("destination")
                        ? "Open the app to confirm the signed-in account and course."
                        : getString(snapshot.state().equals("received")
                        ? R.string.handoff_notification_received_text : R.string.handoff_notification_text))
                .setContentIntent(open)
                .setDeleteIntent(cancelAction)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setVisibility(Notification.VISIBILITY_PRIVATE)
                .addAction(new Notification.Action.Builder(null, getString(R.string.handoff_cancel), cancelAction).build());
        if (Build.VERSION.SDK_INT >= 31) builder.setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE);
        return builder.build();
    }

    private final Runnable checkLifetime = new Runnable() {
        @Override public void run() {
            if (!snapshot.active() || !validateActiveSources()) return;
            long remaining = bridge == null ? deadline - SystemClock.elapsedRealtime() : bridge.remainingMillis();
            if (remaining <= 0) finish(LocalBridge.EndReason.EXPIRED);
            else handler.postDelayed(this, Math.min(remaining, 1000L));
        }
    };

    private void clearPendingPayload() {
        if (pendingPayload != null) Arrays.fill(pendingPayload, (byte) 0);
        pendingPayload = null;
        PagedTransfer closing = pendingTransfer;
        pendingTransfer = null;
        if (closing != null) closing.close();
    }

    private void publish(String state, String code) {
        snapshot = new Snapshot(state, code, pagesProvided, pagesTotal);
        if (observer != null) observer.accept(snapshot);
    }

    private void finish(LocalBridge.EndReason reason) {
        boolean hadSession = sessionId != null;
        NativeAccountReturn completedReturn = hadSession && reason == LocalBridge.EndReason.COMPLETED &&
                accountReturn != null && bridge != null ? accountReturn.completed(bridge.handoffId(), transferDigest) : null;
        sessionId = null;
        sourceBinding = null;
        bindingActive = false;
        transferDigest = null;
        NativeRunAuthority closingRun = nativeRun;
        nativeRun = null;
        if (closingRun != null) closingRun.close();
        handler.removeCallbacks(checkLifetime);
        boolean cleanupFailed = false;
        try { clearPendingPayload(); }
        catch (RuntimeException unavailable) { cleanupFailed = true; }
        LocalBridge closing = bridge;
        bridge = null;
        try { if (closing != null) closing.close(reason); }
        catch (RuntimeException unavailable) { cleanupFailed = true; }
        finally {
            stopForeground(STOP_FOREGROUND_REMOVE);
            stopSelf();
        }
        if (cleanupFailed) storageReady = false;
        if (!cleanupFailed && completedReturn != null) accountReturn = completedReturn;
        if (hadSession) NativeCompletionState.PROCESS.finished(completionAttempt, reason, !cleanupFailed, completedReturn);
        if (hadSession) publish(cleanupFailed ? "failed" : reason.name().toLowerCase(java.util.Locale.ROOT), null);
    }

    @Override public void onTaskRemoved(Intent rootIntent) {
        finish(LocalBridge.EndReason.CANCELLED);
        super.onTaskRemoved(rootIntent);
    }

    @Override public void onTimeout(int startId, int fgsType) {
        finish(LocalBridge.EndReason.EXPIRED);
    }

    @Override public void onDestroy() {
        finish(LocalBridge.EndReason.CANCELLED);
        observer = null;
        authorityExecutor.shutdown();
        super.onDestroy();
    }
}
