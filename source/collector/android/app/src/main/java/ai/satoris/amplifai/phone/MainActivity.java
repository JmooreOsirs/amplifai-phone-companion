package ai.satoris.amplifai.phone;

import android.Manifest;
import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.CancellationSignal;
import android.os.IBinder;
import android.os.OperationCanceledException;
import android.text.Editable;
import android.text.TextWatcher;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.text.DateFormat;
import java.time.Instant;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Owner-operated metadata review and explicit same-phone browser handoff. */
public final class MainActivity extends Activity {
    private static final int CONTACTS_REQUEST = 101;
    private static final int CALLS_REQUEST = 102;
    private static final int SMS_REQUEST = 103;
    private static final int NOTIFICATIONS_REQUEST = 104;
    private static final int BG = Color.rgb(15, 21, 37);
    private static final int TEXT = Color.rgb(237, 239, 243);
    private static final int MUTED = Color.rgb(200, 207, 217);
    private static final int ACCENT = Color.rgb(166, 230, 61);
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Set<Long> selectedIds = new HashSet<>();
    private CancellationSignal cancellation;
    private List<MetadataModel.Contact> contacts = Collections.emptyList();
    private List<MetadataModel.Interaction> calls = Collections.emptyList();
    private List<MetadataModel.Interaction> messages = Collections.emptyList();
    private TextView status;
    private TextView coverage;
    private TextView review;
    private LinearLayout choices;
    private EditText search;
    private EditText country;
    private Button cancel;
    private Button handoffButton;
    private HandoffService.LocalBinder handoff;
    private boolean bound;
    private boolean started;
    private boolean cancelHandoffOnConnect;
    private boolean contactsAvailable;
    private boolean callsAvailable;
    private boolean messagesAvailable;
    private boolean reviewed;
    private final ServiceConnection connection = new ServiceConnection() {
        @Override public void onServiceConnected(ComponentName name, IBinder service) {
            handoff = (HandoffService.LocalBinder) service;
            if (cancelHandoffOnConnect) {
                handoff.cancel();
                cancelHandoffOnConnect = false;
            }
            handoff.observe(MainActivity.this::showHandoff);
            handoffButton.setEnabled(reviewed && !handoff.snapshot().active());
        }
        @Override public void onServiceDisconnected(ComponentName name) {
            handoff = null;
            reviewed = false;
            handoffButton.setEnabled(false);
            review.setText("Browser handoff stopped. Review your selected metadata before approving a new transfer.");
        }
    };

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(BG);
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(22), dp(28), dp(22), dp(40));
        scroll.addView(body);
        setContentView(scroll);

        TextView brand = text("AMPLIFai By Nexus", 24, TEXT);
        brand.setTypeface(null, Typeface.BOLD);
        body.addView(brand);
        body.addView(text("Phone metadata review", 21, ACCENT));
        body.addView(text("Choose each source separately. Android asks for contacts, call-log and SMS access independently. This app reads only names/phone numbers, counterpart numbers, dates, direction and call duration. It never reads message bodies or attachments. Nothing leaves this phone until you approve a local browser handoff; account saves require separate consent.", 14, MUTED));
        body.addView(text("READ_CALL_LOG and READ_SMS can remain unavailable even after installation. If a source is denied or installer-restricted, this app reports it as unavailable; do not change device security settings.", 13, MUTED));

        country = new EditText(this);
        country.setSingleLine(true);
        country.setText(Locale.getDefault().getCountry().toUpperCase(Locale.ROOT));
        country.setHint("US");
        country.setTextColor(TEXT);
        country.setHintTextColor(MUTED);
        country.setBackgroundTintList(android.content.res.ColorStateList.valueOf(ACCENT));
        body.addView(text("Country for phone-number normalization (two-letter ISO code)", 13, MUTED));
        body.addView(country);

        Button readContacts = button("Read contacts", body);
        readContacts.setOnClickListener(view -> readSource(CONTACTS_REQUEST));
        Button readCalls = button("Read call history", body);
        readCalls.setOnClickListener(view -> readSource(CALLS_REQUEST));
        Button readMessages = button("Read SMS metadata", body);
        readMessages.setOnClickListener(view -> readSource(SMS_REQUEST));
        cancel = button("Cancel current read", body);
        cancel.setEnabled(false);
        cancel.setOnClickListener(view -> { if (cancellation != null) cancellation.cancel(); });

        status = text("No source read yet.", 14, MUTED);
        status.setPadding(0, dp(12), 0, dp(6));
        body.addView(status);
        coverage = text("Contacts, calls and SMS are not yet observed.", 13, MUTED);
        body.addView(coverage);

        body.addView(text("Select contacts", 21, TEXT));
        body.addView(text("Search by name or phone ending. Only selected contacts and their matched available interactions belong in a later, separately consented account transfer.", 13, MUTED));
        search = new EditText(this);
        search.setSingleLine(true);
        search.setHint("Search contacts");
        search.setTextColor(TEXT);
        search.setHintTextColor(MUTED);
        search.setBackgroundTintList(android.content.res.ColorStateList.valueOf(ACCENT));
        search.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) { renderChoices(); }
            @Override public void afterTextChanged(Editable s) {}
        });
        body.addView(search);
        choices = new LinearLayout(this);
        choices.setOrientation(LinearLayout.VERTICAL);
        body.addView(choices);
        Button reviewButton = button("Review selected metadata", body);
        reviewButton.setOnClickListener(view -> reviewSelected());
        review = text("No contacts selected. Review stays local until you approve browser handoff; account saves need separate consent.", 14, MUTED);
        body.addView(review);
        handoffButton = button("Approve same-phone browser handoff", body);
        handoffButton.setEnabled(false);
        handoffButton.setOnClickListener(view -> startHandoff());
        Button cancelHandoff = button("Cancel browser handoff", body);
        cancelHandoff.setOnClickListener(view -> {
            if (handoff == null || !handoff.snapshot().active()) {
                status.setText("No active browser handoff to cancel.");
                return;
            }
            invalidateReview();
            status.setText("Browser handoff cancelled. Review again before starting a new code.");
            review.setText("The previous pairing code is no longer available. No account save was made by cancelling.");
        });
        Button openAccount = button("Open AMPLIFai account in browser", body);
        openAccount.setOnClickListener(view -> startActivity(new Intent(Intent.ACTION_VIEW,
                Uri.parse(LocalBridge.ACCOUNT_ORIGIN + "/phone/account"))));
        body.addView(text("Pair only from the browser on this Android phone. A visible notification keeps Cancel available while you use the browser. The code is one-use and expires after five minutes; account uploads still require separate source approval.", 13, MUTED));
        renderChoices();
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }

    private TextView text(String value, int size, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        view.setPadding(0, dp(7), 0, dp(7));
        return view;
    }

    private Button button(String label, LinearLayout parent) {
        Button button = new Button(this);
        button.setText(label);
        button.setAllCaps(false);
        button.setTextColor(BG);
        button.setBackgroundTintList(android.content.res.ColorStateList.valueOf(ACCENT));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.topMargin = dp(8);
        parent.addView(button, params);
        return button;
    }

    private static String permissionFor(int source) {
        return source == CONTACTS_REQUEST ? Manifest.permission.READ_CONTACTS
                : source == CALLS_REQUEST ? Manifest.permission.READ_CALL_LOG : Manifest.permission.READ_SMS;
    }

    private void readSource(int source) {
        if (handoff == null) { status.setText("Wait for the handoff controls to connect before reading a source."); return; }
        String iso = country.getText().toString().trim().toUpperCase(Locale.ROOT);
        if (!iso.matches("[A-Z]{2}")) {
            status.setText("Enter a two-letter country code before reading a source.");
            return;
        }
        if (cancellation != null) { status.setText("Cancel or finish the current source first."); return; }
        invalidateReview();
        clearSource(source);
        if (checkSelfPermission(permissionFor(source)) != PackageManager.PERMISSION_GRANTED) {
            status.setText("Requesting " + sourceName(source) + " access from Android…");
            requestPermissions(new String[]{ permissionFor(source) }, source);
            return;
        }
        beginRead(source, iso);
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == NOTIFICATIONS_REQUEST) {
            status.setText(grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED
                    ? "Notifications enabled. Approve the handoff again when ready."
                    : "Handoff needs its visible Cancel notification. No transfer was started.");
            return;
        }
        if (requestCode != CONTACTS_REQUEST && requestCode != CALLS_REQUEST && requestCode != SMS_REQUEST) return;
        if (grantResults.length == 0 || grantResults[0] != PackageManager.PERMISSION_GRANTED ||
                checkSelfPermission(permissionFor(requestCode)) != PackageManager.PERMISSION_GRANTED) {
            status.setText(sourceName(requestCode) + " unavailable: permission denied or installer-restricted. No provider query ran.");
            return;
        }
        beginRead(requestCode, country.getText().toString().trim().toUpperCase(Locale.ROOT));
    }

    private String sourceName(int source) {
        return source == CONTACTS_REQUEST ? "Contacts" : source == CALLS_REQUEST ? "Calls" : "SMS";
    }

    private void beginRead(int source, String iso) {
        if (!started) { status.setText("Return to the app and read the source again when ready."); return; }
        cancellation = new CancellationSignal();
        CancellationSignal taskSignal = cancellation;
        cancel.setEnabled(true);
        status.setText("Reading " + sourceName(source) + " metadata locally…");
        executor.execute(() -> {
            try {
                PhoneMetadataReader next = new PhoneMetadataReader(this);
                if (source == CONTACTS_REQUEST) {
                    MetadataModel.Source<MetadataModel.Contact> result = next.contacts(iso, taskSignal);
                    publishRead(taskSignal, () -> { contacts = result.records; contactsAvailable = true; selectedIds.clear(); finishRead(source, result); renderChoices(); });
                } else if (source == CALLS_REQUEST) {
                    MetadataModel.Source<MetadataModel.Interaction> result = next.calls(iso, taskSignal);
                    publishRead(taskSignal, () -> { calls = result.records; callsAvailable = true; finishRead(source, result); });
                } else {
                    MetadataModel.Source<MetadataModel.Interaction> result = next.messages(iso, taskSignal);
                    publishRead(taskSignal, () -> { messages = result.records; messagesAvailable = true; finishRead(source, result); });
                }
            } catch (OperationCanceledException ignored) {
                publishRead(taskSignal, () -> finishFailure("Read cancelled. No new source was stored."));
            } catch (SecurityException ignored) {
                publishRead(taskSignal, () -> finishFailure(sourceName(source) + " unavailable: access was denied or revoked. No new source was stored."));
            } catch (MetadataModel.SourceLimitExceededException ignored) {
                publishRead(taskSignal, () -> finishFailure(sourceName(source) + " exceeds this test build's provider scan limit. Nothing from that source was transferred; its full history was not reviewed."));
            } catch (RuntimeException ignored) {
                publishRead(taskSignal, () -> finishFailure(sourceName(source) + " provider unavailable or unsupported. No new source was stored."));
            }
        });
    }

    private void publishRead(CancellationSignal signal, Runnable result) {
        runOnUiThread(() -> {
            if (isDestroyed() || cancellation != signal) return;
            if (signal.isCanceled() || !started) finishFailure("Read cancelled. No new source was stored.");
            else result.run();
        });
    }

    private void finishRead(int source, MetadataModel.Source<?> result) {
        invalidateReview();
        cancellation = null;
        cancel.setEnabled(false);
        status.setText(sourceName(source) + ": " + result.records.size() + " metadata records observed, " +
                result.rejected + " rows excluded.");
        showCoverage();
        review.setText("Source data changed. Review your selected contacts and matching history again before browser handoff.");
    }

    private void finishFailure(String message) {
        cancellation = null;
        cancel.setEnabled(false);
        status.setText(message);
        showCoverage();
        review.setText("Source data changed. Review your selected contacts and matching history again before browser handoff.");
    }

    private void showCoverage() {
        coverage.setText("Available locally: contacts " + (contactsAvailable ? contacts.size() : "unavailable/not read") +
                "; calls " + (callsAvailable ? calls.size() : "unavailable/not read") +
                "; SMS " + (messagesAvailable ? messages.size() : "unavailable/not read") +
                ". Zero means a readable source with no records observed. RCS/MMS and unavailable history are not claimed.");
    }

    private void renderChoices() {
        if (choices == null) return;
        choices.removeAllViews();
        if (contacts.isEmpty()) {
            choices.addView(text("Read contacts first. Search and select after Android grants access.", 13, MUTED));
            return;
        }
        String query = search.getText().toString().trim().toLowerCase(Locale.ROOT);
        int visible = 0;
        for (MetadataModel.Contact contact : contacts) {
            String endings = contact.phones.isEmpty() ? "" : contact.phones.get(0);
            if (!contact.name.toLowerCase(Locale.ROOT).contains(query) &&
                    !endings.endsWith(query) && !query.isEmpty()) continue;
            if (visible++ >= 100) break;
            CheckBox choice = new CheckBox(this);
            choice.setText(contact.name + " · " + (endings.length() > 4 ? endings.substring(endings.length() - 4) : endings));
            choice.setTextColor(TEXT);
            choice.setButtonTintList(android.content.res.ColorStateList.valueOf(ACCENT));
            choice.setChecked(selectedIds.contains(contact.sourceId));
            choice.setOnCheckedChangeListener((button, checked) -> {
                invalidateReview();
                if (checked) selectedIds.add(contact.sourceId); else selectedIds.remove(contact.sourceId);
            });
            choices.addView(choice);
        }
        if (visible > 100) choices.addView(text("Showing the first 100 matches. Narrow the search to find another contact.", 12, MUTED));
    }

    private void reviewSelected() {
        if (cancellation != null || !contactsAvailable) { review.setText("Finish reading contacts before review."); return; }
        if (selectedIds.isEmpty()) { review.setText("Select at least one contact to review."); return; }
        MetadataModel.Review value = MetadataModel.review(contacts, calls, messages, selectedIds);
        String range = value.earliestMs == 0 ? "No matching interaction dates observed"
                : DateFormat.getDateInstance().format(value.earliestMs) + " – " +
                DateFormat.getDateInstance().format(value.latestMs);
        review.setText(value.selectedContacts + " selected contacts; " +
                (callsAvailable ? value.matchedCalls + " matching calls" : "call history unavailable/not read") + "; " +
                (messagesAvailable ? value.matchedMessages + " matching SMS records" : "SMS unavailable/not read") + ". " + range +
                ". This is available observed history, not proof of completeness. No data was sent or saved to an account. Confirm this selection before starting browser handoff.");
        reviewed = true;
        handoffButton.setEnabled(handoff != null && !handoff.snapshot().active());
    }

    private void invalidateReview() {
        reviewed = false;
        if (handoffButton != null) handoffButton.setEnabled(false);
        if (handoff != null && handoff.snapshot().active()) handoff.cancel();
    }

    private void clearSource(int source) {
        if (source == CONTACTS_REQUEST) {
            contacts = Collections.emptyList();
            contactsAvailable = false;
            selectedIds.clear();
            renderChoices();
        } else if (source == CALLS_REQUEST) {
            calls = Collections.emptyList();
            callsAvailable = false;
        } else {
            messages = Collections.emptyList();
            messagesAvailable = false;
        }
        showCoverage();
        review.setText("Source data changed. Review your selected contacts and matching history again before browser handoff.");
    }

    private void startHandoff() {
        if (!started || handoff == null || !reviewed || selectedIds.isEmpty() || !contactsAvailable || cancellation != null) {
            status.setText("Read contacts, select them, and review the current selection first.");
            return;
        }
        if (checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED ||
                (callsAvailable && checkSelfPermission(Manifest.permission.READ_CALL_LOG) != PackageManager.PERMISSION_GRANTED) ||
                (messagesAvailable && checkSelfPermission(Manifest.permission.READ_SMS) != PackageManager.PERMISSION_GRANTED)) {
            invalidateReview();
            status.setText("A source permission changed. Reread and review before pairing.");
            return;
        }
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            status.setText("Allow the handoff notification so Cancel stays visible while you use the browser.");
            requestPermissions(new String[]{ Manifest.permission.POST_NOTIFICATIONS }, NOTIFICATIONS_REQUEST);
            return;
        }
        if (!handoff.notificationsAvailable()) {
            status.setText("Handoff notifications are disabled. No transfer started; its visible Cancel control is required.");
            return;
        }
        byte[] payload = null;
        try {
            payload = NativePayload.selected(contacts, calls, messages, selectedIds,
                    callsAvailable, messagesAvailable, Instant.now());
        } catch (IllegalArgumentException invalidSelection) {
            status.setText(invalidSelection.getMessage() + " Reduce the selection or reread and review it. No account save was made.");
            return;
        }
        try {
            handoff.approve(payload);
        } catch (RuntimeException unavailable) {
            status.setText("Local handoff could not start. Check notification access and review again. No account save was made.");
        } finally {
            if (payload != null) Arrays.fill(payload, (byte) 0);
        }
    }

    private void showHandoff(HandoffService.Snapshot value) {
        if (value.state().equals("idle")) return;
        handoffButton.setEnabled(false);
        if (value.state().equals("ready")) {
            review.setText("One-use pairing code: " + value.code() +
                    ". Expires within 5 minutes of approval. Open AMPLIFai on this same Android phone, enter the code, review each source and separately approve each save. Cancel is available in the notification.");
            status.setText("Local handoff ready on this phone only. No cloud save has occurred.");
        } else if (value.state().equals("starting")) {
            status.setText("Starting the visible handoff notification…");
        } else {
            reviewed = false;
            String message = switch (value.state()) {
                case "completed" -> "Reviewed metadata transferred to the browser. Account saves still require separate source approval.";
                case "expired" -> "Browser handoff expired. Review again for a new pairing code.";
                case "attempts_exhausted" -> "Browser handoff stopped after five incorrect pairing attempts. Review again for a new code.";
                case "cancelled" -> "Browser handoff cancelled. Review again before a new transfer.";
                default -> "Browser handoff stopped. Review again and check notification access before retrying.";
            };
            status.setText(message);
            review.setText("The previous pairing code is no longer available. Nothing is saved to an account by this app.");
        }
    }

    @Override protected void onStart() {
        super.onStart();
        started = true;
        bound = bindService(new Intent(this, HandoffService.class), connection, BIND_AUTO_CREATE);
    }

    @Override protected void onResume() {
        super.onResume();
        boolean revoked = false;
        for (int source : new int[]{ CONTACTS_REQUEST, CALLS_REQUEST, SMS_REQUEST }) {
            boolean available = source == CONTACTS_REQUEST ? contactsAvailable
                    : source == CALLS_REQUEST ? callsAvailable : messagesAvailable;
            if (available && checkSelfPermission(permissionFor(source)) != PackageManager.PERMISSION_GRANTED) {
                invalidateReview();
                clearSource(source);
                revoked = true;
            }
        }
        if (revoked) {
            // Binding completes asynchronously; revoke its handoff before showing any earlier code.
            if (handoff == null) cancelHandoffOnConnect = true;
            status.setText("A source permission changed. That source is unavailable; reread and review permitted sources before pairing.");
        }
    }

    @Override protected void onStop() {
        started = false;
        if (cancellation != null) cancellation.cancel();
        if (handoff != null) {
            if (isFinishing() && handoff.snapshot().active()) handoff.cancel();
            handoff.observe(null);
            handoff = null;
        }
        if (bound) { unbindService(connection); bound = false; }
        super.onStop();
    }

    @Override protected void onDestroy() {
        if (cancellation != null) cancellation.cancel();
        executor.shutdownNow();
        super.onDestroy();
    }
}
