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
import java.util.function.Consumer;

/** Holds an explicitly reviewed in-memory export while the owner uses the same-phone browser. */
public final class HandoffService extends Service {
    private static final String CHANNEL = "phone-handoff";
    private static final String START = "ai.satoris.amplifai.phone.START_HANDOFF";
    private static final String CANCEL = "ai.satoris.amplifai.phone.CANCEL_HANDOFF";
    private static final String SESSION = "session";
    private static final int NOTIFICATION_ID = 1;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final LocalBinder binder = new LocalBinder();
    private LocalBridge bridge;
    private byte[] pendingPayload;
    private String sessionId;
    private long deadline;
    private Consumer<Snapshot> observer;
    private Snapshot snapshot = new Snapshot("idle", null);

    public record Snapshot(String state, String code) {
        public boolean active() { return state.equals("starting") || state.equals("ready"); }
    }

    public final class LocalBinder extends Binder {
        public void observe(Consumer<Snapshot> listener) {
            observer = listener;
            if (listener != null) listener.accept(snapshot);
        }
        public Snapshot snapshot() { return snapshot; }
        public boolean notificationsAvailable() { return HandoffService.this.notificationsAvailable(); }
        public void approve(byte[] payload) { prepare(payload); }
        public void cancel() { finish(LocalBridge.EndReason.CANCELLED); }
    }

    @Override public void onCreate() {
        super.onCreate();
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

    private void prepare(byte[] payload) {
        if (!notificationsAvailable()) throw new IllegalStateException("handoff_notification_unavailable");
        if (payload.length == 0 || payload.length > 32 * 1024 * 1024) {
            throw new IllegalArgumentException("handoff_payload_size");
        }
        finish(LocalBridge.EndReason.CANCELLED);
        sessionId = UUID.randomUUID().toString();
        // No payload goes into an Intent, saved instance state, a file, or a restart request.
        pendingPayload = Arrays.copyOf(payload, payload.length);
        deadline = SystemClock.elapsedRealtime() + LocalBridge.LIFETIME_MILLIS;
        publish("starting", null);
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
        } else if (START.equals(intent.getAction()) && pendingPayload != null) {
            startReviewedTransfer();
        }
        return START_NOT_STICKY;
    }

    private void startReviewedTransfer() {
        String activeSession = sessionId;
        try {
            if (!notificationsAvailable() || SystemClock.elapsedRealtime() >= deadline) {
                finish(LocalBridge.EndReason.FAILED);
                return;
            }
            Notification notification = notification();
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
            } else {
                startForeground(NOTIFICATION_ID, notification);
            }
            bridge = new LocalBridge(pendingPayload, LocalBridge.ACCOUNT_ORIGIN, LocalBridge.PORT,
                    SystemClock::elapsedRealtimeNanos, reason -> handler.post(() -> {
                        if (activeSession.equals(sessionId)) finish(reason);
                    }));
            clearPendingPayload();
            bridge.start();
            publish("ready", bridge.code());
            handler.post(checkLifetime);
        } catch (IOException | RuntimeException unavailable) {
            finish(LocalBridge.EndReason.FAILED);
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
                .setContentText(getString(R.string.handoff_notification_text))
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
            if (!snapshot.active()) return;
            long remaining = deadline - SystemClock.elapsedRealtime();
            if (remaining <= 0) finish(LocalBridge.EndReason.EXPIRED);
            else if (!notificationsAvailable()) finish(LocalBridge.EndReason.FAILED);
            else handler.postDelayed(this, Math.min(remaining, 1000L));
        }
    };

    private void clearPendingPayload() {
        if (pendingPayload != null) Arrays.fill(pendingPayload, (byte) 0);
        pendingPayload = null;
    }

    private void publish(String state, String code) {
        snapshot = new Snapshot(state, code);
        if (observer != null) observer.accept(snapshot);
    }

    private void finish(LocalBridge.EndReason reason) {
        boolean hadSession = sessionId != null;
        sessionId = null;
        handler.removeCallbacks(checkLifetime);
        clearPendingPayload();
        if (bridge != null) {
            bridge.close(reason);
            bridge = null;
        }
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
        if (hadSession) publish(reason.name().toLowerCase(java.util.Locale.ROOT), null);
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
        super.onDestroy();
    }
}
