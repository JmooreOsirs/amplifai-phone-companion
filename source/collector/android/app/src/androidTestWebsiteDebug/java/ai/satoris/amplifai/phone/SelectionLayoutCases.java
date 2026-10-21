package ai.satoris.amplifai.phone;

import android.app.Instrumentation;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Rect;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.ViewTreeObserver;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BooleanSupplier;

/** Actual Activity geometry with private synthetic rows; never reads a phone provider or starts a handoff. */
final class SelectionLayoutCases {
    private final Instrumentation instrumentation;
    private final MainActivity activity;
    private final View root;
    private final LinearLayout choices;
    private final ScrollView contactScroll;
    private final ScrollView detailsScroll;
    private final View header;
    private final View actions;
    private final Button navigation;
    private final Button review;
    private final EditText search;
    private final float density;
    private final File captureDirectory;
    private final JSONObject screenshots = new JSONObject();

    static String run(Instrumentation instrumentation, Bundle arguments) throws Exception {
        require("disposable-emulator".equals(arguments.getString("fixture_scope")) &&
                "true".equals(arguments.getString("network_isolated")),
                "Use an explicitly disposable, WAN-isolated emulator; preserve loopback");
        require(Build.FINGERPRINT.startsWith("generic") || Build.HARDWARE.contains("ranchu") ||
                Build.HARDWARE.contains("goldfish"), "Selection fixtures must not run on a physical phone");
        int width = switch (arguments.getString("scenario", "")) {
            case "selection_layout_360" -> 360;
            case "selection_layout_320_large" -> 320;
            case "selection_layout_768" -> 768;
            default -> throw new AssertionError("Unknown selection layout scenario");
        };
        float scale = width == 320 ? 1.5f : 1f;
        MainActivity activity = (MainActivity) instrumentation.startActivitySync(
                new Intent(instrumentation.getTargetContext(), MainActivity.class)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK));
        try {
            instrumentation.waitForIdleSync();
            return new SelectionLayoutCases(instrumentation, activity, arguments).verify(width, scale);
        } finally { instrumentation.runOnMainSync(activity::finish); }
    }

    private SelectionLayoutCases(Instrumentation instrumentation, MainActivity activity, Bundle arguments) {
        this.instrumentation = instrumentation;
        this.activity = activity;
        root = activity.findViewById(android.R.id.content);
        choices = get("choices", LinearLayout.class);
        contactScroll = get("contactScroll", ScrollView.class);
        detailsScroll = get("detailsScroll", ScrollView.class);
        header = get("selectionHeader", View.class);
        actions = get("actionBar", View.class);
        navigation = get("selectionNavigation", Button.class);
        review = get("reviewButton", Button.class);
        search = get("search", EditText.class);
        density = activity.getResources().getDisplayMetrics().density;
        File external = "true".equals(arguments.getString("capture_screenshots"))
                ? instrumentation.getTargetContext().getExternalFilesDir("instrumentation-selection-layout") : null;
        captureDirectory = external != null
                ? new File(external, arguments.getString("scenario")) : null;
    }

    private String verify(int width, float scale) throws Exception {
        require(Math.abs(activity.getResources().getConfiguration().fontScale - scale) < .01f,
                "Configure the required font scale before launching this scenario");
        require(Math.abs(root.getWidth() / density - width) <= 1,
                "Configure the actual Activity viewport width before launching this scenario");
        awaitLayout(() -> get("handoff", HandoffService.LocalBinder.class) != null, "Activity did not bind its local handoff service");
        main(() -> require(!get("handoff", HandoffService.LocalBinder.class).snapshot().active(),
                "Fixture requires no active handoff"));
        SanitizedStore store = get("store", SanitizedStore.class);
        require(store != null && store.count("contacts") == 0, "Fixture requires an empty private review store");
        get("executor", ExecutorService.class).submit(() -> {
            store.begin("contacts");
            for (long id = 1; id <= 305; id++)
                store.contact(id, String.format(java.util.Locale.ROOT, "Synthetic %03d", id),
                        "+1555" + String.format(java.util.Locale.ROOT, "%07d", id));
            store.commit("contacts", 305, 0);
            store.begin("calls"); store.commit("calls", 0, 0);
        }).get(5, TimeUnit.SECONDS);
        main(() -> {
            set("contactsAvailable", true); set("callsAvailable", true);
            String[] states = get("sourceDispositions", String[].class);
            states[0] = "Available"; states[1] = "Available"; states[2] = "Unavailable";
            invoke("showCoverage"); invoke("renderChoices"); showSelection(true);
        });
        awaitLayout(() -> choices.getChildCount() == 100 && contactScroll.getHeight() > 0, "Populated contacts did not render");
        JSONObject evidence = new JSONObject();
        main(() -> {
            ((CheckBox) choices.getChildAt(0)).performClick();
            require(review.isEnabled() && review.isShown(), "Review is not accessible after selecting a contact");
        });
        instrumentation.waitForIdleSync();
        capture("populated");
        main(() -> {
            Rect beforeHeader = bounds(header), beforeActions = bounds(actions), beforeNavigation = bounds(navigation);
            Rect viewport = bounds(contactScroll);
            Rect window = new Rect(); root.getWindowVisibleDisplayFrame(window);
            require(viewport.top >= beforeHeader.bottom && viewport.bottom <= beforeActions.top,
                    "Contact choices overlap fixed controls");
            require(contactScroll.getHeight() >= Math.max(Math.round(96 * density), choices.getChildAt(0).getHeight() * 2),
                    "Fixed controls leave fewer than two contact rows or 96dp of usable choices");
            require(window.contains(beforeHeader) && window.contains(bounds(review)) && window.contains(beforeNavigation),
                    "Fixed controls extend outside the actual visible window");
            requireFullyVisible(review);
            requireFullyVisible(search);
            requireFullyVisible(get("previousPage", Button.class));
            requireFullyVisible(get("nextPage", Button.class));
            require(navigation.getContentDescription().toString().contains("privacy help"), "Options lost its accessible help label");
            require(search.getCurrentTextColor() == Color.rgb(237, 239, 243) &&
                    review.getCurrentTextColor() == Color.rgb(15, 21, 37) &&
                    header.getPaddingLeft() == Math.round(22 * density) && actions.getPaddingLeft() == header.getPaddingLeft(),
                    "Selection drifted from the native colors or side spacing");
            CheckBox first = (CheckBox) choices.getChildAt(0);
            require(first.getTextSize() == new CheckBox(activity).getTextSize() &&
                    search.getTextSize() == new EditText(activity).getTextSize() &&
                    review.getTextSize() == new Button(activity).getTextSize(), "Selection changed native control typography");
            int[] beforeRow = new int[2]; first.getLocationOnScreen(beforeRow);
            contactScroll.scrollTo(0, choices.getHeight());
            int[] afterRow = new int[2]; first.getLocationOnScreen(afterRow);
            require(contactScroll.getScrollY() > 0 && afterRow[1] < beforeRow[1], "The contact list did not scroll independently");
            require(beforeHeader.equals(bounds(header)) && beforeActions.equals(bounds(actions)) &&
                    beforeNavigation.equals(bounds(navigation)), "Scrolling choices moved fixed context or actions");
            requireFullyVisible(choices.getChildAt(choices.getChildCount() - 1));
            try {
                evidence.put("widthDp", width).put("heightDp", root.getHeight() / density).put("fontScale", scale)
                        .put("visibleWindow", window.toShortString()).put("choicesHeightDp", contactScroll.getHeight() / density)
                        .put("header", beforeHeader.toShortString()).put("actions", beforeActions.toShortString())
                        .put("choices", viewport.toShortString()).put("nativeButtonTextPx", review.getTextSize())
                        .put("searchTextPx", search.getTextSize()).put("checkboxTextPx", first.getTextSize());
            } catch (org.json.JSONException impossible) { throw new AssertionError(impossible); }
        });
        capture("scrolled");
        main(() -> get("nextPage", Button.class).performClick());
        awaitLayout(() -> get("pageIndicator", TextView.class).getText().toString().startsWith("101–200"), "Next page did not render");
        main(() -> { require(contactScroll.getScrollY() == 0, "A new page kept the old scroll offset"); get("previousPage", Button.class).performClick(); });
        awaitLayout(() -> get("pageIndicator", TextView.class).getText().toString().startsWith("1–100"), "Previous page did not render");
        main(() -> search.setText("no synthetic match"));
        awaitLayout(() -> get("pageIndicator", TextView.class).getText().toString().startsWith("No matches"), "Empty search did not settle");
        main(() -> { require(choices.getChildCount() == 0 && contactScroll.getHeight() >= 96 * density, "Empty selection lost its viewport"); search.setText(""); });
        awaitLayout(() -> choices.getChildCount() == 100, "Cleared search did not restore contacts");
        main(() -> { review.performClick(); require(detailsScroll.isShown(), "Review text remains hidden behind the selection pane"); });
        awaitLayout(() -> get("reviewed", Boolean.class), "Actual local review did not finish");
        capture("review");
        main(() -> {
            require(get("review", TextView.class).getText().toString().contains("0 matching calls") &&
                    get("review", TextView.class).getText().toString().contains("SMS unavailable/not read"),
                    "Readable zero and unavailable history were conflated");
            require(get("handoffButton", Button.class).isShown() && !review.isShown(), "Reviewed action did not replace Review");
            navigation.performClick();
            ((CheckBox) choices.getChildAt(1)).performClick();
            require(!get("reviewed", Boolean.class) && review.isShown() && !get("handoffButton", Button.class).isShown(),
                    "Changing selection did not restore the review gate");
            set("bulkSelectionActive", true);
            Button cancel = get("cancelSelectAll", Button.class);
            cancel.setVisibility(View.VISIBLE); invoke("refreshActions");
        });
        instrumentation.waitForIdleSync();
        main(() -> {
            Button cancel = get("cancelSelectAll", Button.class);
            requireFullyVisible(cancel);
            @SuppressWarnings("unchecked") Set<Long> selected = get("selectedIds", Set.class);
            Set<Long> before = Set.copyOf(selected);
            cancel.performClick();
            require(before.equals(selected) && !get("bulkSelectionActive", Boolean.class), "Fixed bulk Cancel changed the prior selection");
            navigation.performClick();
            require(detailsScroll.isShown(), "Source/progress/help pane is inaccessible");
            Button selectAll = get("selectAllContacts", Button.class);
            LinearLayout options = (LinearLayout) selectAll.getParent();
            LinearLayout body = (LinearLayout) options.getParent();
            body.getChildAt(body.indexOfChild(options) - 1).performClick();
            selectAll.requestFocus();
        });
        instrumentation.waitForIdleSync();
        main(() -> {
            Button selectAll = get("selectAllContacts", Button.class);
            requireFullyVisible(selectAll);
            selectAll.performClick();
        });
        awaitLayout(() -> !get("bulkSelectionActive", Boolean.class) && get("selectedIds", Set.class).size() == 305 &&
                choices.getChildCount() == 100, "All-page action did not select every synthetic contact");
        main(() -> {
            require(contactScroll.isShown(), "All-page selection did not return to the contact choices");
            navigation.performClick();
            get("clearSelectedContacts", Button.class).requestFocus();
        });
        instrumentation.waitForIdleSync();
        main(() -> {
            Button clear = get("clearSelectedContacts", Button.class);
            requireFullyVisible(clear);
            clear.performClick();
        });
        awaitLayout(() -> get("selectedIds", Set.class).isEmpty() && choices.getChildCount() == 100,
                "Clear action did not clear the all-page selection");
        main(() -> {
            require(!review.isEnabled() && !get("handoffButton", Button.class).isShown(), "Clear left a reviewed transfer action available");
            navigation.performClick();
            TextView status = get("status", TextView.class);
            status.setText("A selected record is too large for one transfer batch, or the source count exceeds the supported range. Review the source; no partial transfer started.");
            status.requestFocus();
        });
        instrumentation.waitForIdleSync();
        main(() -> {
            TextView status = get("status", TextView.class);
            requireFullyVisible(status);
            for (int line = 0; line < status.getLayout().getLineCount(); line++)
                require(status.getLayout().getEllipsisCount(line) == 0, "Full error explanation was truncated");
        });
        capture("error-details");
        evidence.put("checks", "fixed controls; independent choices; actual bounds; native styles; paging; empty search; review/change; bulk Cancel; all-page selection/clear; zero/unavailable; full error")
                .put("screenshots", screenshots);
        if (captureDirectory != null) {
            evidence.put("artifactDirectory", "instrumentation-selection-layout/" + captureDirectory.getName());
            try (FileOutputStream output = new FileOutputStream(new File(captureDirectory, "receipt.json"))) {
                output.write(evidence.toString(2).getBytes(StandardCharsets.UTF_8));
            } catch (IOException | SecurityException unavailable) {
                evidence.put("receiptUnavailable", unavailable.getClass().getSimpleName());
            }
        }
        return evidence.toString();
    }

    private void awaitLayout(BooleanSupplier condition, String failure) throws Exception {
        CountDownLatch settled = new CountDownLatch(1);
        ViewTreeObserver.OnGlobalLayoutListener listener = () -> { if (condition.getAsBoolean()) settled.countDown(); };
        main(() -> { root.getViewTreeObserver().addOnGlobalLayoutListener(listener); listener.onGlobalLayout(); });
        try { require(settled.await(5, TimeUnit.SECONDS), failure); }
        finally { main(() -> root.getViewTreeObserver().removeOnGlobalLayoutListener(listener)); }
    }

    private void capture(String state) throws Exception {
        if (captureDirectory == null) return;
        CountDownLatch rendered = new CountDownLatch(1);
        main(() -> root.postOnAnimation(() -> root.postOnAnimation(rendered::countDown)));
        if (!rendered.await(5, TimeUnit.SECONDS)) {
            screenshots.put(state, "unavailable:frame_timeout");
            return;
        }
        Bitmap bitmap = null;
        try {
            if (!captureDirectory.isDirectory() && !captureDirectory.mkdirs()) throw new IOException("capture_directory");
            bitmap = instrumentation.getUiAutomation().takeScreenshot();
            if (bitmap == null) throw new IOException("capture_unavailable");
            String relative = state + ".png";
            try (FileOutputStream output = new FileOutputStream(new File(captureDirectory, relative))) {
                if (!bitmap.compress(Bitmap.CompressFormat.PNG, 100, output)) throw new IOException("capture_encode");
            }
            screenshots.put(state, relative);
        } catch (IOException | RuntimeException unavailable) {
            screenshots.put(state, "unavailable:" + unavailable.getClass().getSimpleName());
        } finally { if (bitmap != null) bitmap.recycle(); }
    }

    private void main(Runnable action) {
        AtomicReference<Throwable> failure = new AtomicReference<>();
        instrumentation.runOnMainSync(() -> {
            try { action.run(); }
            catch (RuntimeException | AssertionError invalid) { failure.set(invalid); }
        });
        if (failure.get() != null) throw new AssertionError(failure.get());
    }
    private void showSelection(boolean visible) {
        try {
            Method method = MainActivity.class.getDeclaredMethod("showSelection", boolean.class);
            method.setAccessible(true); method.invoke(activity, visible);
        } catch (ReflectiveOperationException failure) { throw new AssertionError(failure); }
    }
    private void invoke(String name) {
        try {
            Method method = MainActivity.class.getDeclaredMethod(name);
            method.setAccessible(true); method.invoke(activity);
        } catch (ReflectiveOperationException failure) { throw new AssertionError(failure); }
    }
    private <T> T get(String name, Class<T> type) {
        try { Field field = MainActivity.class.getDeclaredField(name); field.setAccessible(true); return type.cast(field.get(activity)); }
        catch (ReflectiveOperationException failure) { throw new AssertionError(failure); }
    }
    private void set(String name, Object value) {
        try { Field field = MainActivity.class.getDeclaredField(name); field.setAccessible(true); field.set(activity, value); }
        catch (ReflectiveOperationException failure) { throw new AssertionError(failure); }
    }
    private static Rect bounds(View view) {
        int[] location = new int[2]; view.getLocationOnScreen(location);
        return new Rect(location[0], location[1], location[0] + view.getWidth(), location[1] + view.getHeight());
    }
    private static void requireFullyVisible(View view) {
        Rect visible = new Rect();
        require(view.isShown() && view.getGlobalVisibleRect(visible) && visible.equals(bounds(view)),
                "Control is clipped or hidden: " + view.getClass().getSimpleName());
    }
    private static void require(boolean condition, String failure) { if (!condition) throw new AssertionError(failure); }
}
