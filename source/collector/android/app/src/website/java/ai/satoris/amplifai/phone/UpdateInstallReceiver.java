package ai.satoris.amplifai.phone;

import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInstaller;
import android.net.Uri;
import android.os.Build;

import java.util.UUID;

/** Receives only this app's OS installation session result, including required user action. */
public final class UpdateInstallReceiver extends BroadcastReceiver {
    private static final String ACTION = "ai.satoris.amplifai.phone.UPDATE_INSTALL_RESULT";
    private static final String APPROVAL = "ai.satoris.amplifai.phone.UPDATE_APPROVAL";
    private static final String PREFS = "android-update-status";
    private static final String SESSION = "session";
    private static final String NONCE = "nonce";
    private static final String TARGET = "target";
    private static final String CONFIRMED_TARGET = "confirmed_target";
    private static final String STATUS = "status";

    static PendingIntent pendingIntent(Context context, int session, int target) {
        String nonce = UUID.randomUUID().toString();
        if (!context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putInt(SESSION, session).putString(NONCE, nonce).putInt(TARGET, target)
                .putString(STATUS, "installing").commit())
            throw new IllegalStateException("update_state_storage");
        Intent callback = new Intent(context, UpdateInstallReceiver.class).setAction(ACTION)
                .putExtra(SESSION, session).putExtra(NONCE, nonce);
        return PendingIntent.getBroadcast(context, session, callback,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_MUTABLE);
    }

    static boolean pending(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt(SESSION, -1) >= 0;
    }

    static String status(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(STATUS, "");
    }

    static int confirmedTarget(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt(CONFIRMED_TARGET, -1);
    }

    static void clear(Context context, String result) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        PendingIntent approval = pendingApproval(context);
        if (approval != null) approval.cancel();
        SharedPreferences.Editor change = prefs.edit().remove(SESSION).remove(NONCE)
                .remove(TARGET).putString(STATUS, result);
        if ("current".equals(result)) change.putInt(CONFIRMED_TARGET, prefs.getInt(TARGET, -1));
        change.commit();
    }

    static PendingIntent pendingApproval(Context context) {
        int session = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt(SESSION, -1);
        if (session < 0) return null;
        return PendingIntent.getActivity(context, session, approvalProxy(context, session),
                PendingIntent.FLAG_NO_CREATE | PendingIntent.FLAG_IMMUTABLE);
    }

    static boolean matchesApproval(Context context, int session, String nonce) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        return session >= 0 && session == prefs.getInt(SESSION, -1) && nonce != null &&
                nonce.equals(prefs.getString(NONCE, "")) &&
                "awaiting_android_approval".equals(prefs.getString(STATUS, ""));
    }

    static void approvalUnavailable(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString(STATUS, "approval_unavailable").commit();
    }

    private static Intent approvalProxy(Context context, int session) {
        return new Intent(context, UpdateApprovalActivity.class).setAction(APPROVAL)
                .setData(Uri.parse("amplifai-update://approval/" + session));
    }

    static void reconcile(Context context, int installedCode) {
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        int session = prefs.getInt(SESSION, -1);
        if (session < 0) return;
        if (installedCode >= prefs.getInt(TARGET, Integer.MAX_VALUE)) {
            clear(context, "current");
        }
    }

    @Override public void onReceive(Context context, Intent intent) {
        if (intent == null || !ACTION.equals(intent.getAction())) return;
        SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        if (prefs.getInt(SESSION, -1) != intent.getIntExtra(SESSION, -2) ||
                !prefs.getString(NONCE, "").equals(intent.getStringExtra(NONCE))) return;
        int result = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
        if (result == PackageInstaller.STATUS_PENDING_USER_ACTION) {
            Intent approval = Build.VERSION.SDK_INT >= 33
                    ? intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent.class)
                    : intent.getParcelableExtra(Intent.EXTRA_INTENT);
            if (approval == null) { clear(context, "failed"); return; }
            int session = prefs.getInt(SESSION, -1);
            try {
                PendingIntent.getActivity(context, session,
                        approvalProxy(context, session)
                                .putExtra(SESSION, session)
                                .putExtra(NONCE, prefs.getString(NONCE, ""))
                                .putExtra(Intent.EXTRA_INTENT, approval),
                        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
                if (!prefs.edit().putString(STATUS, "awaiting_android_approval").commit())
                    approvalUnavailable(context);
            } catch (RuntimeException unavailable) {
                // Keep the OS session guarded. A background broadcast must not
                // attempt to launch the installer on Android's newer BAL rules.
                approvalUnavailable(context);
            }
        } else if (result == PackageInstaller.STATUS_SUCCESS) {
            // Keep collection inhibited until a new process observes the actual
            // installed version. A callback alone does not prove process replacement.
            prefs.edit().putString(STATUS, "applied_waiting_restart").commit();
        } else {
            clear(context, result == PackageInstaller.STATUS_FAILURE_ABORTED ? "cancelled" : "failed");
        }
    }
}
