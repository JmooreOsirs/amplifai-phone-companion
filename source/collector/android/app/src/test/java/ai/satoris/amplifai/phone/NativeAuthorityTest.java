package ai.satoris.amplifai.phone;

import org.junit.Test;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.Principal;
import java.security.cert.Certificate;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import javax.net.ssl.HttpsURLConnection;
import static org.junit.Assert.*;

public final class NativeAuthorityTest {
    private static final Instant NOW = Instant.parse("2026-10-21T22:00:00Z");
    private static final String OPERATION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    private static final String HANDOFF = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    private static final String DIGEST = "a".repeat(64);
    private static byte[] bytes(Object fields) { return SetupJson.encode(fields).getBytes(StandardCharsets.UTF_8); }
    private static Map<String, Object> fixture(String name) throws Exception {
        try (InputStream input = NativeAuthorityTest.class.getResourceAsStream("/phone-native-setup-v2.json")) {
            return NativeSetupContract.object(NativeSetupContract.object(NativeSetupContract.object(
                    SetupJson.parse(input.readAllBytes(), 65536), "fixtures"), "android"), name);
        }
    }
    private static NativeSetupSession prepared() throws Exception {
        ArrayDeque<String> ids = new ArrayDeque<>(Arrays.asList("22222222-2222-4222-8222-222222222222", "33333333-3333-4333-8333-333333333333"));
        ArrayDeque<String> caps = new ArrayDeque<>(Arrays.asList("BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBA", "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCA"));
        NativeSetupSession session = new NativeSetupSession(new NativeSetupContract.Link("44444444-4444-4444-8444-444444444444",
                "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"), new NativeSetupContract.Client("2026.10.21-test", "26102101"), ids::remove, caps::remove, () -> 0L);
        session.acceptClaim(bytes(fixture("claimResponse")), NOW); session.beginApproval(true, NOW);
        session.acceptApproval(bytes(fixture("approveResponse")), NOW);
        return session;
    }
    private static Map<String, Object> startResponse() throws Exception {
        Map<String, Object> fields = NativeSetupContract.object(fixture("approveResponse"), "preparation");
        fields.put("operation", SetupJson.object("operationId", OPERATION, "admittedAt", NOW.toString()));
        return fields;
    }
    private static Map<String, Object> bindResponse() throws Exception {
        Map<String, Object> fields = startResponse();
        fields.remove("disclosureVersion"); fields.remove("expiresAt"); fields.remove("state"); fields.remove("operation");
        fields.put("operationId", OPERATION); return fields;
    }

    @Test public void explicitStartAndContinueFreezeExactRequestsAcrossUncertainReplies() throws Exception {
        AtomicReference<byte[]> previous = new AtomicReference<>();
        AtomicReference<byte[]> response = new AtomicReference<>();
        NativeAuthorityTransport transport = (route, request) -> {
            if (previous.get() == null) previous.set(request); else assertArrayEquals(previous.get(), request);
            if (response.get() == null) throw new NativeAuthorityTransport.Failure(true);
            return response.get();
        };
        try (NativeRunAuthority run = new NativeRunAuthority(prepared(), transport, () -> NOW, () -> OPERATION)) {
            assertFalse(run.canRead("contacts"));
            assertThrows(IllegalArgumentException.class, () -> run.bind(HANDOFF, DIGEST));
            assertThrows(NativeAuthorityTransport.Failure.class, run::start);
            assertEquals(NativeRunAuthority.State.START_PENDING, run.state()); assertFalse(run.canRead("contacts"));
            response.set(bytes(startResponse())); run.start();
            assertTrue(run.canRead("contacts")); run.beginRead("contacts"); assertFalse(run.canRead("contacts"));
            assertFalse(run.canRead("calendar"));
            previous.set(null); response.set(null);
            assertThrows(NativeAuthorityTransport.Failure.class, () -> run.bind(HANDOFF, DIGEST));
            assertFalse(run.canRead("calls"));
            assertThrows(IllegalArgumentException.class, () -> run.bind(OPERATION, DIGEST));
            assertThrows(IllegalArgumentException.class, () -> run.bind(HANDOFF, "b".repeat(64)));
            response.set(bytes(bindResponse())); run.bind(HANDOFF, DIGEST);
            assertEquals(NativeRunAuthority.State.BOUND, run.state());
        }
    }

