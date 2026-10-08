package ai.satoris.amplifai.phone;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

import java.io.ByteArrayOutputStream;
import java.net.InetAddress;
import java.net.Socket;
import java.net.ConnectException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.HexFormat;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class LocalBridgeTest {
    private static final String ORIGIN = "https://amplifai-database-engine.vercel.app";

    private static String send(int port, String origin, String method, String path,
                               String extraHeaders, String body) throws Exception {
        return sendWithHost(port, "127.0.0.1:" + port, origin, method, path, extraHeaders, body);
    }

    private static String sendWithHost(int port, String host, String origin, String method, String path,
                                      String extraHeaders, String body) throws Exception {
        try (Socket socket = new Socket(InetAddress.getByName("127.0.0.1"), port)) {
            socket.setSoTimeout(3000);
            byte[] bytes = body.getBytes(StandardCharsets.US_ASCII);
            String request = method + " " + path + " HTTP/1.1\r\nHost: " + host +
                    "\r\nOrigin: " + origin + "\r\n" + extraHeaders +
                    "Content-Length: " + bytes.length + "\r\nConnection: close\r\n\r\n" + body;
            socket.getOutputStream().write(request.getBytes(StandardCharsets.US_ASCII));
            ByteArrayOutputStream response = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            try {
                int count;
                while ((count = socket.getInputStream().read(chunk)) != -1) response.write(chunk, 0, count);
            } catch (java.net.SocketException resetAfterDeniedBody) {
                if (response.size() == 0) throw resetAfterDeniedBody;
            }
            return response.toString(StandardCharsets.US_ASCII);
        }
    }

    private static String savedAcknowledgment(String pairing) {
        Matcher handoff = Pattern.compile("\\\"handoffId\\\":\\\"([0-9a-f-]{36})\\\"").matcher(pairing);
        Matcher digest = Pattern.compile("\\\"payloadSha256\\\":\\\"([0-9a-f]{64})\\\"").matcher(pairing);
        assertTrue(handoff.find());
        assertTrue(digest.find());
        return "{\"handoffId\":\"" + handoff.group(1) + "\",\"payloadSha256\":\"" + digest.group(1) + "\",\"saved\":true}";
    }

    @Test public void exactOriginCodeAndOneUseTokenGateSelectedMetadata() throws Exception {
        try (LocalBridge bridge = new LocalBridge("{\"schema\":1}".getBytes(StandardCharsets.US_ASCII), ORIGIN, 0)) {
            bridge.start();
            assertTrue(InetAddress.getByName("127.0.0.1").isLoopbackAddress());
            String denied = send(bridge.port(), "https://other.example", "POST", "/v1/pair",
                    "Content-Type: application/json\r\n", "{\"code\":\"" + bridge.code() + "\"}");
            assertTrue(denied.startsWith("HTTP/1.1 403"));
            assertFalse(denied.contains("Access-Control-Allow-Origin"));
            String wrongHost = sendWithHost(bridge.port(), "localhost:" + bridge.port(), ORIGIN, "POST", "/v1/pair",
                    "Content-Type: application/json\r\n", "{\"code\":\"" + bridge.code() + "\"}");
            assertTrue(wrongHost.startsWith("HTTP/1.1 403"));
            assertFalse(wrongHost.contains("Access-Control-Allow-Origin"));

            String wrongCode = bridge.code().equals("0000000000") ? "0000000001" : "0000000000";
            String wrong = send(bridge.port(), ORIGIN, "POST", "/v1/pair",
                    "Content-Type: application/json\r\n", "{\"code\":\"" + wrongCode + "\"}");
            assertTrue(wrong.startsWith("HTTP/1.1 403"));
            String paired = send(bridge.port(), ORIGIN, "POST", "/v1/pair",
                    "Content-Type: application/json\r\n", "{\"code\":\"" + bridge.code() + "\"}");
            assertTrue(paired.startsWith("HTTP/1.1 200"));
            Matcher match = Pattern.compile("\\\"token\\\":\\\"([A-Za-z0-9_-]+)\\\"").matcher(paired);
            assertTrue(match.find());
            String token = match.group(1);
            assertTrue(send(bridge.port(), ORIGIN, "POST", "/v1/pair",
                    "Content-Type: application/json\r\n", "{\"code\":\"" + bridge.code() + "\"}").startsWith("HTTP/1.1 403"));
            assertTrue(send(bridge.port(), ORIGIN, "GET", "/v1/metadata", "", "").startsWith("HTTP/1.1 403"));
            String response = send(bridge.port(), ORIGIN, "GET", "/v1/metadata",
                    "Authorization: Bearer " + token + "\r\n", "");
            assertTrue(response.startsWith("HTTP/1.1 200"));
            assertTrue(response.endsWith("{\"schema\":1}"));
            assertTrue(send(bridge.port(), ORIGIN, "POST", "/v1/acknowledge-save",
                    "Authorization: Bearer " + token + "\r\nContent-Type: application/json\r\n", savedAcknowledgment(paired))
                    .contains("\"acknowledged\":true"));
            assertClosed(bridge.port());
        }
    }

    @Test public void browserSaveAcknowledgmentRequiresDeliveredMatchingHandoff() throws Exception {
        byte[] payload = "{\"schema\":1}".getBytes(StandardCharsets.US_ASCII);
        AtomicInteger received = new AtomicInteger();
        try (LocalBridge bridge = new LocalBridge(payload, ORIGIN, 0, System::nanoTime,
                reason -> {}, received::incrementAndGet)) {
            bridge.start();
            String paired = send(bridge.port(), ORIGIN, "POST", "/v1/pair",
                    "Content-Type: application/json\r\n", "{\"code\":\"" + bridge.code() + "\"}");
            assertTrue(paired.startsWith("HTTP/1.1 200"));
            Matcher token = Pattern.compile("\\\"token\\\":\\\"([A-Za-z0-9_-]+)\\\"").matcher(paired);
            Matcher handoff = Pattern.compile("\\\"handoffId\\\":\\\"([0-9a-f-]{36})\\\"").matcher(paired);
            Matcher digest = Pattern.compile("\\\"payloadSha256\\\":\\\"([0-9a-f]{64})\\\"").matcher(paired);
            assertTrue(token.find());
            assertTrue(handoff.find());
            assertTrue(digest.find());
            assertEquals(HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(payload)), digest.group(1));
            String auth = "Authorization: Bearer " + token.group(1) + "\r\n";
            String correct = "{\"handoffId\":\"" + handoff.group(1) + "\",\"payloadSha256\":\"" + digest.group(1) + "\",\"saved\":true}";
            assertTrue(send(bridge.port(), ORIGIN, "POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n", correct).startsWith("HTTP/1.1 409"));
            assertTrue(send(bridge.port(), ORIGIN, "GET", "/v1/metadata", auth, "").startsWith("HTTP/1.1 200"));
            assertEquals(1, received.get());
            assertTrue(send(bridge.port(), ORIGIN, "GET", "/v1/metadata", auth, "").startsWith("HTTP/1.1 410"));
            String preflight = send(bridge.port(), ORIGIN, "OPTIONS", "/v1/acknowledge-save",
                    "Access-Control-Request-Method: POST\r\nAccess-Control-Request-Private-Network: true\r\n", "");
            assertTrue(preflight.startsWith("HTTP/1.1 204"));
            assertTrue(preflight.contains("Access-Control-Allow-Private-Network: true"));
            assertTrue(send(bridge.port(), ORIGIN, "POST", "/v1/acknowledge-save",
                    "Authorization: Bearer wrong\r\nContent-Type: application/json\r\n", correct).startsWith("HTTP/1.1 403"));
            assertTrue(send(bridge.port(), "https://other.example", "POST", "/v1/acknowledge-save",
                    auth + "Content-Type: application/json\r\n", correct).startsWith("HTTP/1.1 403"));
            assertTrue(send(bridge.port(), ORIGIN, "POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n",
                    correct.replace(digest.group(1), "0".repeat(64))).startsWith("HTTP/1.1 409"));
            assertTrue(send(bridge.port(), ORIGIN, "POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n",
                    correct.replace("true", "false")).startsWith("HTTP/1.1 400"));
            assertTrue(send(bridge.port(), ORIGIN, "POST", "/v1/acknowledge-save", auth + "Content-Type: application/json\r\n", correct)
                    .contains("\"acknowledged\":true"));
            assertEquals(1, received.get());
            assertClosed(bridge.port());
        }
    }

    @Test public void browserOnlyDeliveryCanBeCancelledWithoutClaimingAccountSave() throws Exception {
        AtomicInteger received = new AtomicInteger();
        AtomicReference<LocalBridge.EndReason> end = new AtomicReference<>();
        try (LocalBridge bridge = new LocalBridge("{\"schema\":1}".getBytes(StandardCharsets.US_ASCII), ORIGIN, 0,
                System::nanoTime, end::set, received::incrementAndGet)) {
            bridge.start();
            String paired = send(bridge.port(), ORIGIN, "POST", "/v1/pair",
                    "Content-Type: application/json\r\n", "{\"code\":\"" + bridge.code() + "\"}");
            Matcher token = Pattern.compile("\\\"token\\\":\\\"([A-Za-z0-9_-]+)\\\"").matcher(paired);
            assertTrue(token.find());
            assertTrue(send(bridge.port(), ORIGIN, "GET", "/v1/metadata",
                    "Authorization: Bearer " + token.group(1) + "\r\n", "").startsWith("HTTP/1.1 200"));
            assertEquals(1, received.get());
            assertNull(end.get());
            bridge.close(LocalBridge.EndReason.CANCELLED);
            assertEquals(LocalBridge.EndReason.CANCELLED, end.get());
            assertClosed(bridge.port());
        }
    }

    @Test public void deliveredPreviewKeepsTheSaveAcknowledgmentOpenPastThePairingCodeLifetime() throws Exception {
        AtomicLong clock = new AtomicLong();
        AtomicReference<LocalBridge.EndReason> reason = new AtomicReference<>();
        try (LocalBridge bridge = new LocalBridge("{\"schema\":1}".getBytes(StandardCharsets.US_ASCII), ORIGIN, 0,
                clock::get, reason::set)) {
            bridge.start();
            String paired = send(bridge.port(), ORIGIN, "POST", "/v1/pair",
                    "Content-Type: application/json\r\n", "{\"code\":\"" + bridge.code() + "\"}");
            Matcher token = Pattern.compile("\\\"token\\\":\\\"([A-Za-z0-9_-]+)\\\"").matcher(paired);
            assertTrue(token.find());
            String auth = "Authorization: Bearer " + token.group(1) + "\r\n";
            clock.set(TimeUnit.MILLISECONDS.toNanos(LocalBridge.LIFETIME_MILLIS - 1_000));
            assertTrue(send(bridge.port(), ORIGIN, "GET", "/v1/metadata", auth, "").startsWith("HTTP/1.1 200"));
            assertEquals(LocalBridge.REVIEW_LIFETIME_MILLIS, bridge.remainingMillis());
            clock.set(TimeUnit.MILLISECONDS.toNanos(LocalBridge.LIFETIME_MILLIS + 1_000));
            assertTrue("A confirmed account save must still be acknowledgeable after the pairing code expires",
                    send(bridge.port(), ORIGIN, "POST", "/v1/acknowledge-save",
                            auth + "Content-Type: application/json\r\n", savedAcknowledgment(paired))
                            .contains("\"acknowledged\":true"));
            assertEquals(LocalBridge.EndReason.COMPLETED, reason.get());
        }
    }

    @Test public void deliveredPreviewExpiresWithoutAConfirmedAccountAcknowledgment() throws Exception {
        AtomicLong clock = new AtomicLong();
        AtomicReference<LocalBridge.EndReason> reason = new AtomicReference<>();
        CountDownLatch ended = new CountDownLatch(1);
        try (LocalBridge bridge = new LocalBridge("{\"schema\":1}".getBytes(StandardCharsets.US_ASCII), ORIGIN, 0,
                clock::get, result -> { reason.set(result); ended.countDown(); })) {
            bridge.start();
            String paired = send(bridge.port(), ORIGIN, "POST", "/v1/pair",
                    "Content-Type: application/json\r\n", "{\"code\":\"" + bridge.code() + "\"}");
            Matcher token = Pattern.compile("\\\"token\\\":\\\"([A-Za-z0-9_-]+)\\\"").matcher(paired);
            assertTrue(token.find());
            long deliveredAt = TimeUnit.MILLISECONDS.toNanos(LocalBridge.LIFETIME_MILLIS - 1_000);
            clock.set(deliveredAt);
            assertTrue(send(bridge.port(), ORIGIN, "GET", "/v1/metadata",
                    "Authorization: Bearer " + token.group(1) + "\r\n", "").startsWith("HTTP/1.1 200"));
            clock.set(deliveredAt + TimeUnit.MILLISECONDS.toNanos(LocalBridge.REVIEW_LIFETIME_MILLIS));
            assertTrue("A received preview must not leave a listener open indefinitely", ended.await(3, TimeUnit.SECONDS));
            assertEquals(LocalBridge.EndReason.EXPIRED, reason.get());
            assertClosed(bridge.port());
        }
    }

    @Test public void largePayloadRequiresOrderedPagesAndCompletion() throws Exception {
        byte[] payload = new byte[150_000];
        Arrays.fill(payload, (byte) 'a');
        try (LocalBridge bridge = new LocalBridge(payload, ORIGIN, 0)) {
            bridge.start();
            String paired = send(bridge.port(), ORIGIN, "POST", "/v1/pair",
                    "Content-Type: application/json\r\n", "{\"code\":\"" + bridge.code() + "\"}");
            Matcher match = Pattern.compile("\\\"token\\\":\\\"([A-Za-z0-9_-]+)\\\"").matcher(paired);
            assertTrue(match.find());
            String auth = "Authorization: Bearer " + match.group(1) + "\r\n";
            assertTrue(send(bridge.port(), ORIGIN, "GET", "/v1/metadata?cursor=1", auth, "").startsWith("HTTP/1.1 409"));
            assertTrue(send(bridge.port(), ORIGIN, "GET", "/v1/metadata", auth, "").contains("\"nextCursor\":1"));
            assertTrue(send(bridge.port(), ORIGIN, "POST", "/v1/complete", auth, "").startsWith("HTTP/1.1 409"));
            assertTrue(send(bridge.port(), ORIGIN, "GET", "/v1/metadata?cursor=1", auth, "").contains("\"nextCursor\":null"));
            assertTrue(send(bridge.port(), ORIGIN, "POST", "/v1/complete", auth, "").contains("\"completed\":true"));
            assertTrue(send(bridge.port(), ORIGIN, "POST", "/v1/acknowledge-save",
                    auth + "Content-Type: application/json\r\n", savedAcknowledgment(paired))
                    .contains("\"acknowledged\":true"));
            assertClosed(bridge.port());
        }
    }

    @Test public void multiYearSelectedPayloadTraversesEveryLocalPage() throws Exception {
        List<MetadataModel.Interaction> calls = new ArrayList<>();
        Instant base = Instant.parse("2023-01-01T00:00:00Z");
        for (int index = 0; index < 20_000; index++) {
            calls.add(new MetadataModel.Interaction(index + 1, "call", "+15550100200",
                    base.plus(index % 1_200, ChronoUnit.DAYS).toEpochMilli(), "incoming", 42L));
        }
        byte[] payload = NativePayload.selected(
                List.of(new MetadataModel.Contact(1, "Synthetic", List.of("+15550100200"))),
                calls, List.of(), Set.of(1L), true, true, Instant.parse("2026-09-28T00:00:00Z"));
        try (LocalBridge bridge = new LocalBridge(payload, ORIGIN, 0)) {
            bridge.start();
            String paired = send(bridge.port(), ORIGIN, "POST", "/v1/pair",
                    "Content-Type: application/json\r\n", "{\"code\":\"" + bridge.code() + "\"}");
            Matcher token = Pattern.compile("\\\"token\\\":\\\"([A-Za-z0-9_-]+)\\\"").matcher(paired);
            assertTrue(token.find());
            String auth = "Authorization: Bearer " + token.group(1) + "\r\n";
            int pages = (payload.length + 128 * 1024 - 1) / (128 * 1024);
            assertTrue(pages > 1);
            for (int cursor = 0; cursor < pages; cursor++) {
                String path = "/v1/metadata" + (cursor == 0 ? "" : "?cursor=" + cursor);
                String response = send(bridge.port(), ORIGIN, "GET", path, auth, "");
                assertTrue(response.startsWith("HTTP/1.1 200"));
                assertTrue(response.contains("\"cursor\":" + cursor + ","));
                assertTrue(response.contains("\"totalBytes\":" + payload.length));
                assertTrue(response.contains("\"nextCursor\":" +
                        (cursor == pages - 1 ? "null" : cursor + 1)));
            }
            assertTrue(send(bridge.port(), ORIGIN, "POST", "/v1/complete", auth, "")
                    .contains("\"completed\":true"));
            assertTrue(send(bridge.port(), ORIGIN, "POST", "/v1/acknowledge-save",
                    auth + "Content-Type: application/json\r\n", savedAcknowledgment(paired))
                    .contains("\"acknowledged\":true"));
            assertClosed(bridge.port());
        }
    }

    @Test public void fiveWrongCodesCloseTheHandoffInsteadOfLeavingItListening() throws Exception {
        try (LocalBridge bridge = new LocalBridge("{}".getBytes(StandardCharsets.US_ASCII), ORIGIN, 0)) {
            bridge.start();
            String wrong = bridge.code().equals("0000000000") ? "0000000001" : "0000000000";
            for (int attempt = 0; attempt < 5; attempt++) {
                assertTrue(send(bridge.port(), ORIGIN, "POST", "/v1/pair",
                        "Content-Type: application/json\r\n", "{\"code\":\"" + wrong + "\"}").startsWith("HTTP/1.1 403"));
            }
            assertClosed(bridge.port());
        }
    }

    @Test public void expiryClosesListenerAndNotifiesOnceWithoutAnotherRequest() throws Exception {
        AtomicLong clock = new AtomicLong();
        AtomicInteger count = new AtomicInteger();
        AtomicReference<LocalBridge.EndReason> reason = new AtomicReference<>();
        CountDownLatch ended = new CountDownLatch(1);
        try (LocalBridge bridge = new LocalBridge("{}".getBytes(StandardCharsets.US_ASCII), ORIGIN, 0,
                clock::get, result -> { reason.set(result); count.incrementAndGet(); ended.countDown(); })) {
            bridge.start();
            clock.set(TimeUnit.MILLISECONDS.toNanos(LocalBridge.LIFETIME_MILLIS));
            assertTrue("Idle expiry must notify the foreground service", ended.await(3, TimeUnit.SECONDS));
            assertEquals(LocalBridge.EndReason.EXPIRED, reason.get());
            assertClosed(bridge.port());
            bridge.close();
            assertEquals(1, count.get());
        }
    }

    @Test public void cancellationClosesAnIncompleteRequestAndCannotRestart() throws Exception {
        AtomicReference<LocalBridge.EndReason> reason = new AtomicReference<>();
        AtomicInteger count = new AtomicInteger();
        byte[] original = "{\"schema\":1}".getBytes(StandardCharsets.US_ASCII);
        try (LocalBridge bridge = new LocalBridge(original, ORIGIN, 0, System::nanoTime,
                result -> { reason.set(result); count.incrementAndGet(); })) {
            bridge.start();
            // Establish that the bridge thread is accepting requests before opening an incomplete one.
            assertTrue(send(bridge.port(), ORIGIN, "GET", "/v1/metadata", "", "").startsWith("HTTP/1.1 403"));
            try (Socket partial = new Socket(InetAddress.getByName("127.0.0.1"), bridge.port())) {
                partial.setSoTimeout(1000);
                partial.getOutputStream().write("POST /v1/pair HTTP/1.1\r\n".getBytes(StandardCharsets.US_ASCII));
                bridge.close();
                try {
                    assertEquals(-1, partial.getInputStream().read());
                } catch (java.net.SocketException closedConnection) {
                    // A reset is also terminal; a read timeout is not accepted.
                }
            }
            assertEquals(LocalBridge.EndReason.CANCELLED, reason.get());
            assertClosed(bridge.port());
            bridge.close(LocalBridge.EndReason.COMPLETED);
            assertEquals(1, count.get());
            assertEquals("{\"schema\":1}", new String(original, StandardCharsets.US_ASCII));
            assertThrows(IllegalThreadStateException.class, bridge::start);
        }
    }

    private static void assertClosed(int port) {
        assertThrows(ConnectException.class, () -> {
            try (Socket ignored = new Socket(InetAddress.getByName("127.0.0.1"), port)) {
                throw new AssertionError("Completed or cancelled handoff still accepts connections");
            }
        });
    }
}
