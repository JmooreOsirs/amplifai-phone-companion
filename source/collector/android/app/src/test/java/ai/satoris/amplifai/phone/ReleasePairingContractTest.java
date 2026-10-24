package ai.satoris.amplifai.phone;

import org.junit.Test;
import static org.junit.Assert.*;
import java.net.InetAddress;
import java.net.Socket;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.regex.Pattern;

/** Synthetic localhost-only contract test. No Android provider or account service is contacted. */
public final class ReleasePairingContractTest {
    private static final String ORIGIN = LocalBridge.ACCOUNT_ORIGIN;
    private static final String INTENT = "12345678-1234-4234-8234-123456789abc";
    private static final String CAPABILITY = "A".repeat(43);
    private static final String RELEASE = "B".repeat(43);
    private static final String DIGEST = "1".repeat(64);

    private static String request(LocalBridge bridge, String path, String body) throws Exception {
        try (Socket socket = new Socket(InetAddress.getLoopbackAddress(), bridge.port())) {
            socket.setSoTimeout(3000);
            String wire = "POST " + path + " HTTP/1.1\r\nHost: 127.0.0.1:" + bridge.port()
                    + "\r\nOrigin: " + ORIGIN + "\r\nContent-Type: application/json\r\nContent-Length: "
                    + body.getBytes(StandardCharsets.US_ASCII).length + "\r\nConnection: close\r\n\r\n" + body;
            socket.getOutputStream().write(wire.getBytes(StandardCharsets.US_ASCII));
            ByteArrayOutputStream response = new ByteArrayOutputStream();
            try {
                byte[] chunk = new byte[4096];
                int count;
                while ((count = socket.getInputStream().read(chunk)) != -1) response.write(chunk, 0, count);
            } catch (java.net.SocketException resetAfterDeniedBody) {
                if (response.size() == 0) throw resetAfterDeniedBody;
            }
            return response.toString(StandardCharsets.US_ASCII);
        }
    }

    private static long expiry(String response) {
        var match = Pattern.compile("\"expiresInSeconds\":([0-9]+)").matcher(response);
        assertTrue("Pairing expiry is present", match.find());
        return Long.parseLong(match.group(1));
    }

    private static LocalBridge.PagedSource pages() {
        return new LocalBridge.PagedSource() {
            public String manifestJson() { return "{\"synthetic\":true}"; }
            public String manifestSha256() { return DIGEST; }
            public int pageCount(String category) { return category.equals("contacts") ? 1 : 0; }
            public String pageJson(String category, int cursor) { return "{}"; }
            public void close() {}
        };
    }

    @Test public void accountReleaseAndRetryStayWithinWebsitePairingExpiryContract() throws Exception {
        AtomicLong clock = new AtomicLong();
        AtomicInteger consumes = new AtomicInteger();
        LocalBridge.AccountProof proof = new LocalBridge.AccountProof() {
            public LocalBridge.Destination claim(String id, String capability, String handoff, String digest) {
                return new LocalBridge.Destination(id, "synthetic@example.test", "Synthetic course");
            }
            public boolean consume(String id, String capability, String release, String handoff, String digest) {
                consumes.incrementAndGet(); return true;
            }
        };
        try (LocalBridge bridge = new LocalBridge(pages(), ORIGIN, 0, clock::get,
                ignored -> {}, () -> {}, ignored -> {}, proof, () -> {})) {
            bridge.start();
            String connect = "{\"intentId\":\"" + INTENT + "\",\"capability\":\"" + CAPABILITY
                    + "\",\"handoffId\":\"" + bridge.handoffId() + "\",\"payloadSha256\":\"" + DIGEST + "\"}";
            assertTrue(request(bridge, "/v3/connect", connect).startsWith("HTTP/1.1 200"));
            String release = "{\"intentId\":\"" + INTENT + "\",\"capability\":\"" + CAPABILITY
                    + "\",\"releaseCode\":\"" + RELEASE + "\"}";
            assertTrue(request(bridge, "/v3/release", release).startsWith("HTTP/1.1 403"));
            assertEquals(0, consumes.get());
            assertTrue(bridge.decideDestination(INTENT, true));
            String first = request(bridge, "/v3/release", release);
            assertTrue(first.startsWith("HTTP/1.1 200"));
            long firstSeconds = expiry(first);
            assertTrue("Website rejects released expiry above 300 seconds; got " + firstSeconds,
                    firstSeconds > 0 && firstSeconds <= 300);
            assertEquals(LocalBridge.REVIEW_LIFETIME_MILLIS, bridge.remainingMillis());
            clock.set(TimeUnit.MINUTES.toNanos(1));
            String retry = request(bridge, "/v3/release", release);
            assertTrue(retry.startsWith("HTTP/1.1 200"));
            assertTrue(expiry(retry) > 0 && expiry(retry) <= 300);
            assertEquals(LocalBridge.REVIEW_LIFETIME_MILLIS - 60_000, bridge.remainingMillis());
            assertEquals(2, consumes.get());
            assertEquals(first.substring(first.indexOf("\r\n\r\n") + 4), retry.substring(retry.indexOf("\r\n\r\n") + 4));
            clock.set(TimeUnit.MILLISECONDS.toNanos(LocalBridge.REVIEW_LIFETIME_MILLIS - 2_000));
            assertEquals(2, expiry(request(bridge, "/v3/release", release)));
            assertEquals(2_000, bridge.remainingMillis());
            clock.set(TimeUnit.MILLISECONDS.toNanos(LocalBridge.REVIEW_LIFETIME_MILLIS));
            assertTrue(request(bridge, "/v3/release", release).startsWith("HTTP/1.1 410"));
            assertEquals(3, consumes.get());
        }
    }

    @Test public void temporaryCodeAlreadyAdvertisesFiveMinutePairingBeforeReviewRenewal() throws Exception {
        AtomicLong clock = new AtomicLong();
        try (LocalBridge bridge = new LocalBridge(pages(), ORIGIN, 0, clock::get, ignored -> {}, () -> {})) {
            bridge.start();
            String result = request(bridge, "/v1/pair", "{\"code\":\"" + bridge.code() + "\"}");
            assertTrue(result.startsWith("HTTP/1.1 200"));
            assertEquals(300, expiry(result));
            assertEquals(LocalBridge.REVIEW_LIFETIME_MILLIS, bridge.remainingMillis());
        }
    }
}
