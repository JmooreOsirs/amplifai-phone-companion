package ai.satoris.amplifai.phone;

import android.app.Instrumentation;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInstaller;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.io.InputStream;
import java.net.InetAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.Set;
import java.util.HashSet;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONObject;

/** Runs the changed loopback protocol on Android's actual regex and socket runtime. */
public final class LocalBridgeInstrumentedTest extends Instrumentation {
    private static final String ORIGIN = LocalBridge.ACCOUNT_ORIGIN;
    private Bundle testArguments = Bundle.EMPTY;

    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        if (arguments != null) testArguments = arguments;
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            if ("updater_install_auto".equals(testArguments.getString("scenario"))) {
                initiateUpdaterInstall(true);
                result.putString("result", "same-signed disposable higher-version archive committed with conditional no-user-action request; inspect OS callback and installed version separately");
                finish(0, result);
                return;
            }
            if ("updater_install_manual".equals(testArguments.getString("scenario"))) {
                initiateUpdaterInstall(false);
                result.putString("result", "same-signed disposable higher-version archive committed with required user action; inspect OS prompt and installed version separately");
                finish(0, result);
                return;
            }
            if ("updater".equals(testArguments.getString("scenario"))) {
                verifyUpdaterArchive();
                verifyUpdateCallbackState();
                result.putString("result", "pinned feed/archive identity negatives and installer callback replay, cancel, failure and actual-version reconciliation passed");
                finish(0, result);
                return;
            }
            verifyProgressPanel();
            if ("progress_ui".equals(testArguments.getString("scenario"))) {
                result.putString("result", "measured Android progress, unavailable versus zero, local review, and saved-only results passed");
                result.putString("fontScale", Float.toString(getTargetContext().getResources().getConfiguration().fontScale));
                result.putString("largeCountLayout", "320dp: exact 2,147,483,647 in two lines with no ellipsis");
                finish(0, result);
                return;
            }
            verifyHandoff();
            verifyPagedHandoff();
            verifyOptionalRollback();
            verifyLargeStore();
            verifyFullHistoryStore();
            result.putString("result", "measured native progress and saved-only results; v1 pair/ACK; v2 private store/pages/decline/long transfer/retryable ACK; 25,700 selected contacts/257 pages; 100,001 contacts and 256,001 matching calls passed");
            finish(0, result);
        } catch (Exception | AssertionError failure) {
            result.putString("error", failure.getClass().getSimpleName() + ": " + failure.getMessage());
            finish(-1, result);
        }
    }

    private void verifyUpdaterArchive() throws Exception {
        Context target = getTargetContext();
        byte[] feed;
        byte[] certificate;
        try (InputStream input = getContext().getAssets().open("rc7-signed-feed.txt")) {
            feed = input.readAllBytes();
        }
        try (InputStream input = target.getAssets().open("android_update_publisher.der")) {
            certificate = input.readAllBytes();
        }
        AndroidUpdateFeed.Release rc7 = AndroidUpdateFeed.parse(feed, certificate);
        File copy = new File(target.getNoBackupFilesDir(), "updater-rc7-fixture.apk");
        try {
            try (InputStream input = getContext().getAssets().open("rc7-signed.apk");
                 FileOutputStream output = new FileOutputStream(copy)) {
                input.transferTo(output);
            }
            UpdateInstaller.verifyArchive(target, rc7, copy, 26100806);
            expectUpdateRejection(target, new AndroidUpdateFeed.Release(26100808, rc7.versionName(),
                    rc7.minSdk(), rc7.bytes(), rc7.sha256(), rc7.url()), copy, "wrong version");
            try (RandomAccessFile change = new RandomAccessFile(copy, "rw")) {
                change.seek(copy.length() - 1);
                int last = change.read();
                change.seek(copy.length() - 1);
                change.write(last ^ 1);
            }
            expectUpdateRejection(target, rc7, copy, "corrupt APK");
            try (RandomAccessFile change = new RandomAccessFile(copy, "rw")) {
                change.setLength(rc7.bytes() - 1);
            }
            expectUpdateRejection(target, rc7, copy, "truncated APK");
            File debug = new File(target.getNoBackupFilesDir(), "updater-wrong-signer-fixture.apk");
            try (InputStream input = getContext().getAssets().open("rc8-debug.apk");
                 FileOutputStream output = new FileOutputStream(debug)) {
                input.transferTo(output);
            }
            byte[] digest;
            try {
                try (FileInputStream input = new FileInputStream(debug)) {
                    digest = MessageDigest.getInstance("SHA-256").digest(input.readAllBytes());
                }
                AndroidUpdateFeed.Release wrongSigner = new AndroidUpdateFeed.Release(26100808, "2026.10.08-rc8",
                        26, debug.length(), AndroidUpdateFeed.hex(digest), rc7.url());
                expectUpdateRejection(target, wrongSigner, debug, "wrong signer");
            } finally {
                if (!debug.delete()) debug.deleteOnExit();
            }
        } finally { if (!copy.delete()) copy.deleteOnExit(); }
    }

    private static void expectUpdateRejection(Context context, AndroidUpdateFeed.Release release,
                                              File file, String label) throws Exception {
        try {
            UpdateInstaller.verifyArchive(context, release, file, 26100806);
            throw new AssertionError(label + " was accepted");
        } catch (IOException expected) { /* A bad archive must fail before an OS session is created. */ }
    }

    private void verifyUpdateCallbackState() {
        Context target = getTargetContext();
        SharedPreferences state = target.getSharedPreferences("android-update-status", Context.MODE_PRIVATE);
        state.edit().clear().commit();
        try {
            UpdateInstallReceiver.pendingIntent(target, 987654, 26100809);
            require(UpdateInstallReceiver.pending(target), "pending installer guard missing");
            String nonce = state.getString("nonce", "");
            Intent callback = new Intent(target, UpdateInstallReceiver.class)
                    .setAction("ai.satoris.amplifai.phone.UPDATE_INSTALL_RESULT")
                    .putExtra("session", 987654).putExtra("nonce", nonce);
            UpdateInstallReceiver receiver = new UpdateInstallReceiver();
            receiver.onReceive(target, new Intent(callback).putExtra("nonce", "wrong")
                    .putExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE));
            require(UpdateInstallReceiver.pending(target), "wrong-nonce callback revoked live guard");
            receiver.onReceive(target, new Intent(callback)
                    .putExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_SUCCESS));
            require(UpdateInstallReceiver.pending(target), "success callback cleared guard before installed version");
            UpdateInstallReceiver.reconcile(target, 26100808);
            require(UpdateInstallReceiver.pending(target), "old installed version cleared guard");
            UpdateInstallReceiver.reconcile(target, 26100809);
            require(!UpdateInstallReceiver.pending(target) &&
                    UpdateInstallReceiver.confirmedTarget(target) == 26100809,
                    "installed target did not reconcile exactly");

            UpdateInstallReceiver.pendingIntent(target, 987655, 26100810);
            String nextNonce = state.getString("nonce", "");
            Intent approval = new Intent(callback).putExtra("session", 987655)
                    .putExtra("nonce", nextNonce)
                    .putExtra(Intent.EXTRA_INTENT, new Intent(target, MainActivity.class))
                    .putExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_PENDING_USER_ACTION);
            receiver.onReceive(target, approval);
            require(UpdateInstallReceiver.pendingApproval(target) != null &&
                    "awaiting_android_approval".equals(UpdateInstallReceiver.status(target)),
                    "OS approval was not recoverable without background launch");
            Intent cancelled = new Intent(callback).putExtra("session", 987655)
                    .putExtra("nonce", nextNonce)
                    .putExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE_ABORTED);
            receiver.onReceive(target, cancelled);
            require(!UpdateInstallReceiver.pending(target) && "cancelled".equals(UpdateInstallReceiver.status(target)),
                    "OS cancellation did not release guard");
            require(UpdateInstallReceiver.pendingApproval(target) == null,
                    "cancelled OS approval remained active");
            UpdateInstallReceiver.pendingIntent(target, 987656, 26100810);
            receiver.onReceive(target, new Intent(callback).putExtra("session", 987656)
                    .putExtra("nonce", state.getString("nonce", ""))
                    .putExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE));
            require(!UpdateInstallReceiver.pending(target) && "failed".equals(UpdateInstallReceiver.status(target)),
                    "OS failure did not release guard");
        } finally { state.edit().clear().commit(); }
    }

    private void initiateUpdaterInstall(boolean automatic) throws Exception {
        Context target = getTargetContext();
        require(UpdateInstaller.archiveVersionCode(target.getPackageManager().getPackageInfo(
                target.getPackageName(), 0)) == 26100808, "isolated base must be signed rc8");
        require(!UpdateInstallReceiver.pending(target), "another Android update remains pending");
        File copy = new File(target.getNoBackupFilesDir(), "synthetic-higher-version.apk");
        try {
            try (InputStream input = getContext().getAssets().open("synthetic-rc9-signed.apk");
                 FileOutputStream output = new FileOutputStream(copy)) {
                input.transferTo(output);
            }
            byte[] digest;
            try (FileInputStream input = new FileInputStream(copy)) {
                digest = MessageDigest.getInstance("SHA-256").digest(input.readAllBytes());
            }
            AndroidUpdateFeed.Release fixture = new AndroidUpdateFeed.Release(26100809, "2026.10.08-rc9",
                    26, copy.length(), AndroidUpdateFeed.hex(digest),
                    "https://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/android-2026.10.08-rc9/AMPLIFai-Phone-Android-2026.10.08-rc9.apk");
            UpdateInstaller.Staged staged = UpdateInstaller.stage(target, fixture, copy, 26100808, automatic);
            UpdateInstaller.commit(target, staged, fixture.versionCode());
            require(UpdateInstallReceiver.pending(target), "OS installation session was not durably guarded");
        } finally { if (!copy.delete()) copy.deleteOnExit(); }
    }

    private void verifyProgressPanel() {
        AtomicBoolean accountOpened = new AtomicBoolean();
        runOnMainSync(() -> {
            LinearLayout root = new LinearLayout(getTargetContext());
            root.setOrientation(LinearLayout.VERTICAL);
            CollectionProgressPanel panel = new CollectionProgressPanel(getTargetContext(), root,
                    () -> accountOpened.set(true));
            try {
                panel.sources(new long[]{0, 123_456, -1}, new String[]{"Available", "Available", "Unavailable"});
                require(hasDescription(root, "Contacts: 0 metadata records kept on this phone"),
                        "readable empty contacts looked unavailable");
                require(hasDescription(root, "Calls: 123,456 metadata records kept on this phone"),
                        "large retained call count was not displayed");
                require(hasDescription(root, "SMS: unavailable"), "unavailable SMS looked like zero");
                panel.sources(new long[]{2_147_483_647L, 123_456, -1},
                        new String[]{"Available", "Available", "Unavailable"});
                int narrowWidth = Math.round(320 * getTargetContext().getResources().getDisplayMetrics().density);
                root.measure(View.MeasureSpec.makeMeasureSpec(narrowWidth, View.MeasureSpec.EXACTLY),
                        View.MeasureSpec.makeMeasureSpec(0, View.MeasureSpec.UNSPECIFIED));
                root.layout(0, 0, narrowWidth, root.getMeasuredHeight());
                TextView maximum = findTextView(root, "2,147\n483,647");
                require(maximum != null && maximum.getWidth() > 0 && maximum.getLayout() != null &&
                                maximum.getLayout().getLineCount() == 2 &&
                                maximum.getPaint().measureText("483,647") <= maximum.getWidth() &&
                                maximum.getLayout().getEllipsisCount(0) == 0 &&
                                maximum.getLayout().getEllipsisCount(1) == 0,
                        "maximum source count clips at a 320dp layout");
                require(hasDescription(root, "Contacts: 2,147,483,647 metadata records kept on this phone"),
                        "maximum count was shortened in accessibility text");
                panel.beginRead("Calls");
                panel.inspected(1_000);
                require(hasText(root, "1,000") && hasText(root, "Phone entries checked"),
                        "measured provider rows were not distinct from retained source counts");
                panel.reviewed(12, 34, 0, true, false);
                require(hasText(root, "12") && hasText(root, "Selected contacts"),
                        "local selected review was not shown");
                panel.handoff("received", 4, 4);
                Button report = findButton(root, "Open saved account and report");
                require(report != null && report.getVisibility() == View.GONE,
                        "browser delivery exposed account-saved report control");
                panel.handoff("completed", 4, 4);
                require(report.getVisibility() == View.VISIBLE && hasText(root, "Verified saved receipt"),
                        "matching saved receipt did not expose results control");
                report.performClick();
                require(accountOpened.get(), "saved result did not open the existing account destination");
            } finally { panel.close(); }
        });
    }

    private static boolean hasDescription(View root, String description) {
        if (description.equals(root.getContentDescription())) return true;
        if (root instanceof ViewGroup group) for (int index = 0; index < group.getChildCount(); index++)
            if (hasDescription(group.getChildAt(index), description)) return true;
        return false;
    }

    private static boolean hasText(View root, String value) {
        if (root instanceof TextView text && value.contentEquals(text.getText())) return true;
        if (root instanceof ViewGroup group) for (int index = 0; index < group.getChildCount(); index++)
            if (hasText(group.getChildAt(index), value)) return true;
        return false;
    }

    private static TextView findTextView(View root, String value) {
        if (root instanceof TextView text && value.contentEquals(text.getText())) return text;
        if (root instanceof ViewGroup group) for (int index = 0; index < group.getChildCount(); index++) {
            TextView found = findTextView(group.getChildAt(index), value);
            if (found != null) return found;
        }
        return null;
    }

    private static Button findButton(View root, String label) {
        if (root instanceof Button button && label.contentEquals(button.getText())) return button;
        if (root instanceof ViewGroup group) for (int index = 0; index < group.getChildCount(); index++) {
            Button found = findButton(group.getChildAt(index), label);
            if (found != null) return found;
        }
        return null;
    }

    private static void verifyHandoff() throws Exception {
        byte[] payload = "{\"schema\":1}".getBytes(StandardCharsets.US_ASCII);
        try (LocalBridge bridge = new LocalBridge(payload, ORIGIN, 0)) {
            bridge.start();
            String paired = request(bridge.port(), "POST", "/v1/pair", "Content-Type: application/json\r\n",
                    "{\"code\":\"" + bridge.code() + "\"}");
            require(paired.startsWith("HTTP/1.1 200"), "pair failed");
            String token = capture(paired, "\\\"token\\\":\\\"([A-Za-z0-9_-]+)\\\"");
            String handoff = capture(paired, "\\\"handoffId\\\":\\\"([0-9a-f-]{36})\\\"");
            String digest = capture(paired, "\\\"payloadSha256\\\":\\\"([0-9a-f]{64})\\\"");
            require(digest.equals(HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(payload))),
                    "pairing digest mismatch");
            String auth = "Authorization: Bearer " + token + "\r\n";
            String acknowledgment = "{\"handoffId\":\"" + handoff + "\",\"payloadSha256\":\"" + digest + "\",\"saved\":true}";
            require(request(bridge.port(), "POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n",
                    acknowledgment).startsWith("HTTP/1.1 409"), "ack accepted before delivery");
            String delivered = request(bridge.port(), "GET", "/v1/metadata", auth, "");
            require(delivered.startsWith("HTTP/1.1 200") && delivered.endsWith(new String(payload, StandardCharsets.US_ASCII)),
                    "metadata delivery failed");
            require(request(bridge.port(), "GET", "/v1/metadata", auth, "").startsWith("HTTP/1.1 410"),
                    "delivered payload replayed");
            require(request(bridge.port(), "POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n",
                    acknowledgment.replace(digest, "0".repeat(64))).startsWith("HTTP/1.1 409"),
                    "wrong digest acknowledged");
            require(request(bridge.port(), "POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n",
                    acknowledgment).contains("\"acknowledged\":true"), "saved acknowledgment failed");
            try (Socket ignored = new Socket(InetAddress.getByName("127.0.0.1"), bridge.port())) {
                throw new AssertionError("listener remained open after saved acknowledgment");
            } catch (java.net.ConnectException expected) {
                // The one-use listener must be closed now.
            }
        }
    }

    private void verifyPagedHandoff() throws Exception {
        SanitizedStore source = new SanitizedStore(getTargetContext());
        PagedTransfer transfer;
        try {
            source.begin("contacts");
            String longName = "Synthetic " + "N".repeat(320);
            source.contact(1, longName, "+15551234567");
            source.contact(1, longName, "+15551234568");
            source.commit("contacts", 2, 0);
            source.begin("calls");
            source.interaction("calls", 10, "+15551234567", 1_750_000_000_000L, "incoming", 31L);
            source.commit("calls", 1, 0);
            source.review(Set.of(1L));
            Instant frozen = Instant.parse("2026-10-08T17:00:00Z");
            transfer = new PagedTransfer(getTargetContext(), source, Set.of(1L), frozen, ignored -> {});
            String firstManifest = transfer.manifestJson();
            String firstDigest = transfer.manifestSha256();
            transfer.close();
            transfer = new PagedTransfer(getTargetContext(), source, Set.of(1L), frozen, ignored -> {});
            require(transfer.manifestJson().equals(firstManifest) && transfer.manifestSha256().equals(firstDigest),
                    "unchanged reviewed snapshot changed retry identity after re-pair");
            require(transfer.pageJson("contacts", 0) != null, "re-paired contact page missing");
            transfer.close();
            try (PagedTransfer changed = new PagedTransfer(getTargetContext(), source, Set.of(1L),
                    frozen.plusSeconds(1), ignored -> {})) {
                require(!changed.manifestSha256().equals(firstDigest),
                        "changed reviewed snapshot reused the prior retry identity");
            }
            transfer = new PagedTransfer(getTargetContext(), source, Set.of(1L), frozen, ignored -> {});
            require(transfer.manifestJson().contains("\"platform\":\"android\""), "Android manifest missing");
            require(transfer.manifestJson().contains("\"rowsSeen\":1,\"rowsIncluded\":1,\"providerRowsSeen\":2"),
                    "phone rows were misreported as excluded contact records");
            require(transfer.manifestJson().contains("\"pageCount\":1"), "selected source page missing");
            require(transfer.manifestJson().contains("\"missingSources\":[\"messages\"]"), "missing SMS not marked");
        } finally { source.close(); }
        AtomicLong clock = new AtomicLong();
        try (LocalBridge bridge = new LocalBridge(transfer, ORIGIN, 0, clock::get, reason -> {}, () -> {})) {
            bridge.start();
            String paired = request(bridge.port(), "POST", "/v1/pair", "Content-Type: application/json\r\n",
                    "{\"code\":\"" + bridge.code() + "\"}");
            require(paired.startsWith("HTTP/1.1 200"), "v2 pair failed");
            JSONObject pairing = new JSONObject(paired.substring(paired.indexOf("\r\n\r\n") + 4));
            String token = pairing.getString("token");
            String handoff = pairing.getString("handoffId");
            String digest = pairing.getString("payloadSha256");
            require(pairing.getBoolean("confirmReceiptRequired"), "v2 ACK handshake missing");
            require(digest.equals(PagedTransfer.sha256(pairing.getString("manifestJson"))), "manifest hash mismatch");
            String auth = "Authorization: Bearer " + token + "\r\n";
            String binding = "{\"handoffId\":\"" + handoff + "\",\"payloadSha256\":\"" + digest + "\"";
            clock.set(TimeUnit.MINUTES.toNanos(6));
            require(request(bridge.port(), "POST", "/v2/keepalive", auth, "").contains("\"active\":true"),
                    "active paged review expired with initial pairing code");
            String page = request(bridge.port(), "GET", "/v2/source/contacts?cursor=0", auth, "");
            JSONObject recordPage = new JSONObject(page.substring(page.indexOf("\r\n\r\n") + 4));
            require(recordPage.getString("chunk").contains("N".repeat(320)), "full selected name clipped");
            require(recordPage.getString("pageSha256").equals(PagedTransfer.sha256(recordPage.getString("chunk"))),
                    "contact page hash mismatch");
            require(request(bridge.port(), "GET", "/v2/source/contacts?cursor=0", auth, "").startsWith("HTTP/1.1 200"),
                    "lost page response not retryable");
            require(request(bridge.port(), "GET", "/v2/source/contacts?cursor=1", auth, "").startsWith("HTTP/1.1 409"),
                    "future page accepted");
            String decline = binding + ",\"category\":\"calls\"}";
            require(request(bridge.port(), "POST", "/v2/decline", auth + "Content-Type: application/json\r\n", decline)
                    .contains("\"declined\":true"), "call decline failed");
            require(request(bridge.port(), "POST", "/v2/decline", auth + "Content-Type: application/json\r\n", decline)
                    .contains("\"declined\":true"), "lost decline response not retryable");
            require(request(bridge.port(), "GET", "/v2/source/calls?cursor=0", auth, "").startsWith("HTTP/1.1 410"),
                    "declined call page replayed");
            String completed = request(bridge.port(), "POST", "/v1/complete", auth, "");
            require(completed.contains("\"calls\":\"declined\""), "completion lost decline disposition");
            require(request(bridge.port(), "POST", "/v1/complete", auth, "").contains("\"completed\":true"),
                    "lost completion response not retryable");
            String saved = binding + ",\"saved\":true}";
            require(request(bridge.port(), "POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n",
                    saved.replace(digest, "0".repeat(64))).startsWith("HTTP/1.1 409"), "wrong manifest ACK accepted");
            require(request(bridge.port(), "POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n", saved)
                    .contains("\"acknowledged\":true"), "saved ACK failed");
            require(request(bridge.port(), "POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n", saved)
                    .contains("\"acknowledged\":true"), "lost saved ACK not retryable");
            require(request(bridge.port(), "POST", "/v1/confirm-ack-received", auth + "Content-Type: application/json\r\n",
                    binding + ",\"received\":true}").contains("\"received\":true"), "final receipt failed");
        }
    }

    private void verifyLargeStore() throws Exception {
        SanitizedStore source = new SanitizedStore(getTargetContext());
        try {
            Set<Long> selected = new HashSet<>();
            source.begin("contacts");
            for (int index = 1; index <= 25_700; index++) {
                source.contact(index, "Synthetic " + index, "+1555" + String.format(java.util.Locale.ROOT, "%07d", index));
                selected.add((long) index);
            }
            source.commit("contacts", 25_700, 0);
            require(source.count("contacts") == 25_700, "private source omitted contacts");
            try (PagedTransfer transfer = new PagedTransfer(getTargetContext(), source, selected)) {
                require(transfer.pageCount("contacts") == 257, "old aggregate page ceiling remained");
                require(transfer.page("contacts", 0) != null && transfer.page("contacts", 256) != null,
                        "first or final immutable page missing");
                require(transfer.manifestJson().contains("\"rowsIncluded\":25700"), "manifest silently truncated selected count");
            }
        } finally { source.close(); }
    }

    private void verifyOptionalRollback() throws Exception {
        SanitizedStore source = new SanitizedStore(getTargetContext());
        try {
            source.begin("contacts");
            source.contact(1, "Synthetic Contact", "+15551234567");
            source.commit("contacts", 1, 0);
            source.begin("calls");
            source.interaction("calls", 1, "+15551234567", 1_750_000_000_000L, "incoming", 10L);
            source.abort("calls");
            require(source.count("calls") == 0 && !source.coverage("calls").available,
                    "failed optional source exposed partial records");
            try (PagedTransfer transfer = new PagedTransfer(getTargetContext(), source, Set.of(1L))) {
                require(transfer.pageCount("contacts") == 1 && transfer.pageCount("calls") == 0,
                        "optional-source failure contaminated valid contact selection");
                require(transfer.manifestJson().contains("\"missingSources\":[\"calls\",\"messages\"]"),
                        "failed optional source not marked unavailable");
            }
        } finally { source.close(); }
    }

    private void verifyFullHistoryStore() throws Exception {
        SanitizedStore source = new SanitizedStore(getTargetContext());
        try {
            source.begin("contacts");
            for (int index = 1; index <= 100_001; index++) {
                source.contact(index, "Synthetic " + index, "+1555" + String.format(java.util.Locale.ROOT, "%07d", index));
            }
            source.commit("contacts", 100_001, 0);
            source.begin("calls");
            for (int index = 1; index <= 256_001; index++) {
                source.interaction("calls", index, "+15550000001", 1_750_000_000_000L + index,
                        "incoming", 30L);
            }
            source.commit("calls", 256_001, 0);
            try (PagedTransfer transfer = new PagedTransfer(getTargetContext(), source, Set.of(1L))) {
                require(transfer.pageCount("contacts") == 1, "selected contact missing beyond old source scan cap");
                require(transfer.pageCount("calls") == 2561, "old interaction cap or page ceiling remained");
                require(transfer.page("calls", 2560) != null, "last history page missing");
                require(transfer.manifestJson().contains("\"rowsSeen\":256001,\"rowsIncluded\":256001"),
                        "history manifest omitted measured rows");
            }
        } finally { source.close(); }
    }

    private static String capture(String response, String pattern) {
        Matcher match = Pattern.compile(pattern).matcher(response);
        require(match.find(), "missing pairing field");
        return match.group(1);
    }

    private static String request(int port, String method, String path, String headers, String body) throws Exception {
        try (Socket socket = new Socket(InetAddress.getByName("127.0.0.1"), port)) {
            socket.setSoTimeout(3000);
            byte[] bodyBytes = body.getBytes(StandardCharsets.US_ASCII);
            String request = method + " " + path + " HTTP/1.1\r\nHost: 127.0.0.1:" + port +
                    "\r\nOrigin: " + ORIGIN + "\r\n" + headers + "Content-Length: " + bodyBytes.length +
                    "\r\nConnection: close\r\n\r\n" + body;
            socket.getOutputStream().write(request.getBytes(StandardCharsets.US_ASCII));
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int count;
            while ((count = socket.getInputStream().read(chunk)) != -1) output.write(chunk, 0, count);
            return output.toString(StandardCharsets.US_ASCII);
        }
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
