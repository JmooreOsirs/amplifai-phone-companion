package ai.satoris.amplifai.phone;

import android.animation.ObjectAnimator;
import android.animation.ValueAnimator;
import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.Locale;

/** Presentation of measured local activity and the separate account receipt. */
final class CollectionProgressPanel {
    private static final int BG = Color.rgb(15, 21, 37);
    private static final int PANEL = Color.rgb(20, 29, 48);
    private static final int BORDER = Color.rgb(38, 49, 70);
    private static final int HEADING = Color.rgb(237, 239, 243);
    private static final int BODY = Color.rgb(200, 207, 217);
    private static final int MUTED = Color.rgb(161, 169, 181);
    private static final int LIME = Color.rgb(166, 230, 61);
    private static final String[] SOURCES = {"Contacts", "Calls", "SMS"};

    private final Context context;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final TextView stageTitle;
    private final TextView stageValue;
    private final TextView stageUnit;
    private final TextView stageDetail;
    private final TextView elapsed;
    private final TextView movement;
    private final TextView[] sourceValues = new TextView[3];
    private final TextView[] sourceStates = new TextView[3];
    private final LinearLayout[] sourceCards = new LinearLayout[3];
    private final View activityDot;
    private final Button accountReport;
    private final Runnable tick = new Runnable() {
        @Override public void run() {
            if (!active) return;
            updateClock();
            handler.postDelayed(this, 1_000);
        }
    };
    private ObjectAnimator pulse;
    private String stageKey = "idle";
    private long stageStartedAt;
    private long lastMovementAt;
    private long measuredCount = -1;
    private boolean active;

