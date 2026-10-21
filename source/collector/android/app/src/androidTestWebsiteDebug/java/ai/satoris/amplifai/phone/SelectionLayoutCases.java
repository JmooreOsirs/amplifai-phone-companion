package ai.satoris.amplifai.phone;

import android.app.Instrumentation;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.Insets;
import android.graphics.Rect;
import android.os.Build;
import android.os.Bundle;
import android.os.SystemClock;
import android.provider.Settings;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewTreeObserver;
import android.view.WindowInsets;
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
import java.util.concurrent.atomic.AtomicBoolean;
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
    private final JSONObject evidence = new JSONObject();
    private final JSONObject controls = new JSONObject();
    private final JSONObject screenshots = new JSONObject();

    static String run(Instrumentation instrumentation, Bundle arguments) throws Exception {
        require("disposable-emulator".equals(arguments.getString("fixture_scope")) &&
                "true".equals(arguments.getString("network_isolated")),
                "Use an explicitly disposable, WAN-isolated emulator; preserve loopback");
        require(Build.FINGERPRINT.startsWith("generic") || Build.HARDWARE.contains("ranchu") ||
                Build.HARDWARE.contains("goldfish"), "Selection fixtures must not run on a physical phone");
        String scenario = arguments.getString("scenario", "");
        int width = switch (scenario) {
            case "selection_layout_360", "selection_ime_360" -> 360;
            case "selection_layout_320_large", "selection_ime_320_large" -> 320;
            case "selection_layout_768", "selection_ime_768" -> 768;
            default -> throw new AssertionError("Unknown selection layout scenario");
        };
        float scale = width == 320 ? 1.5f : 1f;
        MainActivity activity = (MainActivity) instrumentation.startActivitySync(
                new Intent(instrumentation.getTargetContext(), MainActivity.class)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK));
        SelectionLayoutCases fixture = null;
        try {
            instrumentation.waitForIdleSync();
            fixture = new SelectionLayoutCases(instrumentation, activity, arguments);
            return fixture.verify(width, scale, scenario.startsWith("selection_ime_"));
        } catch (Exception | AssertionError failure) {
            if (fixture != null) fixture.recordFailure(failure);
            throw failure;
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

    private String verify(int width, float scale, boolean withIme) throws Exception {
        evidence.put("requestedWidthDp", width).put("widthDp", root.getWidth() / density)
                .put("heightDp", root.getHeight() / density)
                .put("fontScale", activity.getResources().getConfiguration().fontScale);
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
        main(() -> {
            ((CheckBox) choices.getChildAt(0)).performClick();
            require(review.isEnabled() && review.isShown(), "Review is not accessible after selecting a contact");
        });
        instrumentation.waitForIdleSync();
        if (withIme) return verifyIme();
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
            requireFullyVisible("reviewAction", review);
            requireFullyVisible("search", search);
            requireFullyVisible("previousPage", get("previousPage", Button.class));
            requireFullyVisible("nextPage", get("nextPage", Button.class));
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
            requireFullyVisible("lastContactChoice", choices.getChildAt(choices.getChildCount() - 1));
            try {
                evidence.put("visibleWindow", window.toShortString()).put("choicesHeightDp", contactScroll.getHeight() / density)
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
        verifyFullReviewReachable();
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
            requireFullyVisible("bulkCancel", cancel);
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
        });
        instrumentation.waitForIdleSync();
        focusWithKeyboard("selectAll", get("selectAllContacts", Button.class));
        main(() -> {
            Button selectAll = get("selectAllContacts", Button.class);
            requireFullyVisible("selectAll", selectAll);
            selectAll.performClick();
        });
        awaitLayout(() -> !get("bulkSelectionActive", Boolean.class) && get("selectedIds", Set.class).size() == 305 &&
                choices.getChildCount() == 100, "All-page action did not select every synthetic contact");
        main(() -> {
            require(contactScroll.isShown(), "All-page selection did not return to the contact choices");
            navigation.performClick();
        });
        instrumentation.waitForIdleSync();
        focusWithKeyboard("clearSelection", get("clearSelectedContacts", Button.class));
        main(() -> {
            Button clear = get("clearSelectedContacts", Button.class);
            requireFullyVisible("clearSelection", clear);
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
            requireFullyVisible("errorStatus", status);
            for (int line = 0; line < status.getLayout().getLineCount(); line++)
                require(status.getLayout().getEllipsisCount(line) == 0, "Full error explanation was truncated");
        });
        capture("error-details");
        evidence.put("checks", "fixed controls; independent choices; actual bounds; native styles; paging; empty search; review/change; bulk Cancel; keyboard focus; all-page selection/clear; zero/unavailable; full review/error");
        return writeReceipt("passed");
    }

    private void verifyFullReviewReachable() throws Exception {
        TextView text = get("review", TextView.class);
        awaitLayout(() -> text.getLayout() != null && !text.isLayoutRequested() && !detailsScroll.isLayoutRequested(),
                "Final reviewed text did not finish layout");
        AtomicBoolean initiallyVisible = new AtomicBoolean();
        main(() -> {
            recordControl("reviewAfterAsyncText", text, null);
            Rect visible = new Rect();
            initiallyVisible.set(text.getGlobalVisibleRect(visible) && visible.equals(bounds(text)));
            for (int line = 0; line < text.getLayout().getLineCount(); line++)
                require(text.getLayout().getEllipsisCount(line) == 0, "Reviewed text was truncated");
        });
        evidence.put("reviewInitiallyFullyVisible", initiallyVisible.get());
        capture("review");
        focusWithKeyboard("fullReview", text);
        main(() -> requireFullyVisible("reviewAfterKeyboard", text));
        capture("review-accessible");
    }

    private String verifyIme() throws Exception {
        require(Build.VERSION.SDK_INT >= 30, "Measured IME fixtures require Android API30 or newer");
        evidence.put("configuredIme", Settings.Secure.getString(activity.getContentResolver(), Settings.Secure.DEFAULT_INPUT_METHOD));
        main(() -> {
            require(!imeVisible(), "Begin the IME fixture with the keyboard dismissed");
            requireChoicesWithoutIme();
        });
        @SuppressWarnings("unchecked") Set<Long> selected = get("selectedIds", Set.class);
        Set<Long> expected = Set.copyOf(selected);
        showKeyboard();
        main(() -> requireImeControls("ime-review", review));
        capture("ime-review");
        instrumentation.sendStringSync("305");
        awaitLayout(() -> "305".contentEquals(search.getText()) && choices.getChildCount() == 1 &&
                get("pageIndicator", TextView.class).getText().toString().startsWith("1–1 of 1"),
                "Typed search did not filter the synthetic contacts");
        main(() -> {
            require(expected.equals(selected), "Typing changed the selected contacts");
            requireImeControls("ime-filtered", review);
        });
        capture("ime-filtered");
        for (int i = 0; i < 3; i++) instrumentation.sendKeyDownUpSync(KeyEvent.KEYCODE_DEL);
        awaitLayout(() -> search.length() == 0 && choices.getChildCount() == 100, "Keyboard deletion did not restore the contact page");
        dismissKeyboard(expected);
        capture("ime-dismissed");
        tap("reviewAfterIme", review);
        awaitLayout(() -> get("reviewed", Boolean.class), "Local review did not finish after keyboard dismissal");
        verifyFullReviewReachable();
        tap("returnToSelection", navigation);
        showKeyboard();
        main(() -> requireImeControls("ime-handoff", get("handoffButton", Button.class)));
        capture("ime-handoff");
        main(() -> {
            set("bulkSelectionActive", true);
            get("cancelSelectAll", Button.class).setVisibility(View.VISIBLE);
            invoke("refreshActions");
        });
        instrumentation.waitForIdleSync();
        main(() -> requireImeControls("ime-cancel", get("cancelSelectAll", Button.class)));
        capture("ime-cancel");
        tap("cancelWithIme", get("cancelSelectAll", Button.class));
        main(() -> {
            require(!get("bulkSelectionActive", Boolean.class) && expected.equals(selected),
                    "Cancel with the keyboard visible changed the prior selection");
        });
        dismissKeyboard(expected);
        evidence.put("checks", "installed docked IME; actual insets and bounds; search/Review/Handoff/Cancel access; typed search/delete; dismissal preserves selection; full review reachable by keyboard focus; no-IME minimum retained");
        return writeReceipt("passed");
    }

    private boolean imeVisible() {
        WindowInsets insets = root.getRootWindowInsets();
        return insets != null && insets.isVisible(WindowInsets.Type.ime());
    }

    private void showKeyboard() throws Exception {
        tap("searchBeforeIme", search);
        awaitLayout(this::imeVisible, "The installed keyboard did not become visible; no IME dimensions were simulated");
        instrumentation.getUiAutomation().waitForIdle(300, 5_000);
        instrumentation.waitForIdleSync();
    }

    private void tap(String identity, View target) {
        Rect rect = new Rect();
        main(() -> { requireFullyVisible(identity, target); rect.set(bounds(target)); });
        long start = SystemClock.uptimeMillis();
        for (int action : new int[]{MotionEvent.ACTION_DOWN, MotionEvent.ACTION_UP}) {
            MotionEvent event = MotionEvent.obtain(start, SystemClock.uptimeMillis(), action,
                    rect.exactCenterX(), rect.exactCenterY(), 0);
            event.setSource(InputDevice.SOURCE_TOUCHSCREEN);
            try { instrumentation.sendPointerSync(event); }
            finally { event.recycle(); }
        }
        instrumentation.waitForIdleSync();
    }

    private void dismissKeyboard(Set<Long> expected) throws Exception {
        main(() -> require(imeVisible(), "Keyboard was no longer visible before dismissal"));
        instrumentation.sendKeyDownUpSync(KeyEvent.KEYCODE_BACK);
        awaitLayout(() -> !imeVisible(), "Back did not dismiss the installed keyboard");
        instrumentation.getUiAutomation().waitForIdle(300, 5_000);
        instrumentation.waitForIdleSync();
        main(() -> {
            require(!activity.isFinishing() && expected.equals(get("selectedIds", Set.class)),
                    "Keyboard dismissal left the Activity or changed selected contacts");
            requireChoicesWithoutIme();
        });
    }

    private void requireChoicesWithoutIme() {
        require(contactScroll.getHeight() >= Math.max(Math.round(96 * density), choices.getChildAt(0).getHeight() * 2),
                "The no-IME viewport lost its two-row/96dp minimum");
    }

    private void requireImeControls(String identity, Button action) {
        WindowInsets insets = root.getRootWindowInsets();
        require(insets != null && insets.isVisible(WindowInsets.Type.ime()), "The installed keyboard is not visible");
        Insets ime = insets.getInsets(WindowInsets.Type.ime());
        Insets system = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
        Rect window = new Rect(); root.getWindowVisibleDisplayFrame(window);
        Rect metrics = activity.getWindowManager().getCurrentWindowMetrics().getBounds();
        Rect usable = new Rect(metrics.left + Math.max(system.left, ime.left), metrics.top + Math.max(system.top, ime.top),
                metrics.right - Math.max(system.right, ime.right), metrics.bottom - Math.max(system.bottom, ime.bottom));
        if (!usable.intersect(window)) usable.setEmpty();
        Rect choicesVisible = new Rect();
        if (!contactScroll.getGlobalVisibleRect(choicesVisible) || !choicesVisible.intersect(usable)) choicesVisible.setEmpty();
        int minimum = Math.max(Math.round(48 * density), choices.getChildAt(0).getHeight());
        boolean wholeRowVisible = false;
        for (int i = 0; i < choices.getChildCount(); i++)
            if (choicesVisible.contains(bounds(choices.getChildAt(i)))) { wholeRowVisible = true; break; }
        try {
            evidence.put(identity, new JSONObject().put("imeVisible", true).put("imeInsets", ime.toString())
                    .put("systemInsets", system.toString()).put("windowMetrics", metrics.toShortString())
                    .put("visibleWindow", window.toShortString()).put("usable", usable.toShortString())
                    .put("choicesVisible", choicesVisible.toShortString()).put("choicesHeightDp", choicesVisible.height() / density)
                    .put("minimumChoicesDp", minimum / density).put("wholeRowVisible", wholeRowVisible));
        } catch (org.json.JSONException impossible) { throw new AssertionError(impossible); }
        require(ime.bottom > 0, "A docked installed keyboard is required to measure its actual occlusion");
        requireFullyVisible(identity + "Search", search);
        requireFullyVisible(identity + "Action", action);
        require(search.isFocused() && action.isEnabled(), "IME input or applicable action is unavailable: " + identity);
        require(usable.contains(bounds(search)) && usable.contains(bounds(action)), "Search or action overlaps the actual IME/system bounds: " + identity);
        require(choicesVisible.height() >= minimum && wholeRowVisible,
                "IME leaves fewer than one full contact row or 48dp of usable choices: " + identity);
    }

    private void recordFailure(Throwable failure) {
        try {
            evidence.put("failure", failure.getMessage());
            capture("failure");
            writeReceipt("failed");
        } catch (Exception | AssertionError unavailable) {
            failure.addSuppressed(unavailable);
        }
    }

    private String writeReceipt(String outcome) throws org.json.JSONException {
        evidence.put("outcome", outcome).put("controls", controls).put("screenshots", screenshots);
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

    private void focusWithKeyboard(String identity, View target) {
        main(() -> recordControl(identity + "BeforeKeyboard", target, target.requestFocus()));
        // Keyboard input makes native control focus requests meaningful. Visibility is checked afterward.
        instrumentation.sendKeyDownUpSync(KeyEvent.KEYCODE_TAB);
        instrumentation.waitForIdleSync();
        main(() -> {
            boolean accepted = target.requestFocus();
            JSONObject state = recordControl(identity + "KeyboardFocus", target, accepted);
            require(!target.isInTouchMode() && accepted && target.isFocused(),
                    "Keyboard focus was not accepted: " + state);
        });
        instrumentation.waitForIdleSync();
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
    private JSONObject recordControl(String identity, View view, Boolean focusAccepted) {
        Rect visible = new Rect(), window = new Rect();
        boolean hasVisibleRect = view.getGlobalVisibleRect(visible);
        root.getWindowVisibleDisplayFrame(window);
        try {
            JSONObject state = new JSONObject().put("identity", identity).put("type", view.getClass().getSimpleName())
                    .put("bounds", bounds(view).toShortString()).put("visible", visible.toShortString())
                    .put("hasVisibleRect", hasVisibleRect).put("window", window.toShortString())
                    .put("shown", view.isShown()).put("enabled", view.isEnabled())
                    .put("inTouchMode", view.isInTouchMode()).put("focusable", view.isFocusable())
                    .put("focusableInTouchMode", view.isFocusableInTouchMode()).put("focused", view.isFocused())
                    .put("layoutRequested", view.isLayoutRequested()).put("windowFocused", view.hasWindowFocus())
                    .put("detailsScrollY", detailsScroll.getScrollY()).put("contactScrollY", contactScroll.getScrollY());
            if (focusAccepted != null) state.put("focusRequestAccepted", focusAccepted);
            if (view instanceof TextView text && text.getLayout() != null)
                state.put("textLines", text.getLayout().getLineCount()).put("textHeight", text.getLayout().getHeight());
            controls.put(identity, state);
            return state;
        } catch (org.json.JSONException impossible) { throw new AssertionError(impossible); }
    }

    private void requireFullyVisible(String identity, View view) {
        Rect visible = new Rect();
        JSONObject state = recordControl(identity, view, null);
        require(view.isShown() && view.getGlobalVisibleRect(visible) && visible.equals(bounds(view)),
                "Control is clipped or hidden: " + state);
    }
    private static void require(boolean condition, String failure) { if (!condition) throw new AssertionError(failure); }
}