    @Test public void startRejectsWrongRunScopeExpiryDisclosureAndOperation() throws Exception {
        for (String field : Arrays.asList("approvalId", "enrollmentId", "destinationEmail", "destinationCourse", "categories",
                "disclosureVersion", "expiresAt", "state", "operation")) {
            Map<String, Object> response = startResponse(); response.put(field, "changed");
            try (NativeRunAuthority run = new NativeRunAuthority(prepared(), (route, request) -> bytes(response), () -> NOW, () -> OPERATION)) {
                assertThrows(field, IllegalArgumentException.class, run::start);
                assertFalse(run.canRead("contacts"));
            }
        }
    }

    @Test public void cancellationDuringResponseCannotReadBindOrRelease() throws Exception {
        AtomicReference<NativeRunAuthority> active = new AtomicReference<>();
        byte[] response = bytes(startResponse());
        NativeRunAuthority run = new NativeRunAuthority(prepared(), (route, request) -> {
            active.get().close(); return response;
        }, () -> NOW, () -> OPERATION);
        active.set(run);
        assertThrows(IllegalArgumentException.class, run::start);
        assertEquals(NativeRunAuthority.State.CLOSED, run.state());
        assertFalse(run.canRead("contacts"));
        assertNull(run.claim(OPERATION, "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", HANDOFF, DIGEST));
    }

    @Test public void verifiedRunAndDestinationGateClaimAndConsumeRequiresItsExactProof() throws Exception {
        Map<String, Object> verified = bindResponse(); verified.remove("protocol"); verified.remove("operationId");
        AtomicReference<String> changed = new AtomicReference<>();
        AtomicReference<NativeRunAuthority> owner = new AtomicReference<>();
        AtomicInteger connects = new AtomicInteger(), consumes = new AtomicInteger();
        byte[] start = bytes(startResponse()), bind = bytes(bindResponse());
        NativeAuthorityTransport transport = (route, request) -> {
            Map<String, Object> command = SetupJson.parse(request, 4096);
            if (route == NativeAuthorityTransport.Route.START) return start;
            if (route == NativeAuthorityTransport.Route.BIND) return bind;
            if (route == NativeAuthorityTransport.Route.VERIFY) {
                assertEquals("88888888-8888-4888-8888-888888888888", command.get("runId"));
                assertEquals(HANDOFF, command.get("handoffId")); assertEquals(DIGEST, command.get("payloadSha256"));
                Map<String, Object> result = new java.util.LinkedHashMap<>(verified);
                if (changed.get() != null && !changed.get().equals("cancel")) result.put(changed.get(), "changed");
                return bytes(result);
            }
            if (route == NativeAuthorityTransport.Route.CONNECT) {
                connects.incrementAndGet();
                return bytes(SetupJson.object("destinationEmail", verified.get("destinationEmail"),
                        "destinationCourse", verified.get("destinationCourse")));
            }
            if (route == NativeAuthorityTransport.Route.CONSUME) {
                consumes.incrementAndGet();
                if ("cancel".equals(changed.get())) owner.get().close();
                return bytes(SetupJson.object("consumed", true));
            }
            throw new AssertionError("Unexpected route");
        };
        try (NativeRunAuthority run = new NativeRunAuthority(prepared(), transport, () -> NOW, () -> OPERATION)) {
            owner.set(run); run.start(); run.bind(HANDOFF, DIGEST);
            String cap = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
            assertNull(run.claim(OPERATION, cap, OPERATION, DIGEST));
            for (String field : Arrays.asList("approvalId", "enrollmentId", "destinationEmail", "destinationCourse", "categories")) {
                changed.set(field); assertNull(run.claim(OPERATION, cap, HANDOFF, DIGEST));
                assertFalse(run.consume(OPERATION, cap, cap, HANDOFF, DIGEST));
            }
            changed.set(null);
            assertNull(run.claim(OPERATION, "K".repeat(43), HANDOFF, DIGEST));
            assertEquals(5, connects.get());
            assertFalse(run.consume(OPERATION, "K".repeat(43), cap, HANDOFF, DIGEST));
            assertFalse(run.consume(OPERATION, cap, "R".repeat(43), HANDOFF, DIGEST));
            assertEquals(0, consumes.get());
            assertNotNull(run.claim(OPERATION, cap, HANDOFF, DIGEST));
            assertEquals(6, connects.get());
            assertTrue(run.consume(OPERATION, cap, cap, HANDOFF, DIGEST));
            assertEquals(1, consumes.get());
            changed.set("cancel"); assertFalse(run.consume(OPERATION, cap, cap, HANDOFF, DIGEST));
        }
    }

