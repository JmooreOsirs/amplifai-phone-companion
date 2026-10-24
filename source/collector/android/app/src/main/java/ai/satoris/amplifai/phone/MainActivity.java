package ai.satoris.amplifai.phone;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ComponentName;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Rect;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.CancellationSignal;
import android.os.IBinder;
import android.os.OperationCanceledException;
import android.os.SystemClock;
import android.database.sqlite.SQLiteException;
import android.database.sqlite.SQLiteFullException;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.View;
import android.view.ViewTreeObserver;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.text.DateFormat;
import java.time.Instant;
import java.util.HashSet;
import java.util.ArrayList;
import java.util.ArrayDeque;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.CancellationException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.function.Supplier;
import java.util.function.Consumer;

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
    private CancellationSignal transferPreparationCancellation;
    private SanitizedStore store;
    private int pageOffset;
    private TextView pageIndicator;
    private String selectionPageText = "";
    private String handoffActionText = "Approve browser handoff";
    private String cancelHandoffText = "Cancel browser handoff";
    private Button previousPage;
    private Button nextPage;
    private Button selectAllContacts;
    private Button clearSelectedContacts;
    private Button cancelSelectAll;
    private volatile int bulkSelectionGeneration;
    private boolean bulkSelectionActive;
    private boolean preparingTransfer;
    private int reviewGeneration;
    private Instant snapshotCollectedAt = Instant.now();
    private volatile int choicesGeneration;
    private TextView status;
    private TextView coverage;
    private CollectionProgressPanel progressPanel;
    private AndroidUpdater updater;
    private final String[] sourceDispositions = {"Not read", "Not read", "Not read"};
    private TextView review;
    private TextView supportCodeView;
    private Button copySupportCode;
    private SupportCode supportCode;
    private String safeSupportCodeWire = "";
    private String journeySessionId;
    private int diagnosticAttempts;
    private LinearLayout choices;
    private LinearLayout selectionPanel;
    private LinearLayout selectionHeader;
    private LinearLayout selectionPages;
    private LinearLayout actionBar;
    private ScrollView detailsScroll;
    private ViewTreeObserver.OnPreDrawListener pendingReviewReveal;
    private ScrollView contactScroll;
    private Button selectionNavigation;
    private boolean selectionVisible;
    private boolean keyboardVisible;
    private EditText search;
    private EditText country;
    private LinearLayout countryControls;
    private String nativeCountry;
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
    private boolean reviewingSelection;
    private Button reviewButton;
    private Button cancelHandoff;
    private TextView handoffReason;
    private TextView notificationNotice;
    private AlertDialog destinationDialog;
    private String destinationIntentShown;
    private int handoffReviewGeneration = -1;
    private boolean savedAckForCurrentReview;
    private int approvedSource;
    private int permissionPendingSource;
    private int activePermissionRequestCode;
    private int nextPermissionRequestCode = 1000;
    private AlertDialog collectionDisclosure;
    private NativeAuthorityTransport authorityTransport = new NativeAuthorityTransport.Https();
    private Supplier<Instant> setupClock = Instant::now;
    private Runnable nativeCollectionStart = this::beginNativeCollection;
    private Consumer<Intent> accountNavigator = this::startActivity;
    private final ArrayDeque<Integer> nativeReadQueue = new ArrayDeque<>();
    private NativeSetupSession nativeSetup;
    private NativeSetupContract.Claim nativeClaim;
    private NativeRunAuthority nativeRun;
    private NativeAccountReturn nativeAccountReturn;
    private SetupDiscovery setupDiscovery;
    private LinearLayout setupPanel;
    private LinearLayout localDetails;
    private TextView setupStatus;
    private TextView setupDestination;
    private CheckBox setupAgreement;
    private Button setupAction;
    private Button setupCancel;
    private Button[] sourceButtons;
    private final List<View> legacyCopy = new ArrayList<>();
    private boolean setupBusy;
    private boolean nativeStarted;
    private boolean nativeRunTransferred;
    private boolean nativeContinuePending;
    private String setupMessage = "";
    private final ServiceConnection connection = new ServiceConnection() {
        @Override public void onServiceConnected(ComponentName name, IBinder service) {
            handoff = (HandoffService.LocalBinder) service;
            if (handoff.nativeRun() != null && handoff.nativeRun().current()) {
                nativeRun = handoff.nativeRun(); nativeSetup = nativeRun.setup(); nativeClaim = nativeSetup.claimed();
                nativeStarted = nativeRunTransferred = true;
                nativeAccountReturn = handoff.accountReturn();
                setupMessage = "This phone is delivering a previously reviewed selection. No new source read was authorized.";
                progressPanel.nativeApproval();
            } else if (nativeSetup == null && nativeAccountReturn == null) nativeAccountReturn = handoff.accountReturn();
            if (cancelHandoffOnConnect) {
                handoff.cancel();
                cancelHandoffOnConnect = false;
            }
            // Validate the surviving service before its immediate observer callback can reveal a code/destination.
            recoverActiveHandoff();
            restoreCompletedHandoff();
            handoff.observe(MainActivity.this::showHandoff);
            refreshActions();
            updater.onSafetyChanged();
        }
        @Override public void onServiceDisconnected(ComponentName name) {
            handoff = null;
            reviewed = false;
            handoffButton.setEnabled(false);
            progressPanel.handoff("failed", 0, 0);
            review.setText("Browser handoff stopped. Review your selected metadata before approving a new transfer.");
            refreshActions();
        }
    };

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        String build = "unknown";
        try {
            build = getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (PackageManager.NameNotFoundException ignored) {
            // Unknown build is still a safe and bounded diagnostic value.
        }
        supportCode = new SupportCode(build, SystemClock.elapsedRealtimeNanos());
        acceptJourneyIntent(getIntent());
        LinearLayout screen = new LinearLayout(this);
        screen.setOrientation(LinearLayout.VERTICAL);
        screen.setBackgroundColor(BG);
        LinearLayout navigation = new LinearLayout(this);
        navigation.setOrientation(LinearLayout.VERTICAL);
        navigation.setPadding(dp(22), dp(8), dp(22), 0);
        screen.addView(navigation);
        selectionNavigation = secondaryButton("Return to contact selection", navigation);
        selectionNavigation.setOnClickListener(view -> showSelection(!selectionVisible));
        navigation.setVisibility(View.GONE);
        detailsScroll = new ScrollView(this);
        detailsScroll.setFillViewport(true);
        detailsScroll.setBackgroundColor(BG);
        LinearLayout body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(22), dp(28), dp(22), dp(40));
        detailsScroll.addView(body);
        screen.addView(detailsScroll, new LinearLayout.LayoutParams(-1, 0, 1));
        selectionPanel = new LinearLayout(this);
        selectionPanel.setOrientation(LinearLayout.VERTICAL);
        selectionPanel.setVisibility(View.GONE);
        screen.addView(selectionPanel, new LinearLayout.LayoutParams(-1, 0, 1));
        selectionHeader = new LinearLayout(this);
        selectionHeader.setOrientation(LinearLayout.VERTICAL);
        selectionHeader.setPadding(dp(22), 0, dp(22), 0);
        selectionPanel.addView(selectionHeader);
        contactScroll = new ScrollView(this);
        contactScroll.setFillViewport(true);
        selectionPanel.addView(contactScroll, new LinearLayout.LayoutParams(-1, 0, 1));
        actionBar = new LinearLayout(this);
        actionBar.setOrientation(LinearLayout.VERTICAL);
        actionBar.setPadding(dp(22), dp(8), dp(22), dp(16));
        screen.addView(actionBar);
        // Target-SDK edge-to-edge can place this fixed action area behind the navigation bar.
        // Keep the applicable action and Cancel above the system navigation inset.
        screen.setOnApplyWindowInsetsListener((view, insets) -> {
            navigation.setPadding(dp(22), dp(8) + Math.max(0, insets.getSystemWindowInsetTop()), dp(22), 0);
            actionBar.setPadding(dp(22), dp(8), dp(22), dp(16) +
                    Math.max(0, insets.getSystemWindowInsetBottom()));
            if (Build.VERSION.SDK_INT >= 30) {
                keyboardVisible = insets.isVisible(WindowInsets.Type.ime());
            }
            updateSelectionControls();
            return insets;
        });
        setContentView(screen);
        getWindow().setNavigationBarColor(BG);
        if (Build.VERSION.SDK_INT >= 29) getWindow().setNavigationBarContrastEnforced(false);
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController bars = getWindow().getInsetsController();
            if (bars != null) bars.setSystemBarsAppearance(0, WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
        } else {
            View decor = getWindow().getDecorView();
            decor.setSystemUiVisibility(decor.getSystemUiVisibility() & ~View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
            // Legacy content ancestors can consume insets before the screen listener receives them.
            decor.setOnApplyWindowInsetsListener((view, insets) -> {
                WindowInsets original = view.getRootWindowInsets();
                keyboardVisible = original != null &&
                        original.getSystemWindowInsetBottom() > original.getStableInsetBottom();
                updateSelectionControls();
                return view.onApplyWindowInsets(insets);
            });
        }

        TextView brand = text("AMPLIFai By Nexus", 24, TEXT);
        brand.setTypeface(null, Typeface.BOLD);
        body.addView(brand);
        setupPanel = new LinearLayout(this);
        setupPanel.setOrientation(LinearLayout.VERTICAL);
        setupPanel.setVisibility(View.GONE);
        body.addView(setupPanel);
        setupPanel.addView(text("Set up this phone collection", 21, ACCENT));
        setupDestination = text("", 16, TEXT);
        setupPanel.addView(setupDestination);
        TextView disclosure = text(NativeSetupContract.SENTENCE, 16, MUTED);
        setupPanel.addView(disclosure);
        setupAgreement = new CheckBox(this);
        setupAgreement.setText(NativeSetupContract.AGREEMENT);
        setupAgreement.setTextColor(TEXT);
        setupAgreement.setButtonTintList(android.content.res.ColorStateList.valueOf(ACCENT));
        setupAgreement.setOnCheckedChangeListener((button, checked) -> refreshSetup());
        setupPanel.addView(setupAgreement);
        setupStatus = text("", 14, MUTED);
        setupStatus.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        setupPanel.addView(setupStatus);
        setupAction = button("Check setup", setupPanel);
        setupAction.setOnClickListener(view -> performSetupAction());
        setupCancel = secondaryButton("Cancel this collection", setupPanel);
        setupCancel.setOnClickListener(view -> cancelNativeSetup());
        secondaryButton("Open signed-in account", setupPanel).setOnClickListener(view -> openAccount());
        LinearLayout policy = disclosure("Account privacy and phone supplement", setupPanel);
        secondaryButton("Read account privacy", policy).setOnClickListener(view -> openPolicy(NativeSetupContract.PRIVACY_PATH));
        secondaryButton("Read phone platform supplement", policy).setOnClickListener(view -> openPolicy(NativeSetupContract.SUPPLEMENT_PATH));
        localDetails = new LinearLayout(this);
        localDetails.setOrientation(LinearLayout.VERTICAL);
        body.addView(localDetails);
        body = localDetails;
        body.addView(text("Phone metadata review", 21, ACCENT));
        addLegacyCopy("Read Contacts, choose who to include, then review. Calls and SMS are optional.", 16, body);

        countryControls = new LinearLayout(this);
        countryControls.setOrientation(LinearLayout.VERTICAL);
        body.addView(countryControls);
        country = new EditText(this);
        country.setSingleLine(true);
        country.setText(Locale.getDefault().getCountry().toUpperCase(Locale.ROOT));
        country.setHint("US");
        country.setTextColor(TEXT);
        country.setHintTextColor(MUTED);
        country.setBackgroundTintList(android.content.res.ColorStateList.valueOf(ACCENT));
        countryControls.addView(text("Country code (2 letters, for example US)", 13, MUTED));
        countryControls.addView(country);

        Button readContacts = button("Read contacts", body);
        readContacts.setOnClickListener(view -> readSource(CONTACTS_REQUEST));
        LinearLayout optionalSources = disclosure("Optional: Calls and SMS", body);
        optionalSources.addView(text("Allow either, both or neither. Denied sources remain unavailable.", 16, MUTED));
        Button readCalls = secondaryButton("Read call history", optionalSources);
        readCalls.setOnClickListener(view -> readSource(CALLS_REQUEST));
        Button readMessages = secondaryButton("Read SMS history", optionalSources);
        readMessages.setOnClickListener(view -> readSource(SMS_REQUEST));
        sourceButtons = new Button[]{readContacts, readCalls, readMessages};
        cancel = button("Cancel current read", body);
        cancel.setEnabled(false);
        cancel.setVisibility(View.GONE);
        cancel.setOnClickListener(view -> { if (cancellation != null) cancellation.cancel(); });
        status = text("No source read yet.", 14, MUTED);
        status.setPadding(0, dp(12), 0, dp(6));
        status.setFocusableInTouchMode(true);
        body.addView(status);
        coverage = text("Contacts, calls and SMS are not yet observed.", 13, MUTED);
        body.addView(coverage);
        supportCodeView = text("", 12, MUTED);
        body.addView(supportCodeView);
        copySupportCode = button("Copy safe support code", body);
        copySupportCode.setEnabled(false);
        copySupportCode.setVisibility(View.GONE);
        copySupportCode.setOnClickListener(view -> {
            if (!copySupportCode.isEnabled() || safeSupportCodeWire.isEmpty()) return;
            getSystemService(ClipboardManager.class).setPrimaryClip(
                    ClipData.newPlainText("AMPLIFai support code", safeSupportCodeWire));
            status.setText("Safe support code copied. Submit it through AMPLIFai contact if you want help; it contains no phone records or account credentials.");
        });

        addLegacyCopy("Search by name or phone ending. Only selected contacts and their matched available interactions belong in a later, separately consented account transfer.", 13, body);
        search = new EditText(this);
        search.setSingleLine(true);
        search.setHint("Search contacts");
        search.setContentDescription("Search contacts by name or phone ending");
        search.setTextColor(TEXT);
        search.setHintTextColor(MUTED);
        search.setBackgroundTintList(android.content.res.ColorStateList.valueOf(ACCENT));
        search.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) { pageOffset = 0; renderChoices(); }
            @Override public void afterTextChanged(Editable s) {}
        });
        selectionHeader.addView(search);
        LinearLayout selectionBulkActions = new LinearLayout(this);
        selectionBulkActions.setOrientation(LinearLayout.HORIZONTAL);
        selectionHeader.addView(selectionBulkActions);
        selectAllContacts = button("Select all contacts", selectionBulkActions);
        selectAllContacts.setContentDescription("Select all contacts across every page, including contacts hidden by search");
        LinearLayout.LayoutParams selectAllParams = new LinearLayout.LayoutParams(0, -2, 1);
        selectAllParams.topMargin = dp(8);
        selectAllParams.rightMargin = dp(4);
        selectAllContacts.setLayoutParams(selectAllParams);
        selectAllContacts.setOnClickListener(view -> selectEveryContact());
        cancelSelectAll = button("Cancel all-contact selection", actionBar);
        cancelSelectAll.setVisibility(View.GONE);
        cancelSelectAll.setOnClickListener(view -> {
            if (!bulkSelectionActive) return;
            cancelBulkSelection();
            status.setText("All-contact selection cancelled. Your previous contact choices are unchanged.");
            updater.onSafetyChanged();
        });
        clearSelectedContacts = secondaryButton("Deselect all", selectionBulkActions);
        clearSelectedContacts.setContentDescription("Deselect all contacts across every page, including contacts hidden by search");
        LinearLayout.LayoutParams deselectAllParams = new LinearLayout.LayoutParams(0, -2, 1);
        deselectAllParams.topMargin = dp(8);
        deselectAllParams.leftMargin = dp(4);
        clearSelectedContacts.setLayoutParams(deselectAllParams);
        clearSelectedContacts.setOnClickListener(view -> clearContactSelection());
        choices = new LinearLayout(this);
        choices.setOrientation(LinearLayout.VERTICAL);
        choices.setPadding(dp(22), 0, dp(22), dp(16));
        contactScroll.addView(choices);
        pageIndicator = text("", 12, MUTED);
        pageIndicator.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        selectionHeader.addView(pageIndicator);
        selectionPages = new LinearLayout(this);
        selectionPages.setOrientation(LinearLayout.HORIZONTAL);
        selectionHeader.addView(selectionPages);
        previousPage = button("Previous", selectionPages);
        previousPage.setContentDescription("Previous contacts");
        LinearLayout.LayoutParams previousParams = new LinearLayout.LayoutParams(0, -2, 1);
        previousParams.topMargin = dp(8);
        previousParams.rightMargin = dp(4);
        previousPage.setLayoutParams(previousParams);
        previousPage.setOnClickListener(view -> { pageOffset = Math.max(0, pageOffset - 100); renderChoices(); });
        nextPage = button("Next", selectionPages);
        nextPage.setContentDescription("Next contacts");
        LinearLayout.LayoutParams nextParams = new LinearLayout.LayoutParams(0, -2, 1);
        nextParams.topMargin = dp(8);
        nextParams.leftMargin = dp(4);
        nextPage.setLayoutParams(nextParams);
        nextPage.setOnClickListener(view -> { pageOffset += 100; renderChoices(); });
        reviewButton = button("Review selected metadata", actionBar);
        reviewButton.setOnClickListener(view -> reviewSelected());
        review = text("No contacts selected. Review stays local until you approve browser handoff; account saves need separate consent.", 14, MUTED);
        review.setFocusableInTouchMode(true);
        body.addView(review);
        handoffButton = button("Approve browser handoff", actionBar);
        handoffButton.setEnabled(false);
        handoffButton.setOnClickListener(view -> {
            if (completedHandoff() || nativeSetup != null && nativeSetup.state() == NativeSetupSession.State.CLOSED) openAccount();
            else if (handoff != null && handoff.snapshot().state().equals("binding_pending")) handoff.retryBinding();
            else if (handoff != null && handoff.snapshot().active()) openAccount();
            else startHandoff();
        });
        handoffReason = text("Read Contacts first.", 14, MUTED);
        handoffReason.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        body.addView(handoffReason);
        cancelHandoff = secondaryButton("Cancel browser handoff", actionBar);
        cancelHandoff.setOnClickListener(view -> {
            if (nativeSetup != null) { cancelNativeSetup(); return; }
            if (transferPreparationCancellation != null) {
                invalidateReview();
                status.setText("Cancelling local preparation. No new handoff will start.");
                return;
            }
            if (handoff == null || !handoff.snapshot().active()) {
                status.setText("No active browser handoff to cancel.");
                return;
            }
            invalidateReview();
            status.setText("Browser handoff cancelled. Review again before starting a new code.");
            review.setText("The previous pairing code is no longer available. No account save was made by cancelling.");
        });
        Button openAccount = secondaryButton("Open account in this phone's browser", body);
        openAccount.setOnClickListener(view -> openAccount());
        addLegacyCopy("Return to AMPLIFai Phone from Recent apps to confirm the account and course or Cancel. Approve each Save in the browser.", 16, body);
        progressPanel = new CollectionProgressPanel(this, body, this::openAccount);
        LinearLayout help = disclosure("Privacy and permission help", body);
        addLegacyCopy("Each read needs your approval and Android permission. Message text and attachments are never read. Sanitized error reports may be sent automatically.", 16, help);
        help.addView(text("Contacts is required for this task. Calls and SMS can stay unavailable if Android or the installer restricts access. Change permissions in Android Settings → Apps; keep device protections on.", 16, MUTED));
        addLegacyCopy("Pair only on this phone. The one-use code expires after five minutes; an active review can remain connected longer. Notifications are optional. Return from Recent apps to confirm the destination or Cancel; an enabled notification adds a shortcut.", 16, help);
        notificationNotice = text("Handoff notifications are optional. Checking their current setting…", 14, MUTED);
        help.addView(notificationNotice);
        Button notificationSettings = secondaryButton("Handoff notifications (optional)", help);
        notificationSettings.setOnClickListener(view -> configureNotifications());
        Button protection = secondaryButton("How this review is protected", help);
        protection.setOnClickListener(view -> new AlertDialog.Builder(this)
                .setTitle("Phone review and account security")
                .setMessage("Temporary review stays in this app's private storage on your phone. The app does not make an encrypted phone backup or add its own encryption to that temporary storage. The preview travels only between this phone's app and browser through a one-use local connection; it is not end-to-end encrypted. " +
                        (nativeSetup == null ? "Saving selected metadata to your account uses a secure web connection and requires a separate approval. " :
                                "Continue sends the reviewed selection for saving under this collection's account approval over a secure web connection. ") +
                        "Message text and attachments are never read.")
                .setPositiveButton("Close", null).show());
        Button privacyNotice = secondaryButton("Read AMPLIFai privacy notice", help);
        privacyNotice.setOnClickListener(view -> startActivity(new Intent(Intent.ACTION_VIEW,
                Uri.parse(LocalBridge.ACCOUNT_ORIGIN + "/experience/account-privacy"))));
        Button accountData = secondaryButton("Manage saved account sources", help);
        accountData.setOnClickListener(view -> openAccount());
        updater = new AndroidUpdater(this, help, this::canInstallUpdate);

        try { store = new SanitizedStore(this); }
        catch (SQLiteException unavailable) {
            for (int index = 0; index < sourceDispositions.length; index++) sourceDispositions[index] = "Unavailable";
            failure("storage", "unavailable", "Private phone storage is unavailable. Free local space and reopen the app before reading sources.");
            showCoverage();
        }
        renderChoices();
        acceptSetupIntent(getIntent());
        if (NativeSetupSupport.ENABLED) {
            try { setupDiscovery = new SetupDiscovery(installedSetupClient(), SetupDiscovery.PORT); }
            catch (java.io.IOException | RuntimeException unavailable) { /* Discovery failure grants no setup authority. */ }
        }
    }

    private void addLegacyCopy(String value, int size, LinearLayout parent) {
        TextView view = text(value, size, MUTED); parent.addView(view); legacyCopy.add(view);
    }

    private NativeSetupContract.Client installedSetupClient() {
        try {
            android.content.pm.PackageInfo info = getPackageManager().getPackageInfo(getPackageName(), 0);
            if (!NativeSetupSupport.ENABLED || !NativeSetupContract.PACKAGE.equals(info.packageName))
                throw new IllegalArgumentException("setup_distribution_unsupported");
            long build = Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
            return new NativeSetupContract.Client(info.versionName, Long.toString(build));
        } catch (PackageManager.NameNotFoundException unavailable) { throw new IllegalArgumentException("setup_identity_unavailable"); }
    }

    private void acceptSetupIntent(Intent intent) {
        if (intent == null || intent.getData() == null || !"setup".equals(intent.getData().getHost())) return;
        String link = intent.getData().toString();
        intent.setData(null); // Do not retain the bootstrap URL in this Activity's current Intent.
        if (!NativeSetupSupport.ENABLED) { status.setText("This distribution does not support native account setup."); return; }
        if ((nativeSetup != null && nativeSetup.state() != NativeSetupSession.State.CLOSED) || cancellation != null || preparingTransfer ||
                permissionPendingSource != 0 || collectionDisclosure != null || bulkSelectionActive || reviewingSelection ||
                (handoff != null && handoff.snapshot().active())) {
            status.setText("Cancel the current collection before opening a different setup."); return;
        }
        try {
            NativeSetupSession session = new NativeSetupSession(NativeSetupContract.link(link), installedSetupClient());
            for (int source : new int[]{CONTACTS_REQUEST, CALLS_REQUEST, SMS_REQUEST}) {
                if (!clearSource(source)) { session.close(); return; }
            }
            selectedIds.clear(); reviewed = false; nativeStarted = false;
            NativeCompletionState.PROCESS.beginAttempt();
            nativeRun = null; nativeClaim = null; setupBusy = nativeRunTransferred = false;
            nativeAccountReturn = null;
            nativeCountry = null;
            nativeSetup = session;
            if (countryControls.getParent() != setupPanel) {
                ((LinearLayout) countryControls.getParent()).removeView(countryControls);
                setupPanel.addView(countryControls, 2);
            }
            setupAgreement.setChecked(false);
            setupMessage = "Check this setup with your signed-in account. No source permission or collection has started.";
            showSelection(false); refreshSetup();
            performSetupAction();
        } catch (IllegalArgumentException invalid) {
            status.setText("This setup link is invalid or unsupported. Start a new setup from your signed-in account.");
        }
    }

    private void openPolicy(String path) {
        try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(LocalBridge.ACCOUNT_ORIGIN + path))); }
        catch (ActivityNotFoundException | SecurityException unavailable) { setupMessage = "Android could not open the privacy notice. No approval changed."; refreshSetup(); }
    }

    private void refreshSetup() {
        if (setupPanel == null || nativeSetup == null) return;
        setupPanel.setVisibility(View.VISIBLE);
        localDetails.setVisibility(nativeStarted ? View.VISIBLE : View.GONE);
        for (View view : legacyCopy) view.setVisibility(View.GONE);
        NativeSetupSession.State state = nativeSetup.state();
        setupDestination.setText(nativeClaim == null ? "Checking account and course…" :
                "Account: " + nativeClaim.scope().email() + "\nCourse: " + nativeClaim.scope().course() +
                        "\nSources: " + String.join(", ", nativeClaim.scope().categories()));
        setupAgreement.setVisibility(state == NativeSetupSession.State.AWAITING_APPROVAL ? View.VISIBLE : View.GONE);
        setupAgreement.setEnabled(!setupBusy);
        country.setEnabled(!setupBusy && !nativeStarted &&
                (nativeRun == null || nativeRun.state() != NativeRunAuthority.State.START_PENDING));
        setupAction.setText(state == NativeSetupSession.State.CLAIM_PENDING ? "Retry setup check" :
                state == NativeSetupSession.State.AWAITING_APPROVAL ? "Approve this collection" :
                state == NativeSetupSession.State.APPROVAL_PENDING ? "Retry approval receipt" :
                nativeRun != null && nativeRun.state() == NativeRunAuthority.State.START_PENDING ? "Retry Start collection" : "Start collection");
        setupAction.setVisibility(nativeStarted || state == NativeSetupSession.State.CLOSED ? View.GONE : View.VISIBLE);
        setupAction.setEnabled(!setupBusy && state != NativeSetupSession.State.CLOSED &&
                (state != NativeSetupSession.State.AWAITING_APPROVAL || setupAgreement.isChecked()));
        setupCancel.setEnabled(state != NativeSetupSession.State.CLOSED);
        setupStatus.setText(setupMessage);
        if (sourceButtons != null) for (int i = 0; i < sourceButtons.length; i++) {
            sourceButtons[i].setEnabled(nativeRun != null && nativeRun.canRead(categoryFor(CONTACTS_REQUEST + i)) && !setupBusy &&
                    cancellation == null && permissionPendingSource == 0 && !preparingTransfer && (handoff == null || !handoff.snapshot().active()));
        }
        if (!nativeStarted) {
            actionBar.setVisibility(View.GONE);
            selectionPanel.setVisibility(View.GONE);
            ((View) selectionNavigation.getParent()).setVisibility(View.GONE);
            detailsScroll.setVisibility(View.VISIBLE);
        } else actionBar.setVisibility(View.VISIBLE);
    }

    private void performSetupAction() {
        if (nativeSetup == null || setupBusy || isDestroyed()) return;
        NativeSetupSession session = nativeSetup;
        NativeSetupSession.State state = session.state();
        if (state == NativeSetupSession.State.CLOSED) return;
        boolean checked = setupAgreement.isChecked();
        if (state == NativeSetupSession.State.AWAITING_APPROVAL && !checked) return;
        if (state == NativeSetupSession.State.READY && (!started || updater.blocksCollection() || store == null || handoff == null)) {
            setupMessage = "Return to the app and finish any update before starting collection."; refreshSetup(); return;
        }
        if (state == NativeSetupSession.State.READY && !country.getText().toString().trim().toUpperCase(Locale.ROOT).matches("[A-Z]{2}")) {
            setupMessage = "Set a two-letter country code before starting collection.";
            refreshSetup(); return;
        }
        if (state == NativeSetupSession.State.READY && nativeCountry == null)
            nativeCountry = country.getText().toString().trim().toUpperCase(Locale.ROOT);
        setupBusy = true; setupMessage = "Checking the exact account authorization…"; refreshSetup();
        executor.execute(() -> {
            NativeSetupContract.Claim claim = null;
            NativeRunAuthority run = nativeRun;
            boolean success = false, retryable = true;
            try {
                if (state == NativeSetupSession.State.CLAIM_PENDING) {
                    claim = session.acceptClaim(authorityTransport.post(NativeAuthorityTransport.Route.CLAIM, session.claimRequest()), setupClock.get());
                } else if (state == NativeSetupSession.State.AWAITING_APPROVAL || state == NativeSetupSession.State.APPROVAL_PENDING) {
                    byte[] request = state == NativeSetupSession.State.APPROVAL_PENDING ? session.retryApproval() : session.beginApproval(checked, setupClock.get());
                    session.acceptApproval(authorityTransport.post(NativeAuthorityTransport.Route.APPROVE, request), setupClock.get());
                    run = new NativeRunAuthority(session, authorityTransport, setupClock, () -> java.util.UUID.randomUUID().toString());
                } else if (state == NativeSetupSession.State.READY && run != null) run.start();
                else throw new IllegalArgumentException("setup_state_invalid");
                success = true;
            } catch (NativeAuthorityTransport.Failure failure) { retryable = failure.retryable; }
            catch (IllegalArgumentException invalid) { /* A malformed success cannot grant authority; keep only exact retry material. */ }
            NativeSetupContract.Claim receivedClaim = claim;
            NativeRunAuthority receivedRun = run;
            boolean accepted = success, retry = retryable;
            runOnUiThread(() -> {
                if (isDestroyed() || nativeSetup != session || session.state() == NativeSetupSession.State.CLOSED) return;
                setupBusy = false;
                if (accepted) {
                    if (receivedClaim != null) nativeClaim = receivedClaim;
                    if (receivedRun != null) {
                        nativeRun = receivedRun;
                        try {
                            NativeSetupContract.Approval approval = receivedRun.approval();
                            if (nativeAccountReturn == null) nativeAccountReturn = new NativeAccountReturn(
                                    approval.runId(), approval.scope().enrollmentId(), null, null);
                        } catch (IllegalArgumentException expired) {
                            receivedRun.close(); setupMessage = "This collection expired. Start a new setup from your signed-in account.";
                            refreshActions(); return;
                        }
                    }
                    setupMessage = state == NativeSetupSession.State.CLAIM_PENDING ? "Review the account, course and disclosure before approving." :
                            state == NativeSetupSession.State.READY ? "Start confirmed for this account. Android permissions are still required before reading sources. Choose and review contacts, then Continue to save that selection." :
                                    "Approval confirmed. Start collection is a separate action; Android permissions are still required.";
                    if (state == NativeSetupSession.State.READY) {
                        if (!started) { cancelNativeSetup(); return; }
                        nativeStarted = true;
                        progressPanel.nativeApproval();
                        review.setText("Choose contacts and review their available metadata. Continue sends this selection for saving to the approved account and course.");
                    }
                } else {
                    if (!retry) { if (receivedRun != null) receivedRun.close(); else session.close(); }
                    setupMessage = retry ? "The response was unavailable or could not be verified. Retry the same request or Cancel. No source read started." :
                            "This setup is unsupported, changed, stopped or expired. Start a new setup from your signed-in account.";
                }
                refreshActions(); refreshSetup();
                if (accepted && state == NativeSetupSession.State.READY) nativeCollectionStart.run();
            });
        });
    }

    private void cancelNativeSetup() {
        if (nativeSetup == null) return;
        nativeReadQueue.clear();
        if (cancellation != null) cancellation.cancel();
        if (transferPreparationCancellation != null) transferPreparationCancellation.cancel();
        cancelBulkSelection();
        if (handoff != null) handoff.cancel();
        if (nativeRun != null) nativeRun.close(); else nativeSetup.close();
        approvedSource = permissionPendingSource = activePermissionRequestCode = 0;
        setupBusy = false; nativeStarted = false;
        nativeContinuePending = false;
        invalidateReview();
        setupMessage = "Stopped on this phone. Saved results remain in your account. Return to your account to check results or stop this collection.";
        showSelection(false); refreshSetup();
    }

    private void beginNativeCollection() {
        nativeReadQueue.clear();
        if (nativeRun == null || !nativeRun.current()) return;
        for (int source : new int[]{CONTACTS_REQUEST, CALLS_REQUEST, SMS_REQUEST})
            if (nativeRun.approval().scope().categories().contains(categoryFor(source))) nativeReadQueue.add(source);
        continueNativeCollection();
    }

    private void continueNativeCollection() {
        if (started && nativeRun != null && nativeRun.current() && cancellation == null && permissionPendingSource == 0 && !nativeReadQueue.isEmpty())
            startApprovedRead(nativeReadQueue.remove());
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
        styleButton(button, true);
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, -2);
        params.topMargin = dp(8);
        parent.addView(button, params);
        return button;
    }

    private void styleButton(Button button, boolean primary) {
        int[][] states = {new int[]{-android.R.attr.state_enabled}, new int[]{}};
        button.setTextColor(new android.content.res.ColorStateList(states,
                new int[]{Color.rgb(161, 169, 181), primary ? BG : TEXT}));
        button.setBackgroundTintList(new android.content.res.ColorStateList(states,
                new int[]{Color.rgb(38, 49, 70), primary ? ACCENT : Color.rgb(38, 49, 70)}));
    }

    private Button secondaryButton(String label, LinearLayout parent) {
        Button value = button(label, parent);
        styleButton(value, false);
        return value;
    }

    private LinearLayout disclosure(String label, LinearLayout parent) {
        Button toggle = secondaryButton("+ " + label, parent);
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setVisibility(View.GONE);
        parent.addView(content);
        toggle.setOnClickListener(view -> {
            boolean expanded = content.getVisibility() != View.VISIBLE;
            content.setVisibility(expanded ? View.VISIBLE : View.GONE);
            toggle.setText((expanded ? "− " : "+ ") + label);
        });
        return content;
    }

    private void showSelection(boolean visible) {
        boolean available = contactsAvailable && store != null && (nativeSetup == null || nativeStarted);
        selectionVisible = available && visible;
        ((View) selectionNavigation.getParent()).setVisibility(available ? View.VISIBLE : View.GONE);
        selectionNavigation.setText(selectionVisible ? "Sources & options" : "Return to contact selection");
        selectionNavigation.setContentDescription(selectionVisible
                ? "Sources, progress and privacy help" : "Return to contact selection");
        selectionPanel.setVisibility(selectionVisible ? View.VISIBLE : View.GONE);
        detailsScroll.setVisibility(selectionVisible ? View.GONE : View.VISIBLE);
        updateSelectionControls();
        refreshSetup();
    }

    private void updateSelectionControls() {
        // Keep Search, the count and actions usable above the IME; restore secondary controls on dismissal.
        // The navigation container retains its status-bar inset even when its button is hidden.
        boolean compact = selectionVisible && keyboardVisible;
        selectionNavigation.setVisibility(compact ? View.GONE : View.VISIBLE);
        if (selectionPages != null) selectionPages.setVisibility(compact ? View.GONE : View.VISIBLE);
        if (pageIndicator != null) {
            String count = compact ? getString(R.string.selection_all_contacts_count, selectedIds.size()) : selectionPageText;
            if (!count.contentEquals(pageIndicator.getText())) pageIndicator.setText(count);
        }
        selectionActionLabel(reviewButton, "Review selected metadata", "Review", compact);
        String shortHandoff = handoffActionText.equals("Retry Continue") ? "Retry Continue" :
                handoffActionText.equals("Open account in this phone's browser") ? "Open account" :
                nativeSetup == null ? "Approve handoff" : "Continue";
        selectionActionLabel(handoffButton, handoffActionText, shortHandoff, compact);
        selectionActionLabel(cancelSelectAll, "Cancel all-contact selection", "Cancel selection", compact);
        selectionActionLabel(cancelHandoff, cancelHandoffText,
                nativeSetup != null ? "Cancel collection" : preparingTransfer ? "Cancel preparation" : "Cancel handoff", compact);
    }

    private void selectionActionLabel(Button control, String full, String shortLabel, boolean compact) {
        if (control == null) return;
        String label = compact ? shortLabel : full;
        if (!label.contentEquals(control.getText())) control.setText(label);
        if (!full.equals(control.getContentDescription())) control.setContentDescription(full);
    }

    private void openAccount() {
        if (!recoverActiveHandoff()) return;
        launchAccount(accountReturnHref());
    }

    private void launchAccount(String target) {
        status.setText(nativeSetup == null ? "Return to AMPLIFai Phone from Recent apps to confirm the destination or Cancel. Save stays in the browser." :
                "Open your signed-in account to receive this collection or check saved results. Android chooses the browser; return from Recent apps to Cancel.");
        try {
            accountNavigator.accept(new Intent(Intent.ACTION_VIEW, Uri.parse(target)));
        } catch (ActivityNotFoundException | SecurityException unavailable) {
            status.setText("Android could not open the account. Use Open account to try again, or visit " +
                    LocalBridge.ACCOUNT_ORIGIN + "/phone/account in your browser and return here.");
        }
    }

    private void openNativeContinuation() {
        if (!nativeContinuePending) return;
        if (!started || isDestroyed()) { nativeContinuePending = false; return; }
        if (nativeRun == null || nativeRun.state() != NativeRunAuthority.State.BOUND || !nativeRun.current() || nativeAccountReturn == null) return;
        nativeContinuePending = false; // One automatic navigation attempt for the initiating Continue, including bind retries.
        launchAccount(nativeAccountReturn.href(true));
    }

    private String accountReturnHref() {
        if (nativeAccountReturn != null) return nativeAccountReturn.href(handoff != null && handoff.snapshot().active() &&
                nativeRun != null && nativeRun.state() == NativeRunAuthority.State.BOUND && nativeRun.current());
        return nativeClaim != null ? NativeAccountReturn.account(nativeClaim.scope().enrollmentId()) : LocalBridge.ACCOUNT_ORIGIN + "/phone/account";
    }

    private void refreshActions() {
        if (handoffReason == null) return;
        boolean active = handoff != null && handoff.snapshot().active();
        boolean completed = completedHandoff();
        boolean nativeStopped = nativeSetup != null && nativeSetup.state() == NativeSetupSession.State.CLOSED && nativeStarted;
        boolean busy = cancellation != null || permissionPendingSource != 0 || collectionDisclosure != null ||
                preparingTransfer || bulkSelectionActive || reviewingSelection;
        boolean canReview = started && store != null && contactsAvailable && !selectedIds.isEmpty() &&
                !busy && !active && !updater.blocksCollection() && (nativeSetup == null ||
                (nativeRun != null && nativeRun.state() == NativeRunAuthority.State.STARTED && nativeRun.current()));
        reviewButton.setEnabled(canReview);
        reviewButton.setVisibility(active || completed || nativeStopped || (contactsAvailable && (reviewed || bulkSelectionActive)) ? View.GONE : View.VISIBLE);
        styleButton(reviewButton, !reviewed);
        boolean bindingRetry = active && handoff.snapshot().state().equals("binding_pending");
        handoffActionText = completed ? "Open saved account and report" : bindingRetry ? "Retry Continue" : active || nativeStopped ? "Open account in this phone's browser" :
                nativeSetup == null ? "Approve browser handoff" : "Continue with selected contacts";
        handoffButton.setVisibility(!bulkSelectionActive && (!contactsAvailable || active || completed || nativeStopped || preparingTransfer || reviewed)
                ? View.VISIBLE : View.GONE);
        handoffButton.setEnabled(completed || nativeStopped || (active && (nativeSetup == null || !handoff.snapshot().state().equals("starting"))) || (canReview && reviewed && handoff != null &&
                handoff.storageAvailable() && !PagedTransfer.cleanupRequired()));
        cancelHandoff.setVisibility(active || preparingTransfer || (nativeSetup != null && nativeStarted && !nativeStopped && !bulkSelectionActive) ? View.VISIBLE : View.GONE);
        cancelHandoffText = nativeSetup != null ? "Cancel this collection" : preparingTransfer ? "Cancel preparation" : "Cancel browser handoff";
        String reason;
        if (active) reason = "Handoff active. Open this phone's browser, or Cancel here.";
        else if (store == null) reason = "Private storage unavailable. Reopen after freeing space.";
        else if (handoff != null && !handoff.storageAvailable()) reason = "Private transfer cleanup failed. Close and reopen the app before another handoff.";
        else if (PagedTransfer.cleanupRequired()) reason = "Private transfer cleanup failed. Close and reopen the app before another handoff.";
        else if (completed) reason = "Account save confirmed. Open your saved account and report; a new collection needs a new setup.";
        else if (!started) reason = "Return to the app to continue.";
        else if (nativeSetup != null && (nativeRun == null || !nativeRun.current())) reason = "This collection is not current. Start a new setup from your signed-in account.";
        else if (updater.blocksCollection()) reason = "Finish or cancel the app update first.";
        else if (preparingTransfer) reason = "Preparing the reviewed selection…";
        else if (reviewingSelection) reason = "Reviewing the current selection…";
        else if (bulkSelectionActive) reason = "Finish or cancel all-contact selection first.";
        else if (permissionPendingSource != 0 || collectionDisclosure != null) reason = "Finish the current source approval first.";
        else if (cancellation != null) reason = "Finish or cancel the current read first.";
        else if (!contactsAvailable) reason = "Read Contacts first. Calls and SMS are optional.";
        else if (selectedIds.isEmpty()) reason = "Select contacts, then review them.";
        else if (!reviewed) reason = "Review the current selection before browser handoff.";
        else if (handoff == null) reason = "Connecting local handoff controls…";
        else reason = nativeSetup == null ? "Ready for your approval. Account Save is a separate browser action." :
                "Continue sends only this reviewed selection for saving to the approved account and course.";
        handoffReason.setText(reason);
        showSelection(selectionVisible);
        if (notificationNotice != null) notificationNotice.setText(nativeSetup != null
                ? "Handoff notifications are optional. Return from Recent apps to check delivery or Cancel. Android chooses the browser used for your account."
                : handoff == null
                ? "Handoff notifications are optional. Return from Recent apps to confirm or Cancel."
                : handoff.notificationsAvailable()
                ? "Handoff notifications are enabled. You can also return from Recent apps to confirm or Cancel."
                : "Handoff notifications are off. Return from Recent apps to confirm or Cancel. Notification access is optional.");
    }

    private void configureNotifications() {
        // Optional owner action only. This callback never collects, prepares, pairs or saves.
        if (!started) return;
        if (Build.VERSION.SDK_INT >= 33 &&
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{ Manifest.permission.POST_NOTIFICATIONS }, NOTIFICATIONS_REQUEST);
            return;
        }
        try {
            startActivity(new Intent(android.provider.Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS)
                    .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, getPackageName())
                    .putExtra(android.provider.Settings.EXTRA_CHANNEL_ID, "phone-handoff"));
        } catch (ActivityNotFoundException | SecurityException unavailable) {
            status.setText("Notification settings could not open. Notifications are optional; return here to confirm or Cancel.");
        }
    }

    private static String permissionFor(int source) {
        return source == CONTACTS_REQUEST ? Manifest.permission.READ_CONTACTS
                : source == CALLS_REQUEST ? Manifest.permission.READ_CALL_LOG : Manifest.permission.READ_SMS;
    }

    private void readSource(int source) {
        if (nativeSetup != null) {
            if (nativeRun == null || !nativeRun.canRead(categoryFor(source))) {
                status.setText("This source is outside the current collection or was already read. A new collection needs a new setup and Start."); return;
            }
            startApprovedRead(source); return;
        }
        if (updater.blocksCollection()) { status.setText("Finish or cancel the Android update before reading a source."); return; }
        if (!started || permissionPendingSource != 0 || cancellation != null || collectionDisclosure != null ||
                preparingTransfer || bulkSelectionActive || store == null) {
            status.setText("Finish the current approval or read before starting another source.");
            return;
        }
        CheckBox agreement = new CheckBox(this);
        agreement.setChecked(false);
        agreement.setTextColor(BG);
        agreement.setText("I approve this one local " + sourceName(source).toLowerCase(Locale.ROOT) + " metadata read. Saving and browser handoff are separate decisions.");
        agreement.setPadding(dp(16), dp(8), dp(16), dp(8));
        AlertDialog dialog = new AlertDialog.Builder(this)
                .setTitle("Before reading " + sourceName(source).toLowerCase(Locale.ROOT))
                .setMessage((source == CONTACTS_REQUEST ? "Read names and phone numbers." :
                        source == CALLS_REQUEST ? "Read the other party's number, date, direction and call duration where available." :
                        "Read the other party's number, date and direction. Message text and attachments are never read.") +
                        "\n\nMetadata stays on this phone until you review and approve handoff. Account Save needs separate approval. Android permission is separate; Not now requests nothing. Sanitized error reports may be sent automatically.")
                .setView(agreement)
                .setNegativeButton("Not now", (ignored, which) -> {})
                .setPositiveButton("Approve one read", (ignored, which) -> {
                    if (agreement.isChecked() && started) startApprovedRead(source);
                })
                .create();
        collectionDisclosure = dialog;
        dialog.setOnDismissListener(ignored -> {
            if (collectionDisclosure == dialog) collectionDisclosure = null;
            refreshActions();
        });
        dialog.setOnShowListener(ignored -> {
            Button approve = dialog.getButton(AlertDialog.BUTTON_POSITIVE);
            approve.setEnabled(false);
            agreement.setOnCheckedChangeListener((button, checked) -> approve.setEnabled(checked));
        });
        dialog.show();
        refreshActions();
    }

    private void startApprovedRead(int source) {
        if (!started) { status.setText("Return to the app before reading a source."); return; }
        if (nativeSetup != null && (nativeRun == null || !nativeRun.canRead(categoryFor(source)))) return;
        if (nativeSetup != null && handoff != null && handoff.snapshot().active()) return;
        if (updater.blocksCollection()) { status.setText("Android is handling an app update. No source read started."); return; }
        if (handoff == null) { failure("handoff", "unavailable", "Wait for the handoff controls to connect before reading a source."); return; }
        String iso = nativeSetup != null && nativeCountry != null ? nativeCountry : country.getText().toString().trim().toUpperCase(Locale.ROOT);
        if (!iso.matches("[A-Z]{2}")) {
            failure("permission", "invalid", "Enter a two-letter country code before reading a source.");
            return;
        }
        if (cancellation != null || permissionPendingSource != 0 || preparingTransfer || store == null) { status.setText("Cancel or finish the current operation first."); return; }
        if (nativeSetup == null) {
            NativeCompletionState.PROCESS.beginAttempt();
            nativeAccountReturn = null;
        }
        clearSupportCode();
        noteReviewDataChanged();
        if (!clearSource(source)) return;
        approvedSource = source;
        if (checkSelfPermission(permissionFor(source)) != PackageManager.PERMISSION_GRANTED) {
            progressPanel.awaitingPermission(sourceName(source));
            permissionPendingSource = source;
            activePermissionRequestCode = nextPermissionRequestCode++;
            if (nextPermissionRequestCode > 65000) nextPermissionRequestCode = 1000;
            status.setText("Requesting " + sourceName(source) + " access from Android…");
            refreshActions();
            requestPermissions(new String[]{ permissionFor(source) }, activePermissionRequestCode);
            return;
        }
        beginRead(source, iso);
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == NOTIFICATIONS_REQUEST) {
            status.setText(nativeSetup != null ? "Notification visibility is optional. Return from Recent apps to check this collection or Cancel."
                    : grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED
                    ? "Notification permission allowed. Channel settings may still hide the shortcut. Handoff and account Save need separate approval."
                    : "Notifications remain off. You can still approve a reviewed handoff; return from Recent apps to confirm or Cancel.");
            refreshActions();
            return;
        }
        if (requestCode != activePermissionRequestCode || permissionPendingSource == 0) return;
        int source = permissionPendingSource;
        activePermissionRequestCode = 0;
        if (!started || approvedSource != source) {
            permissionPendingSource = 0;
            approvedSource = 0;
            status.setText("Source approval expired. Return to the app and approve a new local read.");
            return;
        }
        permissionPendingSource = 0;
        if (grantResults.length == 0 || grantResults[0] != PackageManager.PERMISSION_GRANTED ||
                checkSelfPermission(permissionFor(source)) != PackageManager.PERMISSION_GRANTED) {
            approvedSource = 0;
            sourceDispositions[sourceIndex(source)] = "Unavailable";
            showCoverage();
            progressPanel.failedRead(sourceName(source), "Android or its installer did not grant this source. Other available sources can continue.");
            failure(sourceStage(source), "restricted", sourceName(source) +
                    " unavailable: permission denied or installer-restricted. No source read ran.");
            continueNativeCollection();
            return;
        }
        beginRead(source, nativeSetup != null ? nativeCountry : country.getText().toString().trim().toUpperCase(Locale.ROOT));
    }

    private String sourceName(int source) {
        return source == CONTACTS_REQUEST ? "Contacts" : source == CALLS_REQUEST ? "Calls" : "SMS";
    }

    private String sourceStage(int source) {
        return source == CONTACTS_REQUEST ? "contacts" : source == CALLS_REQUEST ? "calls" : "sms";
    }

    private String categoryFor(int source) {
        return source == CONTACTS_REQUEST ? "contacts" : source == CALLS_REQUEST ? "calls" : "messages";
    }

    private int sourceIndex(int source) {
        return source == CONTACTS_REQUEST ? 0 : source == CALLS_REQUEST ? 1 : 2;
    }

    private void clearSupportCode() {
        safeSupportCodeWire = "";
        supportCodeView.setText("");
        copySupportCode.setEnabled(false);
        copySupportCode.setVisibility(View.GONE);
    }

    private void failure(String stage, String category, String message) {
        status.setText(message);
        showSelection(false);
        status.requestFocus();
        safeSupportCodeWire = supportCode.format(stage, category, SystemClock.elapsedRealtimeNanos());
        supportCodeView.setText("Safe support code: " + safeSupportCodeWire);
        copySupportCode.setEnabled(true);
        copySupportCode.setVisibility(View.VISIBLE);
        String currentCode = safeSupportCodeWire;
        if (diagnosticAttempts++ < 8) SafeDiagnostic.send(currentCode, journeySessionId, delivered -> runOnUiThread(() -> {
            if (!isDestroyed() && currentCode.equals(safeSupportCodeWire)) supportCodeView.setText(
                    "Safe support code: " + currentCode + (delivered
                            ? " · Bounded diagnostic delivered."
                            : " · Automatic delivery unavailable; use Copy if you want support."));
        }));
        refreshActions();
    }

    private void acceptJourneyIntent(Intent intent) {
        if (intent == null || intent.getData() == null) return;
        Uri data = intent.getData();
        if (!"amplifai-phone".equals(data.getScheme()) || !"open".equals(data.getHost())) return;
        String candidate = data.getQueryParameter("journeyId");
        if (candidate != null && candidate.matches("[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}"))
            journeySessionId = candidate;
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        acceptJourneyIntent(intent);
        acceptSetupIntent(intent);
    }

    private void beginRead(int source, String iso) {
        if (updater.blocksCollection()) { approvedSource = 0; status.setText("Android is handling an app update. No source read started."); return; }
        if (approvedSource != source || permissionPendingSource != 0) {
            status.setText("Approve this source in the app before reading metadata.");
            return;
        }
        approvedSource = 0;
        if (!started) { status.setText("Return to the app and read the source again when ready."); return; }
        if (nativeSetup != null) {
            if (nativeRun == null || !nativeRun.canRead(categoryFor(source))) {
                status.setText("Collection authorization expired or changed. Start a new setup from your account."); return;
            }
            nativeRun.beginRead(categoryFor(source));
        }
        cancellation = new CancellationSignal();
        showSelection(false);
        CancellationSignal taskSignal = cancellation;
        SanitizedStore activeStore = store;
        cancel.setEnabled(true);
        cancel.setVisibility(View.VISIBLE);
        refreshSetup();
        sourceDispositions[sourceIndex(source)] = "Reading";
        showCoverage();
        progressPanel.beginRead(sourceName(source));
        status.setText("Reading " + sourceName(source) + " metadata locally…");
        executor.execute(() -> {
            try {
                PhoneMetadataReader next = new PhoneMetadataReader(this);
                String category = categoryFor(source);
                SanitizedStore.Coverage result = next.readToStore(category, iso, taskSignal, activeStore,
                        seen -> runOnUiThread(() -> {
                            if (cancellation == taskSignal && started) {
                                progressPanel.inspected(seen);
                                status.setText("Reading " + sourceName(source) + ": " +
                                        CollectionProgressPanel.counted(seen, "phone entry", "phone entries") + " checked locally…");
                            }
                        }));
                publishRead(source, taskSignal, () -> {
                    if (source == CONTACTS_REQUEST) { contactsAvailable = true; selectedIds.clear(); pageOffset = 0; renderChoices(); }
                    else if (source == CALLS_REQUEST) callsAvailable = true;
                    else messagesAvailable = true;
                    finishRead(source, result);
                });
            } catch (OperationCanceledException ignored) {
                publishRead(source, taskSignal, () -> finishFailure(source, "cancelled", "Read cancelled. No new source was stored."));
            } catch (SecurityException ignored) {
                publishRead(source, taskSignal, () -> finishFailure(source, "revoked", sourceName(source) + " unavailable: access was denied or revoked. No new source was stored."));
            } catch (SQLiteFullException full) {
                publishRead(source, taskSignal, () -> finishFailure(source, "storage_full", "Private phone storage is full. Free local space and reread; no partial source is available."));
            } catch (SQLiteException failedStorage) {
                publishRead(source, taskSignal, () -> finishFailure(source, "storage_io", "Private phone storage could not retain this source. Free local space and reread; no partial source is available."));
            } catch (IllegalArgumentException invalidRecord) {
                String code = "source_count_outside_protocol".equals(invalidRecord.getMessage()) ? "source_count" : "invalid";
                publishRead(source, taskSignal, () -> finishFailure(source, code, "This source has a record or count outside the transfer contract. No partial source is available."));
            } catch (RuntimeException providerFailure) {
                String code = "source_provider_unavailable".equals(providerFailure.getMessage()) ? "provider_null" : "provider_read";
                publishRead(source, taskSignal, () -> finishFailure(source, code,
                        "Android could not read " + sourceName(source).toLowerCase(Locale.ROOT) +
                                " on this phone. No new source was kept."));
            }
        });
    }

    private void publishRead(int source, CancellationSignal signal, Runnable result) {
        runOnUiThread(() -> {
            if (isDestroyed() || cancellation != signal) return;
            if (signal.isCanceled() || !started || (nativeSetup != null && (nativeRun == null || !nativeRun.current())))
                finishFailure(source, "cancelled", "Read cancelled. No new source was stored.");
            else result.run();
        });
    }

    private void finishRead(int source, SanitizedStore.Coverage result) {
        noteReviewDataChanged();
        cancellation = null;
        cancel.setEnabled(false);
        cancel.setVisibility(View.GONE);
        sourceDispositions[sourceIndex(source)] = "Available";
        long retained = store.count(categoryFor(source));
        status.setText(sourceName(source) + ": " +
                CollectionProgressPanel.counted(result.seen, "phone entry", "phone entries") + " checked; " +
                CollectionProgressPanel.counted(retained, "metadata record", "metadata records") + " kept on this phone; " +
                CollectionProgressPanel.counted(result.rejected, "entry", "entries") + " excluded.");
        progressPanel.finishedRead(sourceName(source), retained, result.seen, result.rejected);
        showCoverage();
        review.setText("Source data changed. Review your selected contacts and matching history again before browser handoff.");
        if (source == CONTACTS_REQUEST) showSelection(true);
        continueNativeCollection();
    }

    private void finishFailure(int source, String category, String message) {
        cancellation = null;
        cancel.setEnabled(false);
        cancel.setVisibility(View.GONE);
        if (source != 0 && store != null) {
            try { store.abort(categoryFor(source)); }
            catch (RuntimeException failedCleanup) {
                disableBrokenStore();
                failure("storage", "storage_io", "Private source cleanup failed. Close the app before trying another read.");
                return;
            }
        }
        if (source != 0) {
            sourceDispositions[sourceIndex(source)] = "cancelled".equals(category) ? "Not read" : "Unavailable";
            progressPanel.failedRead(sourceName(source), message);
        }
        failure(source == 0 ? "permission" : sourceStage(source), category, message);
        showCoverage();
        review.setText("Source data changed. Review your selected contacts and matching history again before browser handoff.");
        if ("cancelled".equals(category)) nativeReadQueue.clear();
        else continueNativeCollection();
    }

    private void showCoverage() {
        long contactsCount = contactsAvailable && store != null ? store.count("contacts") : -1;
        long callsCount = callsAvailable && store != null ? store.count("calls") : -1;
        long messagesCount = messagesAvailable && store != null ? store.count("messages") : -1;
        coverage.setText("Available locally: contacts " + (contactsCount < 0 ? "unavailable/not read" : contactsCount) +
                "; calls " + (callsCount < 0 ? "unavailable/not read" : callsCount) +
                "; SMS " + (messagesCount < 0 ? "unavailable/not read" : messagesCount) +
                ". Zero means a readable source with no records observed. Other messaging formats and unavailable history are not claimed.");
        progressPanel.sources(new long[]{contactsCount, callsCount, messagesCount}, sourceDispositions);
        refreshActions();
    }

    private void renderChoices() {
        if (choices == null) return;
        refreshActions();
        int generation = ++choicesGeneration;
        choices.removeAllViews();
        contactScroll.scrollTo(0, 0);
        if (!contactsAvailable || store == null) {
            choices.addView(text("Read contacts first. Search and select after Android grants access.", 13, MUTED));
            if (pageIndicator != null) { selectionPageText = ""; pageIndicator.setText(""); pageIndicator.setContentDescription(null); }
            if (previousPage != null) previousPage.setEnabled(false);
            if (nextPage != null) nextPage.setEnabled(false);
            if (selectAllContacts != null) selectAllContacts.setEnabled(false);
            if (clearSelectedContacts != null) clearSelectedContacts.setEnabled(!bulkSelectionActive && !selectedIds.isEmpty());
            return;
        }
        selectAllContacts.setEnabled(!bulkSelectionActive && !nativeSelectionFrozen());
        clearSelectedContacts.setEnabled(!bulkSelectionActive && !selectedIds.isEmpty() && !nativeSelectionFrozen());
        String query = search.getText().toString().trim();
        int requestedOffset = pageOffset;
        selectionPageText = "Loading contacts…";
        updateSelectionControls();
        pageIndicator.setContentDescription("Loading this contact page");
        previousPage.setEnabled(false);
        nextPage.setEnabled(false);
        executor.execute(() -> {
            if (generation != choicesGeneration) return;
            try {
                long total = store.contactCount(query);
                int offset = total == 0 ? 0 : requestedOffset >= total
                        ? (int) Math.min(Integer.MAX_VALUE, ((total - 1) / 100) * 100) : requestedOffset;
                List<MetadataModel.Contact> page = store.contactsPage(query, offset, 100);
                runOnUiThread(() -> {
                    if (!isDestroyed() && generation == choicesGeneration && contactsAvailable)
                        showChoicesPage(page, offset, total);
                });
            } catch (SQLiteException failedStorage) {
                runOnUiThread(() -> {
                    if (!isDestroyed() && generation == choicesGeneration)
                        failure("storage", "storage_io", "Private contact search failed. Free local space and retry.");
                });
            }
        });
    }

    private void showChoicesPage(List<MetadataModel.Contact> page, int offset, long total) {
        pageOffset = offset;
        choices.removeAllViews();
        for (MetadataModel.Contact contact : page) {
            String endings = contact.phones.isEmpty() ? "" : contact.phones.get(0);
            CheckBox choice = new CheckBox(this);
            choice.setText(contact.name + " · " + (endings.length() > 4 ? endings.substring(endings.length() - 4) : endings));
            choice.setTextColor(TEXT);
            choice.setButtonTintList(android.content.res.ColorStateList.valueOf(ACCENT));
            choice.setChecked(selectedIds.contains(contact.sourceId));
            choice.setEnabled(!nativeSelectionFrozen());
            choice.setOnClickListener(view -> {
                if (updater.blocksCollection() || nativeSelectionFrozen()) {
                    choice.setChecked(!choice.isChecked());
                    status.setText("Finish or cancel the Android update before changing this local review.");
                    return;
                }
                cancelBulkSelection();
                noteReviewDataChanged();
                if (choice.isChecked()) selectedIds.add(contact.sourceId); else selectedIds.remove(contact.sourceId);
                showSelectionPageCount(total);
                clearSelectedContacts.setEnabled(!selectedIds.isEmpty());
            });
            choices.addView(choice);
        }
        showSelectionPageCount(total);
        if (previousPage != null) previousPage.setEnabled(pageOffset > 0);
        if (nextPage != null) nextPage.setEnabled((long) pageOffset + 100 < total);
    }

    private void showSelectionPageCount(long total) {
        if (pageIndicator == null) return;
        pageIndicator.setContentDescription(total == 0 ? "No contacts match this search. " + selectedIds.size() +
                " selected across all pages." : "Contacts " + (pageOffset + 1) + "–" +
                Math.min(total, (long) pageOffset + 100) + " of " + total +
                "; " + selectedIds.size() + " selected across all pages.");
        selectionPageText = total == 0 ? getString(R.string.selection_no_matches, selectedIds.size())
                : getString(R.string.selection_page_count, pageOffset + 1,
                        Math.min(total, (long) pageOffset + 100), total, selectedIds.size());
        refreshActions();
    }

    private void cancelBulkSelection() {
        bulkSelectionGeneration++;
        bulkSelectionActive = false;
        if (selectAllContacts != null) selectAllContacts.setEnabled(contactsAvailable && store != null && !nativeSelectionFrozen());
        if (clearSelectedContacts != null) clearSelectedContacts.setEnabled(!selectedIds.isEmpty() && !nativeSelectionFrozen());
        if (cancelSelectAll != null) cancelSelectAll.setVisibility(View.GONE);
        refreshActions();
    }

    private void selectEveryContact() {
        if (nativeSelectionFrozen()) return;
        if (updater.blocksCollection() || !started || !contactsAvailable || store == null ||
                cancellation != null || preparingTransfer || bulkSelectionActive ||
                (handoff != null && handoff.snapshot().active())) {
            status.setText("Finish the current read, handoff or update before selecting all contacts.");
            return;
        }
        bulkSelectionActive = true;
        showSelection(true);
        int generation = ++bulkSelectionGeneration;
        selectAllContacts.setEnabled(false);
        cancelSelectAll.setVisibility(View.VISIBLE);
        clearSelectedContacts.setEnabled(false);
        status.setText("Checking every contact ID locally. Your current selection stays unchanged until the full scan finishes.");
        refreshActions();
        updater.onSafetyChanged();
        executor.execute(() -> {
            try {
                long expected = store.contactCount("");
                Set<Long> complete = ContactBulkSelector.scan(expected, 100, store::contactIdsAfter,
                        () -> generation != bulkSelectionGeneration);
                runOnUiThread(() -> {
                    if (isDestroyed() || !started || !bulkSelectionActive || generation != bulkSelectionGeneration) return;
                    bulkSelectionActive = false;
                    cancelSelectAll.setVisibility(View.GONE);
                    noteReviewDataChanged();
                    selectedIds.clear();
                    selectedIds.addAll(complete);
                    status.setText("Selected " + CollectionProgressPanel.counted(complete.size(), "contact", "contacts") +
                            " across every page. Review the selected metadata before browser handoff.");
                    renderChoices();
                    reviewButton.requestFocus();
                });
            } catch (CancellationException ignored) {
                // A later manual edit or lifecycle change owns the visible selection.
            } catch (RuntimeException failed) {
                runOnUiThread(() -> {
                    if (isDestroyed() || generation != bulkSelectionGeneration) return;
                    bulkSelectionActive = false;
                    cancelSelectAll.setVisibility(View.GONE);
                    selectAllContacts.setEnabled(contactsAvailable && store != null);
                    clearSelectedContacts.setEnabled(!selectedIds.isEmpty());
                    failure("storage", "invalid", "Could not verify every contact ID. Your previous selection is unchanged; try again.");
                    updater.onSafetyChanged();
                });
            }
        });
    }

    private void clearContactSelection() {
        if (nativeSelectionFrozen()) return;
        if (updater.blocksCollection() || !started || cancellation != null || preparingTransfer ||
                (handoff != null && handoff.snapshot().active())) {
            status.setText("Finish the current read, handoff or update before clearing selection.");
            return;
        }
        cancelBulkSelection();
        if (selectedIds.isEmpty()) return;
        noteReviewDataChanged();
        selectedIds.clear();
        status.setText("All contact choices cleared. No phone source or saved account data was deleted.");
        renderChoices();
        showSelection(true);
    }

    private void reviewSelected() {
        if (updater.blocksCollection()) { review.setText("Finish or cancel the Android update before reviewing contacts."); return; }
        if (!started || cancellation != null || preparingTransfer || bulkSelectionActive || reviewingSelection ||
                !contactsAvailable || store == null || (handoff != null && handoff.snapshot().active())) {
            review.setText("Finish reading and verifying contacts before review."); return;
        }
        if (selectedIds.isEmpty()) { review.setText("Select at least one contact to review."); return; }
        int generation = ++reviewGeneration;
        reviewingSelection = true;
        reviewed = false;
        refreshActions();
        Set<Long> ids = new HashSet<>(selectedIds);
        review.setText("Matching selected contacts with available history locally…");
        showSelection(false);
        review.requestFocus();
        executor.execute(() -> {
            try {
                MetadataModel.Review value = store.review(ids);
                runOnUiThread(() -> {
                    if (isDestroyed() || generation != reviewGeneration) return;
                    String range = value.earliestMs == 0 ? "No matching interaction dates observed"
                            : DateFormat.getDateInstance().format(value.earliestMs) + " – " +
                            DateFormat.getDateInstance().format(value.latestMs);
                    review.setText(value.selectedContacts + " selected contacts; " +
                            (callsAvailable ? value.matchedCalls + " matching calls" : "call history unavailable/not read") + "; " +
                            (messagesAvailable ? value.matchedMessages + " matching SMS records" : "SMS unavailable/not read") + ". " + range +
                            ". This is available observed history, not proof of completeness. No data was sent or saved to an account. " +
                            (nativeSetup == null ? "Confirm this selection before starting browser handoff." :
                                    "Continue sends this selection for saving to the approved account and course."));
                    progressPanel.reviewed(value.selectedContacts, value.matchedCalls, value.matchedMessages,
                            callsAvailable, messagesAvailable);
                    reviewingSelection = false;
                    reviewed = true;
                    refreshActions();
                    review.requestFocus();
                    if (nativeSetup != null) revealNativeReview(generation);
                });
            } catch (SQLiteException disk) {
                runOnUiThread(() -> { if (generation == reviewGeneration) {
                    reviewingSelection = false;
                    failure("storage", "unavailable", "Local review storage failed. Free space and review again.");
                    refreshActions();
                } });
            } catch (RuntimeException invalid) {
                runOnUiThread(() -> { if (generation == reviewGeneration) {
                    reviewingSelection = false;
                    failure("handoff", "invalid", "Selected metadata could not be reviewed within the transfer contract.");
                    refreshActions();
                } });
            }
        });
    }

    private void revealNativeReview(int generation) {
        clearReviewReveal();
        // Focus can already belong to this TextView while asynchronous text and the footer resize.
        // Reveal its beginning after layout, independently of whether focus changed in touch mode.
        pendingReviewReveal = () -> {
            clearReviewReveal();
            if (started && !isDestroyed() && generation == reviewGeneration && reviewed && !selectionVisible) {
                Rect summary = new Rect(0, 0, review.getWidth(), review.getHeight());
                detailsScroll.offsetDescendantRectToMyCoords(review, summary);
                // Retarget the framework scroller so an earlier focus/resize animation cannot undo the reveal.
                detailsScroll.smoothScrollTo(0, Math.max(0, summary.top - detailsScroll.getPaddingTop()));
            }
            return true;
        };
        detailsScroll.getViewTreeObserver().addOnPreDrawListener(pendingReviewReveal);
    }

    private void clearReviewReveal() {
        if (pendingReviewReveal == null) return;
        ViewTreeObserver tree = detailsScroll.getViewTreeObserver();
        if (tree.isAlive()) tree.removeOnPreDrawListener(pendingReviewReveal);
        pendingReviewReveal = null;
    }

    private void invalidateReview() {
        clearReviewReveal();
        nativeContinuePending = false;
        reviewGeneration++;
        reviewingSelection = false;
        reviewed = false;
        if (transferPreparationCancellation != null) transferPreparationCancellation.cancel();
        if (handoffButton != null) handoffButton.setEnabled(false);
        if (handoff != null && handoff.snapshot().active()) handoff.cancel();
        refreshActions();
    }

    private void noteReviewDataChanged() {
        snapshotCollectedAt = Instant.now();
        savedAckForCurrentReview = false;
        invalidateReview();
        progressPanel.selectionChanged();
        updater.onSafetyChanged();
    }

    private boolean canInstallUpdate() {
        if (nativeSetup != null && nativeSetup.state() != NativeSetupSession.State.CLOSED) return false;
        boolean activeHandoff = handoff == null || handoff.snapshot().active();
        boolean localSourceHeld = contactsAvailable || callsAvailable || messagesAvailable ||
                reviewed || !selectedIds.isEmpty();
        return UpdateSafety.canInstall(new UpdateSafety.State(started, collectionDisclosure != null || approvedSource != 0,
                permissionPendingSource != 0 || activePermissionRequestCode != 0, cancellation != null || bulkSelectionActive,
                preparingTransfer, activeHandoff, localSourceHeld, savedAckForCurrentReview));
    }

    private boolean clearSource(int source) {
        if (store != null) try { store.abort(categoryFor(source)); }
        catch (RuntimeException failedCleanup) {
            disableBrokenStore();
            failure("storage", "storage_io", "Private source cleanup failed. Close the app before trying another read.");
            return false;
        }
        if (source == CONTACTS_REQUEST) {
            contactsAvailable = false;
            selectedIds.clear();
            pageOffset = 0;
            renderChoices();
        } else if (source == CALLS_REQUEST) {
            callsAvailable = false;
        } else {
            messagesAvailable = false;
        }
        sourceDispositions[sourceIndex(source)] = "Not read";
        showCoverage();
        review.setText("Source data changed. Review your selected contacts and matching history again before browser handoff.");
        return true;
    }

    private void disableBrokenStore() {
        SanitizedStore broken = store;
        store = null;
        if (cancellation != null) cancellation.cancel();
        cancellation = null;
        cancel.setEnabled(false);
        cancel.setVisibility(View.GONE);
        contactsAvailable = false;
        callsAvailable = false;
        messagesAvailable = false;
        for (int index = 0; index < sourceDispositions.length; index++) sourceDispositions[index] = "Unavailable";
        selectedIds.clear();
        reviewed = false;
        handoffButton.setEnabled(false);
        renderChoices();
        showCoverage();
        if (broken != null) executor.execute(() -> {
            try { broken.close(); } catch (RuntimeException ignored) { /* Restart cleanup remains required. */ }
        });
    }

    private void startHandoff() {
        if (nativeSetup != null && (nativeRun == null || nativeRun.state() != NativeRunAuthority.State.STARTED || !nativeRun.current())) {
            status.setText("A current, separately started collection is required. Start a new setup from your account."); return;
        }
        if (updater.blocksCollection()) { status.setText("Finish or cancel the Android update before pairing."); return; }
        if (!started || handoff == null || handoff.snapshot().active() || !reviewed || selectedIds.isEmpty() || !contactsAvailable || cancellation != null || preparingTransfer || bulkSelectionActive || store == null) {
            status.setText("Read contacts, select them, and review the current selection first.");
            return;
        }
        if (checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED ||
                (callsAvailable && checkSelfPermission(Manifest.permission.READ_CALL_LOG) != PackageManager.PERMISSION_GRANTED) ||
                (messagesAvailable && checkSelfPermission(Manifest.permission.READ_SMS) != PackageManager.PERMISSION_GRANTED)) {
            invalidateReview();
            failure("permission", "revoked", "A source permission changed. Reread and review before pairing.");
            return;
        }
        if (PagedTransfer.cleanupRequired()) {
            failure("storage", "storage_io", "Private transfer cleanup is still needed. Close and reopen the app before another handoff.");
            return;
        }
        preparingTransfer = true;
        nativeContinuePending = nativeSetup != null;
        CancellationSignal taskSignal = new CancellationSignal();
        transferPreparationCancellation = taskSignal;
        refreshActions();
        Set<Long> ids = new HashSet<>(selectedIds);
        Instant frozenCollectedAt = snapshotCollectedAt;
        int generation = reviewGeneration;
        progressPanel.preparing("selected metadata", 0);
        status.setText("Preparing selected metadata locally…");
        executor.execute(() -> {
            PagedTransfer transfer = null;
            try {
                transfer = new PagedTransfer(this, store, ids, frozenCollectedAt, progress -> runOnUiThread(() -> {
                    if (!isDestroyed() && preparingTransfer && generation == reviewGeneration) {
                        String label = "messages".equals(progress.category()) ? "SMS" : progress.category();
                        progressPanel.preparing(label, progress.rows());
                        status.setText("Preparing " + label + " locally: " + progress.rows() + " selected records…");
                    }
                }), taskSignal);
                PagedTransfer ready = transfer;
                runOnUiThread(() -> {
                    if (!finishPreparation(taskSignal) || taskSignal.isCanceled() || !started ||
                            generation != reviewGeneration || handoff == null) {
                        closeTransferSafely(ready); return;
                    }
                    try {
                        if (nativeSetup == null) handoff.approve(ready);
                        else { handoff.approve(ready, nativeRun); nativeRunTransferred = true; }
                        handoffReviewGeneration = generation;
                        renderChoices();
                    }
                    catch (RuntimeException unavailable) {
                        nativeContinuePending = false;
                        if (!closeTransferSafely(ready)) return;
                        progressPanel.handoff("failed", 0, 0);
                        failure("handoff", "unavailable", "Local handoff could not start. Review and retry; no account save was made.");
                    }
                });
            } catch (OperationCanceledException cancelled) {
                PagedTransfer abandoned = transfer;
                runOnUiThread(() -> {
                    boolean current = finishPreparation(taskSignal);
                    if (!closeTransferSafely(abandoned)) return;
                    if (current) {
                        progressPanel.handoff("cancelled", 0, 0);
                        status.setText("Preparation cancelled. Review again before a new handoff.");
                    }
                });
            } catch (SQLiteException disk) {
                preparationFailed(taskSignal, generation, transfer, "storage", "storage_io",
                        "Private transfer storage failed. Free local space and review again.");
            } catch (IllegalArgumentException invalidRecord) {
                String code = "record_outside_protocol".equals(invalidRecord.getMessage()) ? "record_size" :
                        "source_count_outside_protocol".equals(invalidRecord.getMessage()) ? "source_count" : "invalid";
                preparationFailed(taskSignal, generation, transfer, "handoff", code,
                        "A selected record is too large for one transfer batch, or the source count exceeds the supported range. Review the source; no partial transfer started.");
            } catch (RuntimeException unavailable) {
                preparationFailed(taskSignal, generation, transfer, "handoff", "unavailable",
                        "Selected metadata could not be prepared. No account save was made.");
            }
        });
    }

    private boolean finishPreparation(CancellationSignal signal) {
        if (transferPreparationCancellation != signal) return false;
        transferPreparationCancellation = null;
        preparingTransfer = false;
        if (isDestroyed()) return false;
        refreshActions();
        return true;
    }

    private boolean nativeSelectionFrozen() {
        return nativeSetup != null && (preparingTransfer || nativeRun == null ||
                nativeRun.state() != NativeRunAuthority.State.STARTED ||
                (handoff != null && handoff.snapshot().active()));
    }

    private boolean closeTransferSafely(PagedTransfer transfer) {
        if (transfer == null) return true;
        try { transfer.close(); return true; }
        catch (RuntimeException cleanupFailure) {
            if (!isDestroyed()) {
                progressPanel.handoff("failed", 0, 0);
                failure("storage", "storage_io", "Private transfer cleanup failed. Close and reopen the app before another handoff.");
            }
            return false;
        }
    }

    private void preparationFailed(CancellationSignal signal, int generation, PagedTransfer transfer,
                                   String stage, String category, String message) {
        runOnUiThread(() -> {
            boolean current = finishPreparation(signal);
            if (current) nativeContinuePending = false;
            if (!closeTransferSafely(transfer) || !current) return;
            if (!"storage".equals(stage) && (generation != reviewGeneration || !started)) return;
            progressPanel.handoff("failed", 0, 0);
            failure(stage, category, message);
        });
    }

    private boolean recoverActiveHandoff() {
        if (handoff == null || !handoff.snapshot().active()) return true;
        TransferSourceBinding recovered = handoff.recoverSources();
        if (recovered == null) {
            if (destinationDialog != null) destinationDialog.dismiss();
            reviewed = false;
            failure("permission", "revoked", "Browser handoff cancelled because its source access could not be verified. Reread and review permitted sources before pairing.");
            refreshActions();
            return false;
        }
        // These are the frozen handoff's sources, not data in this Activity's new, empty review store.
        coverage.setText("Active reviewed handoff: Contacts" +
                (recovered.includes(TransferSourceBinding.CALLS) ? ", Calls" : "") +
                (recovered.includes(TransferSourceBinding.MESSAGES) ? ", SMS" : "") +
                ". A new collection needs a new read and review.");
        return true;
    }

    private void showHandoff(HandoffService.Snapshot value) {
        if (nativeSetup != null && !nativeRunTransferred && !value.active()) { refreshActions(); return; }
        if (value.active() && !recoverActiveHandoff()) return;
        if (value.state().equals("idle")) { refreshActions(); return; }
        if (!value.active()) showSelection(false);
        progressPanel.handoff(value.state(), value.pagesProvided(), value.pagesTotal());
        if (nativeSetup != null) { showNativeHandoff(value); return; }
        handoffButton.setEnabled(false);
        if (value.state().equals("ready")) {
            clearSupportCode();
            review.setText("One-use pairing code: " + value.code() +
                    ". Keep this app open, then tap Connect on the signed-in website on this same phone. Confirm the exact account and course here. This code remains a manual fallback and expires within 5 minutes. Account Save requires separate approval.");
            status.setText("Reviewed handoff ready on this phone. Nothing has been saved to an account.");
        } else if (value.state().equals("destination")) {
            status.setText("Confirm the signed-in destination shown in this app before transferring the preview.");
            showPendingDestination();
        } else if (value.state().equals("destination_approved")) {
            status.setText("Destination approved. Return to this phone's browser to finish receiving the preview; Save is separate.");
        } else if (value.state().equals("destination_denied")) {
            status.setText("Destination denied. Local review remains here; cancel this handoff before starting a new one.");
        } else if (value.state().equals("transferring")) {
            status.setText("Browser requested " + value.pagesProvided() + " / " +
                    value.pagesTotal() + " local batches. Validation and account save are separate steps.");
            review.setText("Return here from Recent apps to Cancel or confirm the destination. Browser receipt is not an account Save; approve each source there.");
        } else if (value.state().equals("starting")) {
            status.setText("Starting the approved local handoff…");
        } else if (value.state().equals("received")) {
            status.setText(R.string.handoff_received_status);
            review.setText(R.string.handoff_received_review);
        } else {
            if (destinationDialog != null) destinationDialog.dismiss();
            showCoverage();
            if (value.state().equals("completed") && handoffReviewGeneration == reviewGeneration)
                savedAckForCurrentReview = true;
            reviewed = false;
            String message = switch (value.state()) {
                case "completed" -> getString(R.string.handoff_saved_status);
                case "expired" -> "Browser handoff expired. Review again for a new pairing code.";
                case "attempts_exhausted" -> "Browser handoff stopped after five incorrect pairing attempts. Review again for a new code.";
                case "cancelled" -> "Browser handoff cancelled. Review again before a new transfer.";
                case "source_unavailable" -> "Browser handoff cancelled because source access could not be verified. Reread and review permitted sources before pairing.";
                default -> "Browser handoff stopped. Return to the app, review again and retry.";
            };
            if (value.state().equals("source_unavailable")) failure("permission", "revoked", message);
            else if (value.state().equals("failed")) failure("handoff", "unavailable", message);
            else status.setText(message);
            review.setText(value.state().equals("completed")
                    ? "Confirmed saved sources remain in your signed-in AMPLIFai account. Open the account page to review or export them."
                    : "The previous pairing code is no longer available. Any sources already saved in your account remain there; check the account before retrying.");
            updater.onSafetyChanged();
        }
        refreshActions();
    }

    private boolean completedHandoff() {
        return nativeAccountReturn != null && nativeAccountReturn.completedHandoffId() != null &&
                (nativeSetup == null || nativeRunTransferred) && (handoff == null || !handoff.snapshot().active());
    }

    private void restoreCompletedHandoff() {
        if (handoff == null) return;
        NativeAccountReturn completed = handoff.completedReturn(nativeAccountReturn, nativeSetup != null && !nativeRunTransferred);
        if (completed == null) return;
        // The service can finish while this Activity is unbound in the browser. Recover only
        // the exact completed display/return context, never a run or collection permission.
        nativeAccountReturn = completed;
        reviewed = false;
        showSelection(false);
        progressPanel.handoff("completed", 0, 0);
        status.setText(R.string.handoff_saved_status);
        review.setText("Confirmed saved sources remain in your account. Open the saved report to review them.");
        setupMessage = "The browser acknowledged verified account saves. Open your account for saved results.";
        refreshSetup();
    }

    private void showNativeHandoff(HandoffService.Snapshot value) {
        if (handoff != null && handoff.accountReturn() != null) nativeAccountReturn = handoff.accountReturn();
        String state = value.state();
        if ("ready".equals(state)) {
            clearSupportCode();
            status.setText("The reviewed selection is bound to this collection. Open your signed-in account on this phone to continue delivery.");
            review.setText("Only the approved account and course can receive this selection. Saving is covered by your collection approval; a saved receipt is still required.");
        } else if ("binding_pending".equals(state)) {
            status.setText("Continue has an uncertain response. Your selection remains fixed. Retry Continue or Cancel and start a new setup.");
        } else if ("starting".equals(state)) {
            status.setText("Binding the exact reviewed selection to the approved collection…");
        } else if ("destination_approved".equals(state)) {
            status.setText("The signed-in destination matches this collection's approval. Delivery and saving are pending.");
        } else if ("transferring".equals(state)) {
            status.setText("Browser requested " + value.pagesProvided() + " / " + value.pagesTotal() + " selected batches. No saved receipt has been confirmed.");
        } else if ("received".equals(state)) {
            status.setText("The browser received the selected metadata. Waiting for verified account save and readback.");
        } else if (!value.active()) {
            nativeContinuePending = false;
            if ("completed".equals(state) && handoffReviewGeneration == reviewGeneration) savedAckForCurrentReview = true;
            reviewed = false;
            status.setText("completed".equals(state) ? getString(R.string.handoff_saved_status) :
                    "This local collection stopped. Check saved results in your signed-in account; a new collection needs a new setup and Start.");
            review.setText("Confirmed saved sources remain in your account. Local delivery alone does not establish a saved report.");
            setupMessage = "completed".equals(state) ? "The browser acknowledged verified account saves. Open your account for saved results." :
                    "Local collection stopped. Check saved results in your account before starting a new setup.";
        }
        refreshActions();
        if ("ready".equals(state)) openNativeContinuation();
    }

    private void showPendingDestination() {
        if (!started || handoff == null || !recoverActiveHandoff()) return;
        LocalBridge.Destination destination = handoff.pendingDestination();
        if (destination == null || destination.intentId().equals(destinationIntentShown)) return;
        destinationIntentShown = destination.intentId();
        destinationDialog = new AlertDialog.Builder(this)
                .setTitle("Confirm signed-in account")
                .setMessage("Send this reviewed preview to:\n" + destination.email() +
                        "\nCourse: " + destination.course() +
                        "\n\nConnecting transfers the preview only. You approve each account Save separately in the browser.")
                .setNegativeButton("Deny", (dialog, which) -> {
                    if (handoff != null) handoff.decideDestination(destination.intentId(), false);
                })
                .setPositiveButton("Approve connection", (dialog, which) -> {
                    if (handoff != null && recoverActiveHandoff() && !handoff.decideDestination(destination.intentId(), true))
                        status.setText("This account confirmation expired. Keep the review open and start a fresh handoff.");
                })
                .create();
        destinationDialog.setOnDismissListener(dialog -> { destinationDialog = null; destinationIntentShown = null; });
        destinationDialog.show();
    }

    @Override protected void onStart() {
        super.onStart();
        started = true;
        bound = bindService(new Intent(this, HandoffService.class), connection, BIND_AUTO_CREATE);
        updater.onStart();
        refreshActions();
    }

    @Override protected void onResume() {
        super.onResume();
        boolean revoked = false;
        for (int source : new int[]{ CONTACTS_REQUEST, CALLS_REQUEST, SMS_REQUEST }) {
            boolean available = source == CONTACTS_REQUEST ? contactsAvailable
                    : source == CALLS_REQUEST ? callsAvailable : messagesAvailable;
            if (available && checkSelfPermission(permissionFor(source)) != PackageManager.PERMISSION_GRANTED) {
                noteReviewDataChanged();
                if (!clearSource(source)) return;
                sourceDispositions[sourceIndex(source)] = "Unavailable";
                progressPanel.failedRead(sourceName(source), "Android access was revoked. Reread and review permitted sources before pairing.");
                revoked = true;
            }
        }
        if (revoked) {
            showCoverage();
            // Binding completes asynchronously; revoke its handoff before showing any earlier code.
            if (handoff == null) cancelHandoffOnConnect = true;
            failure("permission", "revoked", "A source permission changed. That source is unavailable; reread and review permitted sources before pairing.");
        }
        recoverActiveHandoff();
        updater.onResume();
        refreshActions();
    }

    @Override protected void onStop() {
        clearReviewReveal();
        started = false;
        nativeContinuePending = false;
        nativeReadQueue.clear();
        if (transferPreparationCancellation != null) invalidateReview();
        if (destinationDialog != null) destinationDialog.dismiss();
        cancelBulkSelection();
        updater.onStop();
        progressPanel.pause();
        if (permissionPendingSource != 0) status.setText(nativeSetup == null ?
                "Permission prompt interrupted. Review and approve this source again before reading." :
                "Permission prompt interrupted. Return to this collection before reading a permitted source.");
        approvedSource = 0;
        permissionPendingSource = 0;
        activePermissionRequestCode = 0;
        if (collectionDisclosure != null) collectionDisclosure.dismiss();
        if (cancellation != null) cancellation.cancel();
        if (handoff != null) {
            if (isFinishing() && handoff.snapshot().active()) handoff.cancel();
            handoff.observe(null);
            handoff = null;
        }
        if (bound) { unbindService(connection); bound = false; }
        refreshActions();
        super.onStop();
    }

    @Override protected void onDestroy() {
        clearReviewReveal();
        if (Build.VERSION.SDK_INT < 30) getWindow().getDecorView().setOnApplyWindowInsetsListener(null);
        if (cancellation != null) cancellation.cancel();
        if (transferPreparationCancellation != null) transferPreparationCancellation.cancel();
        progressPanel.close();
        updater.close();
        if (setupDiscovery != null) setupDiscovery.close();
        if (nativeSetup != null && !nativeRunTransferred) {
            if (nativeRun != null) nativeRun.close(); else nativeSetup.close();
        }
        SanitizedStore closing = store;
        executor.execute(() -> { if (closing != null) closing.close(); });
        executor.shutdown();
        super.onDestroy();
    }
}
