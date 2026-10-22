package ai.satoris.amplifai.phone;

import android.app.Activity;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.function.BooleanSupplier;

/** The Play build leaves package updates entirely with Google Play. */
final class AndroidUpdater {
    AndroidUpdater(Activity activity, LinearLayout parent, BooleanSupplier safeToInstall) {
        PackageInfo installed;
        try {
            installed = activity.getPackageManager().getPackageInfo(activity.getPackageName(), 0);
        } catch (PackageManager.NameNotFoundException impossible) {
            throw new IllegalStateException(impossible);
        }
        TextView heading = new TextView(activity);
        heading.setText(R.string.play_update_heading);
        heading.setTextColor(Color.rgb(237, 239, 243));
        heading.setTextSize(18);
        int top = (int) (14 * activity.getResources().getDisplayMetrics().density + 0.5f);
        int bottom = (int) (2 * activity.getResources().getDisplayMetrics().density + 0.5f);
        heading.setPadding(0, top, 0, bottom);
        parent.addView(heading);
        TextView status = new TextView(activity);
        status.setText(activity.getString(R.string.play_update_status, installed.versionName));
        status.setTextColor(Color.rgb(200, 207, 217));
        status.setTextSize(13);
        parent.addView(status);
    }

    boolean blocksCollection() { return false; }
    void onSafetyChanged() {}
    void onStart() {}
    void onResume() {}
    void onStop() {}
    void close() {}
}
