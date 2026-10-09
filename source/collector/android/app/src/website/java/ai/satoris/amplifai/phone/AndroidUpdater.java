package ai.satoris.amplifai.phone;

import android.app.Activity;
import android.app.PendingIntent;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.os.Build;
import android.provider.Settings;
import android.net.Uri;
import android.view.View;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.File;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.function.BooleanSupplier;

/** Launch discovery and a guarded OS package update; it never reads phone sources. */
final class AndroidUpdater {
    private static final int BG = Color.rgb(15, 21, 37);
    private static final int TEXT = Color.rgb(237, 239, 243);
    private static final int MUTED = Color.rgb(200, 207, 217);
    private static final int ACCENT = Color.rgb(166, 230, 61);
    private static final String PREFS = "android-update-choice";
    private final Activity activity;
    private final BooleanSupplier safeToInstall;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final TextView state;
    private final Button action;
    private final Button permission;
    private final CheckBox automatic;
    private final SharedPreferences updateStatus;
    private final SharedPreferences.OnSharedPreferenceChangeListener statusListener;
    private final int installedCode;
    private final String installedName;
    private Future<?> checkTask;
    private Future<?> installTask;
    private AndroidUpdateFeed.Release release;
    private volatile boolean started;
    private boolean preparing;
    private int checkGeneration;
    private volatile int installGeneration;
    private volatile boolean cancelRequested;
    private volatile boolean closed;

