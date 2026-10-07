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
            result.putString("result", "pair, deliver, wrong ack, saved ack, close passed");
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