    CollectionProgressPanel(Context context, LinearLayout parent, Runnable openAccount) {
        this.context = context;
        LinearLayout panel = column();
        panel.setPadding(dp(16), dp(14), dp(16), dp(16));
        panel.setBackground(outline(PANEL, 10));
        LinearLayout.LayoutParams panelParams = new LinearLayout.LayoutParams(-1, -2);
        panelParams.topMargin = dp(14);
        parent.addView(panel, panelParams);

        LinearLayout kicker = new LinearLayout(context);
        kicker.setOrientation(LinearLayout.HORIZONTAL);
        kicker.setGravity(Gravity.CENTER_VERTICAL);
        activityDot = new View(context);
        activityDot.setBackground(outline(LIME, 6));
        LinearLayout.LayoutParams dotParams = new LinearLayout.LayoutParams(dp(8), dp(8));
        dotParams.rightMargin = dp(8);
        kicker.addView(activityDot, dotParams);
        TextView overline = label("LOCAL PHONE PROCESS", 11, LIME, true);
        kicker.addView(overline);
        panel.addView(kicker);
        stageTitle = label("Choose a source to read", 22, HEADING, true);
        panel.addView(stageTitle);
        stageValue = label("—", 34, HEADING, true);
        stageValue.setAutoSizeTextTypeUniformWithConfiguration(20, 34, 1, TypedValue.COMPLEX_UNIT_SP);
        panel.addView(stageValue);
        stageUnit = label("No phone entries checked yet", 12, MUTED, false);
        panel.addView(stageUnit);
        stageDetail = label("Each source needs its own local approval. Account saving is a later decision.", 13, BODY, false);
        panel.addView(stageDetail);
        elapsed = label("Elapsed · —", 12, MUTED, false);
        movement = label("Waiting for measured activity", 12, MUTED, false);
        panel.addView(elapsed);
        panel.addView(movement);
        elapsed.setVisibility(View.GONE);
        movement.setVisibility(View.GONE);
        accountReport = new Button(context);
        accountReport.setText("Open saved account and report");
        accountReport.setAllCaps(false);
        accountReport.setTextColor(BG);
        accountReport.setBackgroundTintList(android.content.res.ColorStateList.valueOf(LIME));
        accountReport.setOnClickListener(view -> openAccount.run());
        accountReport.setVisibility(View.GONE);
        panel.addView(accountReport);

        TextView sourceHeading = label("OBSERVED LOCAL SOURCES", 11, LIME, true);
        LinearLayout.LayoutParams headingParams = new LinearLayout.LayoutParams(-1, -2);
        headingParams.topMargin = dp(16);
        parent.addView(sourceHeading, headingParams);
        LinearLayout row = new LinearLayout(context);
        row.setOrientation(LinearLayout.HORIZONTAL);
        parent.addView(row);
        for (int index = 0; index < SOURCES.length; index++) {
            LinearLayout card = column();
            card.setPadding(dp(9), dp(10), dp(8), dp(10));
            card.setBackground(outline(PANEL, 8));
            LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, -2, 1);
            if (index > 0) params.leftMargin = dp(7);
            row.addView(card, params);
            card.addView(label(SOURCES[index].toUpperCase(Locale.ROOT), 10, MUTED, true));
            sourceValues[index] = label("—", 27, HEADING, true);
            sourceValues[index].setMaxLines(2);
            sourceValues[index].setAutoSizeTextTypeUniformWithConfiguration(11, 27, 1, TypedValue.COMPLEX_UNIT_SP);
            card.addView(sourceValues[index]);
            sourceStates[index] = label("Not read", 11, MUTED, false);
            card.addView(sourceStates[index]);
            sourceCards[index] = card;
            card.setContentDescription(SOURCES[index] + ": not read");
        }
        activityDot.setVisibility(View.INVISIBLE);
    }

    private int dp(int value) { return Math.round(value * context.getResources().getDisplayMetrics().density); }

    private LinearLayout column() {
        LinearLayout value = new LinearLayout(context);
        value.setOrientation(LinearLayout.VERTICAL);
        return value;
    }

    private TextView label(String value, int sp, int color, boolean bold) {
        TextView result = new TextView(context);
        result.setText(value);
        result.setTextSize(sp);
        result.setTextColor(color);
        if (bold) result.setTypeface(null, Typeface.BOLD);
        result.setPadding(0, dp(3), 0, dp(3));
        return result;
    }

    private GradientDrawable outline(int fill, int radius) {
        GradientDrawable result = new GradientDrawable();
        result.setColor(fill);
        result.setCornerRadius(dp(radius));
        if (fill == PANEL) result.setStroke(dp(1), BORDER);
        return result;
    }

    static String whole(long number) {
        return String.format(Locale.US, "%,d", number);
    }

    static String counted(long number, String singular, String plural) {
        return whole(number) + " " + (number == 1 ? singular : plural);
    }

    private static String sourceDisplayCount(long number) {
        String exact = whole(number);
        if (exact.length() <= 7) return exact;
        int split = exact.lastIndexOf(',', exact.length() - 8);
        return split < 0 ? exact : exact.substring(0, split) + "\n" + exact.substring(split + 1);
    }

    void sources(long[] retained, String[] dispositions) {
        if (retained.length != 3 || dispositions.length != 3) throw new IllegalArgumentException("Three source states required");
        for (int index = 0; index < 3; index++) {
            String state = dispositions[index];
            boolean available = "Available".equals(state);
            String value = available ? sourceDisplayCount(retained[index]) : "—";
            boolean changed = !value.contentEquals(sourceValues[index].getText());
            sourceValues[index].setText(value);
            if (changed) emphasize(sourceValues[index]);
            sourceStates[index].setText(state);
            sourceCards[index].setContentDescription(SOURCES[index] + ": " +
                    (available ? counted(retained[index], "metadata record", "metadata records") + " kept on this phone"
                            : state.toLowerCase(Locale.ROOT)));
        }
    }

    void beginRead(String source) {
        showStage("read:" + source, "Reading " + source + " locally", "0", "Phone entries checked",
                "No account save has occurred. Available records are committed only after this read finishes.", true);
    }

    void awaitingPermission(String source) {
        showStage("permission:" + source, "Waiting for " + source + " access", "—", "No new source read",
                "Review Android's permission prompt. A denied source stays unavailable.", false);
    }

    void inspected(long seen) {
        if (!stageKey.startsWith("read:") || seen < 0 || seen < measuredCount) return;
        if (seen > measuredCount && seen > 0) lastMovementAt = SystemClock.elapsedRealtime();
        measuredCount = seen;
        String value = whole(seen);
        if (!value.contentEquals(stageValue.getText())) {
            stageValue.setText(value);
            emphasize(stageValue);
        }
        updateClock();
    }

    void finishedRead(String source, long retained, long seen, long rejected) {
        showStage("read-done:" + source, source + " read complete", whole(retained),
                "Metadata records kept on this phone", counted(seen, "phone entry", "phone entries") + " checked; " +
                        counted(rejected, "entry", "entries") + " excluded. Select contacts before any browser handoff.", false);
    }

    void failedRead(String source, String detail) {
        showStage("read-failed:" + source, source + " unavailable", "—", "No completed local source",
                detail, false);
    }

    void reviewed(long contacts, long calls, long messages, boolean callsAvailable, boolean messagesAvailable) {
        showStage("review", "Local selection reviewed", whole(contacts), "Selected contacts",
                (callsAvailable ? whole(calls) + " matching calls" : "Calls unavailable") + " · " +
                        (messagesAvailable ? whole(messages) + " matching SMS" : "SMS unavailable") +
                        ". These are local review counts, not account-saved totals.", false);
    }

    void selectionChanged() {
        showStage("selection-changed", "Selection changed", "—", "Review required",
                "Review the current selected contacts before approving another browser handoff.", false);
    }

    void preparing(String source, long rows) {
        showStage("preparing:" + source, "Preparing " + source + " locally", whole(rows),
                "Selected records prepared", "Only reviewed metadata is prepared for the browser. No account save has occurred.", true);
        if (rows > measuredCount && rows > 0) lastMovementAt = SystemClock.elapsedRealtime();
        measuredCount = Math.max(measuredCount, rows);
        updateClock();
    }

    void handoff(String state, long pagesProvided, long pagesTotal) {
        switch (state) {
            case "starting" -> showStage(state, "Starting local handoff", "—", "Browser delivery pending",
                    "The companion is preparing a visible Cancel notification.", true);
            case "ready" -> showStage(state, "Browser pairing ready", "Ready", "One-use local pairing",
                    "Enter the code on this phone. Account saving needs separate source consent.", true);
            case "transferring" -> {
                showStage(state, "Sharing reviewed metadata", whole(pagesProvided) + " / " + whole(pagesTotal),
                        "Batches requested by browser", "Browser verification and account save remain separate.", true);
                if (pagesProvided > measuredCount && pagesProvided > 0) lastMovementAt = SystemClock.elapsedRealtime();
                measuredCount = Math.max(measuredCount, pagesProvided);
                stageValue.setText(whole(pagesProvided) + " / " + whole(pagesTotal));
                updateClock();
            }
            case "received" -> showStage(state, "Browser review underway", "Pending", "Account save receipt",
                    "Keep this handoff open through separate source approval and account readback.", true);
            case "completed" -> showStage(state, "Account save confirmed", "Confirmed", "Verified saved receipt",
                    "Open your signed-in account to inspect the exact saved sources, report and print/PDF option.", false);
            default -> showStage("stopped:" + state, "Handoff stopped", "—", "No new save confirmed here",
                    "Previously saved account sources remain available in your signed-in account.", false);
        }
        accountReport.setVisibility("completed".equals(state) ? View.VISIBLE : View.GONE);
    }

    private void showStage(String key, String title, String value, String unit, String detail,
                           boolean isActive) {
        if (!key.equals(stageKey)) {
            stageKey = key;
            stageStartedAt = SystemClock.elapsedRealtime();
            lastMovementAt = 0;
            measuredCount = -1;
        }
        stageTitle.setText(title);
        stageValue.setText(value);
        stageUnit.setText(unit);
        stageDetail.setText(detail);
        accountReport.setVisibility(View.GONE);
        elapsed.setVisibility(isActive ? View.VISIBLE : View.GONE);
        movement.setVisibility(isActive && (key.startsWith("read:") || key.startsWith("preparing:") || "transferring".equals(key))
                ? View.VISIBLE : View.GONE);
        setActive(isActive);
        updateClock();
    }

    private void setActive(boolean value) {
        if (active == value) return;
        active = value;
        handler.removeCallbacks(tick);
        if (pulse != null) { pulse.cancel(); pulse = null; }
        activityDot.setAlpha(1f);
        activityDot.setVisibility(value ? View.VISIBLE : View.INVISIBLE);
        if (value) {
            if (ValueAnimator.areAnimatorsEnabled()) {
                pulse = ObjectAnimator.ofFloat(activityDot, View.ALPHA, 0.45f, 1f);
                pulse.setDuration(900);
                pulse.setRepeatCount(ValueAnimator.INFINITE);
                pulse.setRepeatMode(ValueAnimator.REVERSE);
                pulse.start();
            }
            handler.post(tick);
        }
    }

    private void updateClock() {
        if (stageStartedAt == 0) return;
        long now = SystemClock.elapsedRealtime();
        long seconds = Math.max(0, (now - stageStartedAt) / 1_000);
        elapsed.setText("Elapsed " + (seconds / 60) + "m " + (seconds % 60) + "s · not time remaining");
        movement.setText(lastMovementAt == 0 ? "Waiting for first measured movement" :
                "Last measured movement " + Math.max(0, (now - lastMovementAt) / 1_000) + "s ago");
    }

    private void emphasize(TextView value) {
        value.animate().cancel();
        value.setAlpha(1f);
        if (ValueAnimator.areAnimatorsEnabled()) {
            // Show the exact measured value immediately; motion never invents intermediate counts.
            value.setAlpha(0.55f);
            value.animate().alpha(1f).setDuration(240).start();
        }
    }

    void pause() { setActive(false); }

    void close() {
        pause();
        stageValue.animate().cancel();
        for (TextView value : sourceValues) value.animate().cancel();
        handler.removeCallbacksAndMessages(null);
    }
}