    @Test public void claimedIntentIsVerifiedBeforeApprovalAndReleasedIntentIsConsumedWithoutPendingVerification() throws Exception {
        HandshakeServer server = new HandshakeServer();
        try (NativeRunAuthority run = new NativeRunAuthority(prepared(), server, () -> NOW, () -> OPERATION)) {
            run.start(); run.bind(HANDOFF, DIGEST);
            assertFalse(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.RELEASE, HANDOFF, DIGEST));
            assertEquals(0, server.consumes);
            assertNotNull(run.claim(OPERATION, HandshakeServer.CAP, HANDOFF, DIGEST));
            assertEquals(Arrays.asList("claim", "verify"), server.events);
            // A valid local verification does not authorize consumption without server release.
            assertFalse(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.RELEASE, HANDOFF, DIGEST));
            server.released = true;
            assertTrue(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.RELEASE, HANDOFF, DIGEST));
            assertEquals(1, server.verifies);
            assertTrue(server.consumed);
        }
    }

    @Test public void consumeRetryPreservesVerifiedBindingAfterLostReplyButStillChecksCurrentServerAuthority() throws Exception {
        HandshakeServer server = new HandshakeServer();
        try (NativeRunAuthority run = new NativeRunAuthority(prepared(), server, () -> NOW, () -> OPERATION)) {
            run.start(); run.bind(HANDOFF, DIGEST);
            assertNotNull(run.claim(OPERATION, HandshakeServer.CAP, HANDOFF, DIGEST));
            server.released = true; server.loseConsumeReply = true;
            assertFalse(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.RELEASE, HANDOFF, DIGEST));
            assertTrue(server.consumed);
            assertTrue(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.RELEASE, HANDOFF, DIGEST));
            assertEquals(2, server.consumes); assertEquals(1, server.verifies);
            assertArrayEquals(server.firstConsumeRequest, server.lastConsumeRequest);
            server.current = false;
            assertFalse(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.RELEASE, HANDOFF, DIGEST));
            assertEquals(3, server.consumes);
            run.close();
            assertFalse(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.RELEASE, HANDOFF, DIGEST));
            assertEquals(3, server.consumes);
        }
    }

    @Test public void failedScopeVerificationAndChangedBindingsCannotReachConsume() throws Exception {
        HandshakeServer server = new HandshakeServer();
        AtomicReference<Instant> time = new AtomicReference<>(NOW);
        try (NativeRunAuthority run = new NativeRunAuthority(prepared(), server, time::get, () -> OPERATION)) {
            run.start(); run.bind(HANDOFF, DIGEST);
            server.verifyAllowed = false;
            assertNull(run.claim(OPERATION, HandshakeServer.CAP, HANDOFF, DIGEST));
            assertFalse(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.RELEASE, HANDOFF, DIGEST));
            assertEquals(0, server.consumes);
            server.verifyAllowed = true;
            assertNotNull(run.claim(OPERATION, HandshakeServer.CAP, HANDOFF, DIGEST));
            server.released = true;
            assertFalse(run.consume(HANDOFF, HandshakeServer.CAP, HandshakeServer.RELEASE, HANDOFF, DIGEST));
            assertFalse(run.consume(OPERATION, HandshakeServer.OTHER_CAP, HandshakeServer.RELEASE, HANDOFF, DIGEST));
            assertFalse(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.RELEASE, OPERATION, DIGEST));
            assertFalse(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.RELEASE, HANDOFF, "b".repeat(64)));
            assertEquals(0, server.consumes);
            assertFalse(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.OTHER_CAP, HANDOFF, DIGEST));
            assertEquals(1, server.consumes); // Wrong release is rejected by the server, never treated as success.
            time.set(run.approval().expiresAt());
            assertFalse(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.RELEASE, HANDOFF, DIGEST));
            assertEquals(1, server.consumes);
        }
    }

    @Test public void failedReclaimClearsPriorProofAndExpiryDuringConsumeRejectsSuccess() throws Exception {
        HandshakeServer server = new HandshakeServer();
        AtomicReference<Instant> time = new AtomicReference<>(NOW);
        try (NativeRunAuthority run = new NativeRunAuthority(prepared(), server, time::get, () -> OPERATION)) {
            run.start(); run.bind(HANDOFF, DIGEST);
            assertNotNull(run.claim(OPERATION, HandshakeServer.CAP, HANDOFF, DIGEST));
            server.verifyAllowed = false;
            assertNull(run.claim(OPERATION, HandshakeServer.CAP, HANDOFF, DIGEST));
            server.released = true;
            assertFalse(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.RELEASE, HANDOFF, DIGEST));
            assertEquals(0, server.consumes);
            server.released = false; server.verifyAllowed = true;
            assertNotNull(run.claim(OPERATION, HandshakeServer.CAP, HANDOFF, DIGEST));
            server.released = true;
            Instant expires = run.approval().expiresAt();
            server.beforeConsumeReply = () -> time.set(expires);
            assertFalse(run.consume(OPERATION, HandshakeServer.CAP, HandshakeServer.RELEASE, HANDOFF, DIGEST));
            assertEquals(1, server.consumes);
        }
    }

    /** Models the published SQL's distinct pending-claimed and released-consume phases. */
    private static final class HandshakeServer implements NativeAuthorityTransport {
        static final String CAP = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
        static final String RELEASE = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBA";
        static final String OTHER_CAP = "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCA";
        final java.util.List<String> events = new java.util.ArrayList<>();
        final Map<String, Object> destination;
        final byte[] start, bind;
        boolean claimed, released, consumed, loseConsumeReply;
        boolean current = true, verifyAllowed = true;
        int verifies, consumes;
        byte[] firstConsumeRequest, lastConsumeRequest;
        Runnable beforeConsumeReply = () -> {};
        HandshakeServer() throws Exception {
            start = bytes(startResponse()); bind = bytes(bindResponse());
            destination = bindResponse(); destination.remove("protocol"); destination.remove("operationId");
        }
        @Override public byte[] post(Route route, byte[] request) throws Failure {
            if (route == Route.START) return start;
            if (route == Route.BIND) return bind;
            Map<String, Object> body = SetupJson.parse(request, 4096);
            if (route == Route.CONNECT) {
                events.add("claim"); requireConnection(body);
                if (!current || released) throw new Failure(false);
                claimed = true;
                return bytes(SetupJson.object("destinationEmail", destination.get("destinationEmail"),
                        "destinationCourse", destination.get("destinationCourse")));
            }
            if (route == Route.VERIFY) {
                events.add("verify"); verifies++;
                assertEquals("88888888-8888-4888-8888-888888888888", body.get("runId"));
                assertEquals(destination.get("approvalId"), body.get("expectedApprovalId"));
                assertEquals(OPERATION, body.get("intentId")); assertEquals(HANDOFF, body.get("handoffId"));
                assertEquals(DIGEST, body.get("payloadSha256"));
                if (!current || !verifyAllowed || !claimed || released || consumed) throw new Failure(false);
                return bytes(destination);
            }
            if (route == Route.CONSUME) {
                events.add("consume"); consumes++; requireConnection(body);
                if (!current || !claimed || !released || !RELEASE.equals(body.get("releaseCode"))) throw new Failure(false);
                if (firstConsumeRequest == null) firstConsumeRequest = request.clone();
                lastConsumeRequest = request.clone();
                consumed = true;
                if (loseConsumeReply) { loseConsumeReply = false; throw new Failure(true); }
                beforeConsumeReply.run();
                return bytes(SetupJson.object("consumed", true));
            }
            throw new AssertionError("Unexpected route");
        }
        private void requireConnection(Map<String, Object> body) throws Failure {
            if (!OPERATION.equals(body.get("intentId")) || !CAP.equals(body.get("capability"))
                    || !HANDOFF.equals(body.get("handoffId")) || !DIGEST.equals(body.get("payloadSha256"))) throw new Failure(false);
        }
    }

    @Test public void startAllowsOnlyBoundedTimestampSkew() throws Exception {
        for (int seconds : new int[]{59, 61}) {
            Map<String, Object> response = startResponse();
            NativeSetupContract.object(response, "operation").put("admittedAt", NOW.plusSeconds(seconds).toString());
            try (NativeRunAuthority run = new NativeRunAuthority(prepared(), (route, request) -> bytes(response), () -> NOW, () -> OPERATION)) {
                if (seconds == 59) { run.start(); assertTrue(run.canRead("contacts")); }
                else assertThrows(IllegalArgumentException.class, run::start);
            }
        }
    }

    @Test public void fixedHttpsRejectsRedirectsWrongSuccessTypesSizesAndStatusWithoutFollowing() throws Exception {
        for (int status : new int[]{200, 201, 302, 400, 404, 409, 503}) {
            FakeConnection connection = new FakeConnection(status, "application/json", "{}".getBytes(StandardCharsets.UTF_8));
            NativeAuthorityTransport transport = new NativeAuthorityTransport.Https(target -> {
                assertEquals("https://amplifai.satoris.ai/api/v1/phone-platform-setups/claim", target.toString());
                return connection;
            });
            if (status == 200) assertArrayEquals("{}".getBytes(StandardCharsets.UTF_8), transport.post(NativeAuthorityTransport.Route.CLAIM, bytes(SetupJson.object())));
            else assertEquals(status == 503, assertThrows(NativeAuthorityTransport.Failure.class,
                    () -> transport.post(NativeAuthorityTransport.Route.CLAIM, bytes(SetupJson.object()))).retryable);
            assertFalse(connection.getInstanceFollowRedirects()); assertTrue(connection.disconnected);
            assertNull(connection.getRequestProperty("Cookie")); assertNull(connection.getRequestProperty("Authorization"));
        }
        for (String type : Arrays.asList("text/html", "application/jsonp", "application/json;charset=utf-8")) {
            FakeConnection connection = new FakeConnection(201, type, bytes(SetupJson.object()));
            NativeAuthorityTransport transport = new NativeAuthorityTransport.Https(target -> connection);
            if (type.startsWith("application/json;")) transport.post(NativeAuthorityTransport.Route.APPROVE, bytes(SetupJson.object()));
            else assertThrows(NativeAuthorityTransport.Failure.class, () -> transport.post(NativeAuthorityTransport.Route.APPROVE, bytes(SetupJson.object())));
        }
        FakeConnection oversized = new FakeConnection(200, "application/json", new byte[8193]);
        assertThrows(NativeAuthorityTransport.Failure.class, () -> new NativeAuthorityTransport.Https(target -> oversized)
                .post(NativeAuthorityTransport.Route.CLAIM, bytes(SetupJson.object())));
    }

    private static final class FakeConnection extends HttpsURLConnection {
        final int status; final String type; final byte[] body; boolean disconnected;
        FakeConnection(int status, String type, byte[] body) throws Exception { super(new URL("https://amplifai.satoris.ai")); this.status = status; this.type = type; this.body = body; }
        @Override public int getResponseCode() { return status; }
        @Override public String getContentType() { return type; }
        @Override public int getContentLength() { return -1; }
        @Override public InputStream getInputStream() { return new ByteArrayInputStream(body); }
        @Override public ByteArrayOutputStream getOutputStream() { return new ByteArrayOutputStream(); }
        @Override public void connect() {}
        @Override public void disconnect() { disconnected = true; }
        @Override public boolean usingProxy() { return false; }
        @Override public String getCipherSuite() { return "fixture"; }
        @Override public Certificate[] getLocalCertificates() { return null; }
        @Override public Certificate[] getServerCertificates() { return null; }
        @Override public Principal getPeerPrincipal() { return null; }
        @Override public Principal getLocalPrincipal() { return null; }
    }
}