    AndroidUpdater(Activity activity, LinearLayout parent, BooleanSupplier safeToInstall) {
        this.activity = activity;
        this.safeToInstall = safeToInstall;
        PackageInfo installed;
        try { installed = activity.getPackageManager().getPackageInfo(activity.getPackageName(), 0); }
        catch (PackageManager.NameNotFoundException impossible) { throw new IllegalStateException(impossible); }
        installedCode = UpdateInstaller.archiveVersionCode(installed);
        installedName = installed.versionName;
        updateStatus = activity.getSharedPreferences("android-update-status", Activity.MODE_PRIVATE);
        statusListener = (preferences, key) -> {
            if ("status".equals(key) && !closed)
                activity.runOnUiThread(this::refreshPending);
        };
        updateStatus.registerOnSharedPreferenceChangeListener(statusListener);
        TextView heading = label("Companion updates", 18, TEXT);
        heading.setPadding(0, dp(14), 0, dp(2));
        parent.addView(heading);
        parent.addView(label("Installed version " + installedName, 12, MUTED));
        state = label("Checking for an update to " + installedName + "…", 13, MUTED);
        parent.addView(state);
        automatic = new CheckBox(activity);
        automatic.setText("Install verified updates automatically when local work is finished. Android may still ask me to approve.");
        automatic.setTextColor(TEXT);
        automatic.setChecked(activity.getSharedPreferences(PREFS, Activity.MODE_PRIVATE)
                .getBoolean("automatic", false));
        automatic.setOnCheckedChangeListener((view, checked) -> {
            activity.getSharedPreferences(PREFS, Activity.MODE_PRIVATE).edit().putBoolean("automatic", checked).apply();
            if (checked) maybeAutomatic();
        });
        parent.addView(automatic);
        action = button("Check update status", parent);
        action.setOnClickListener(view -> {
            if (preparing) {
                cancelRequested = true;
                installGeneration++;
                preparing = false;
                if (installTask != null) installTask.cancel(true);
                show("cancelled", "Update preparation cancelled. Phone review remains available.");
                return;
            }
            if (UpdateInstallReceiver.pending(activity)) {
                PendingIntent approval = UpdateInstallReceiver.pendingApproval(activity);
                if (approval != null && safeToInstall.getAsBoolean()) {
                    try { approval.send(); }
                    catch (PendingIntent.CanceledException unavailable) {
                        UpdateInstallReceiver.approvalUnavailable(activity);
                    }
                }
                refreshPending();
                return;
            }
            if (release == null) { check(); return; }
            requestInstall(false);
        });
        permission = button("Allow AMPLIFai Phone to request app updates", parent);
        permission.setVisibility(View.GONE);
        permission.setOnClickListener(view -> {
            Intent settings = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + activity.getPackageName()));
            activity.startActivity(settings);
        });
        cleanupAbandonedDownloads();
        refreshPending();
    }

    void onStart() { started = true; check(); }

    void onResume() { refreshPending(); maybeAutomatic(); }

    void onStop() {
        started = false;
        checkGeneration++;
        installGeneration++;
        if (checkTask != null) checkTask.cancel(true);
        checkTask = null;
        if (preparing) {
            cancelRequested = true;
            if (installTask != null) installTask.cancel(true);
            preparing = false;
        }
    }

    void close() {
        closed = true;
        onStop();
        updateStatus.unregisterOnSharedPreferenceChangeListener(statusListener);
        worker.shutdownNow();
    }

    boolean blocksCollection() { return preparing || UpdateInstallReceiver.pending(activity); }

    void onSafetyChanged() { if (started) { refreshPending(); maybeAutomatic(); } }

    private void check() {
        if (!started || closed || preparing || UpdateInstallReceiver.pending(activity) ||
                checkTask != null && !checkTask.isDone()) return;
        release = null;
        show("checking", "Checking for an update to " + installedName + "…");
        int generation = ++checkGeneration;
        checkTask = worker.submit(() -> {
            try {
                byte[] wire = UpdateTransport.feed(() -> !started || closed);
                byte[] certificate;
                try (InputStream input = activity.getAssets().open("android_update_publisher.der")) {
                    ByteArrayOutputStream output = new ByteArrayOutputStream(1200);
                    byte[] block = new byte[1200];
                    int count;
                    while ((count = input.read(block)) != -1) {
                        if (output.size() + count > 2048) throw new IOException("update_certificate_size");
                        output.write(block, 0, count);
                    }
                    certificate = output.toByteArray();
                }
                AndroidUpdateFeed.Release found = AndroidUpdateFeed.parse(wire, certificate);
                activity.runOnUiThread(() -> {
                    if (!started || closed || generation != checkGeneration) return;
                    release = found;
                    switch (AndroidUpdateFeed.decide(found, installedCode, Build.VERSION.SDK_INT)) {
                        case CURRENT -> show("current", installedName + " is current.");
                        case INCOMPATIBLE -> show("incompatible", found.versionName() +
                                " needs a newer Android version. " + installedName + " remains usable.");
                        case AVAILABLE -> {
                            show("available", "Verified update " + found.versionName() + " is available.");
                            maybeAutomatic();
                        }
                    }
                });
            } catch (IOException | IllegalArgumentException failed) {
                activity.runOnUiThread(() -> {
                    if (started && !closed && generation == checkGeneration)
                        show("failed", "Update check unavailable. Phone review still works; try again later.");
                });
            }
        });
    }

    private void maybeAutomatic() {
        if (release == null || !automatic.isChecked() || !started || preparing ||
                UpdateInstallReceiver.pending(activity) ||
                release.versionCode() <= UpdateInstallReceiver.confirmedTarget(activity) ||
                AndroidUpdateFeed.decide(release, installedCode, Build.VERSION.SDK_INT) != AndroidUpdateFeed.Decision.AVAILABLE)
            return;
        if (!safeToInstall.getAsBoolean()) {
            show("deferred", "Update " + release.versionName() + " waits for local review and account confirmation to finish.");
            return;
        }
        if (!activity.getPackageManager().canRequestPackageInstalls()) {
            show("available", "Update " + release.versionName() + " is available. Android must first allow updates from this app.");
            return;
        }
        requestInstall(true);
    }

    private void requestInstall(boolean automaticRequest) {
        if (!started || closed || release == null || preparing || UpdateInstallReceiver.pending(activity)) return;
        if (AndroidUpdateFeed.decide(release, installedCode, Build.VERSION.SDK_INT) != AndroidUpdateFeed.Decision.AVAILABLE) return;
        if (!safeToInstall.getAsBoolean()) {
            show("deferred", "Finish or discard the private local review before updating.");
            return;
        }
        if (!activity.getPackageManager().canRequestPackageInstalls()) {
            show("available", "Allow app updates in Android Settings, then return here.");
            permission.setVisibility(View.VISIBLE);
            return;
        }
        preparing = true;
        cancelRequested = false;
        int generation = ++installGeneration;
        AndroidUpdateFeed.Release target = release;
        show("preparing", "Verifying " + target.versionName() + " before Android installs it…");
        installTask = worker.submit(() -> {
            File apk = null;
            UpdateInstaller.Staged staged = null;
            try {
                apk = UpdateTransport.apk(target, activity.getNoBackupFilesDir(),
                        () -> cancelRequested || generation != installGeneration || !started || closed);
                if (cancelRequested || generation != installGeneration || !started || closed)
                    throw new IOException("update_cancelled");
                staged = UpdateInstaller.stage(activity, target, apk, installedCode,
                        automaticRequest && Build.VERSION.SDK_INT >= 31);
                UpdateInstaller.Staged ready = staged;
                activity.runOnUiThread(() -> {
                    if (closed || !started || cancelRequested || generation != installGeneration ||
                            !safeToInstall.getAsBoolean() ||
                            !activity.getPackageManager().canRequestPackageInstalls()) {
                        UpdateInstaller.discard(ready);
                        if (generation == installGeneration) {
                            preparing = false;
                            show("deferred", "Update deferred. The local review remains available.");
                        }
                        return;
                    }
                    try {
                        UpdateInstaller.commit(activity, ready, target.versionCode());
                        preparing = false;
                        show("installing", "Android is applying the verified update. Approve its prompt if shown.");
                    } catch (RuntimeException failed) {
                        preparing = false;
                        show("failed", "Android could not start the update. Your phone review remains available.");
                    }
                });
            } catch (IOException | RuntimeException failed) {
                if (staged != null) UpdateInstaller.discard(staged);
                activity.runOnUiThread(() -> {
                    if (closed || generation != installGeneration) return;
                    preparing = false;
                    show("failed", cancelRequested || !started ? "Update preparation cancelled. Phone review is unchanged."
                            : "Update verification failed. No APK was installed; phone review is unchanged.");
                });
            } finally { if (apk != null) apk.delete(); }
        });
    }

    private void refreshPending() {
        UpdateInstallReceiver.reconcile(activity, installedCode);
        if (UpdateInstallReceiver.pending(activity)) {
            if ("approval_unavailable".equals(UpdateInstallReceiver.status(activity))) {
                show("installing", "Android approval could not be restored. No local collection will start while its installation remains pending.");
            } else if (UpdateInstallReceiver.pendingApproval(activity) != null) {
                show("approval", "Android requires your approval for this update. Continue here to see its installer prompt.");
            } else {
                show("installing", "Android update approval or installation is pending. No local collection will start.");
            }
        } else if ("cancelled".equals(UpdateInstallReceiver.status(activity))) {
            show("cancelled", "Android update cancelled. Phone review remains available.");
        } else if ("failed".equals(UpdateInstallReceiver.status(activity))) {
            show("failed", "Android did not install the update. Phone review is available; check again when ready.");
        }
    }

    private void show(String next, String detail) {
        state.setText(detail);
        permission.setVisibility("available".equals(next) && release != null &&
                !activity.getPackageManager().canRequestPackageInstalls() ? View.VISIBLE : View.GONE);
        action.setVisibility("current".equals(next) || "incompatible".equals(next) ? View.GONE : View.VISIBLE);
        action.setEnabled(!"checking".equals(next) && !"installing".equals(next) &&
                !"incompatible".equals(next) && (!"approval".equals(next) || safeToInstall.getAsBoolean()));
        action.setText(switch (next) {
            case "approval" -> "Continue Android approval";
            case "preparing" -> "Cancel update preparation";
            case "failed" -> release == null ? "Check for update again" : "Retry verified update";
            case "cancelled" -> release == null ? "Check for update again" : "Install verified update";
            case "deferred" -> "Update after local review";
            case "available" -> "Install verified update";
            default -> "Check update status";
        });
    }

    private void cleanupAbandonedDownloads() {
        File[] files = activity.getNoBackupFilesDir().listFiles((directory, name) ->
                name.startsWith("android-update-") && name.endsWith(".apk"));
        if (files != null) for (File file : files) if (!file.delete()) file.deleteOnExit();
    }

    private int dp(int amount) { return Math.round(amount * activity.getResources().getDisplayMetrics().density); }

    private TextView label(String value, int size, int color) {
        TextView text = new TextView(activity);
        text.setText(value);
        text.setTextSize(size);
        text.setTextColor(color);
        text.setPadding(0, dp(4), 0, dp(4));
        return text;
    }

    private Button button(String title, LinearLayout parent) {
        Button button = new Button(activity);
        button.setText(title);
        button.setAllCaps(false);
        button.setTextColor(BG);
        button.setBackgroundTintList(android.content.res.ColorStateList.valueOf(ACCENT));
        parent.addView(button);
        return button;
    }
}
