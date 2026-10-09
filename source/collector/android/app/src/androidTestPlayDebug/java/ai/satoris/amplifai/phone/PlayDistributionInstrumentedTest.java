package ai.satoris.amplifai.phone;

import android.app.Instrumentation;
import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.os.Bundle;

import java.io.IOException;
import java.util.Arrays;

/** Checks the installed Play variant without requesting any phone permission. */
public final class PlayDistributionInstrumentedTest extends Instrumentation {
    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            Context app = getTargetContext();
            PackageInfo info = app.getPackageManager().getPackageInfo(app.getPackageName(),
                    PackageManager.GET_PERMISSIONS | PackageManager.GET_ACTIVITIES |
                            PackageManager.GET_RECEIVERS | PackageManager.GET_SERVICES);
            require(hasPermission(info, "android.permission.READ_CONTACTS"), "contacts permission missing");
            require(hasPermission(info, "android.permission.READ_CALL_LOG"), "call permission missing");
            require(hasPermission(info, "android.permission.READ_SMS"), "SMS permission missing");
            require(!hasPermission(info, "android.permission.REQUEST_INSTALL_PACKAGES"),
                    "APK install permission included");
            require(!hasPermission(info, "android.permission.UPDATE_PACKAGES_WITHOUT_USER_ACTION"),
                    "automatic package install permission included");
            require(info.services != null && Arrays.stream(info.services)
                    .anyMatch(service -> service.name.endsWith(".HandoffService")),
                    "browser handoff service missing");
            require(Arrays.stream(info.activities).noneMatch(activity -> activity.name.endsWith(".UpdateApprovalActivity")),
                    "website update approval activity included");
            require(info.receivers == null || Arrays.stream(info.receivers)
                    .noneMatch(receiver -> receiver.name.endsWith(".UpdateInstallReceiver")),
                    "website update receiver included");
            for (String name : new String[]{"AndroidUpdateFeed", "UpdateTransport", "UpdateInstaller",
                    "UpdateInstallReceiver", "UpdateApprovalActivity"}) {
                try {
                    app.getClassLoader().loadClass("ai.satoris.amplifai.phone." + name);
                    throw new AssertionError(name + " included in Play variant");
                } catch (ClassNotFoundException expected) {
                    // The Play APK must contain no website updater implementation.
                }
            }
            try (var ignored = app.getAssets().open("android_update_publisher.der")) {
                throw new AssertionError("website update certificate included");
            } catch (IOException expected) {
                // The Play APK relies on Google Play for updates.
            }
            result.putString("result", "Play installer code, permission, components and certificate absent; phone-source and handoff permissions retained");
            finish(0, result);
        } catch (Exception | AssertionError failure) {
            result.putString("error", failure.getClass().getSimpleName() + ": " + failure.getMessage());
            finish(-1, result);
        }
    }

    private static boolean hasPermission(PackageInfo info, String name) {
        return info.requestedPermissions != null && Arrays.asList(info.requestedPermissions).contains(name);
    }

    private static void require(boolean passed, String message) {
        if (!passed) throw new AssertionError(message);
    }
}
