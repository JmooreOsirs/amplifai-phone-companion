package ai.satoris.amplifai.phone;

import android.app.Instrumentation;
import android.os.Bundle;

import java.io.ByteArrayOutputStream;
import java.net.InetAddress;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.Set;
import java.util.HashSet;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import org.json.JSONObject;

/** Runs the changed loopback protocol on Android's actual regex and socket runtime. */
public final class LocalBridgeInstrumentedTest extends Instrumentation {
    private static final String ORIGIN = LocalBridge.ACCOUNT_ORIGIN;

    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        start();
    }

    @Override public void onStart() {
        Bundle result = new Bundle();
        try {
            verifyHandoff();
            verifyPagedHandoff();
            verifyOptionalRollback();
            verifyLargeStore();
            verifyFullHistoryStore();
            result.putString("result", "v1 pair/ACK; v2 private store/pages/decline/long transfer/retryable ACK; 25,700 selected contacts/257 pages; 100,001 contacts and 256,001 matching calls passed");
            finish(0, result);
        } catch (Exception | AssertionError failure) {
            result.putString("error", failure.getClass().getSimpleName() + ": " + failure.getMessage());
            finish(-1, result);
        }
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
            transfer = new PagedTransfer(getTargetContext(), source, Set.of(1L));
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
