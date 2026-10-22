package ai.satoris.amplifai.phone;

import android.app.Activity;
import android.content.Intent;
import android.os.Build;
import android.os.Bundle;

/** User-tapped foreground continuation for Android's own installer prompt. */
public final class UpdateApprovalActivity extends Activity {
    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Intent request = getIntent();
        int session = request.getIntExtra("session", -1);
        String nonce = request.getStringExtra("nonce");
        if (!UpdateInstallReceiver.matchesApproval(this, session, nonce)) { finish(); return; }
        Intent approval = Build.VERSION.SDK_INT >= 33
                ? request.getParcelableExtra(Intent.EXTRA_INTENT, Intent.class)
                : request.getParcelableExtra(Intent.EXTRA_INTENT);
        if (approval == null) {
            UpdateInstallReceiver.approvalUnavailable(this);
            finish();
            return;
        }
        try { startActivity(approval); }
        catch (RuntimeException unavailable) { UpdateInstallReceiver.approvalUnavailable(this); }
        finish();
    }
}
