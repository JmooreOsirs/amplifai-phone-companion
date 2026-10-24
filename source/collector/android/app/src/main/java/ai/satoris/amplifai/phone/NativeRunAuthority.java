package ai.satoris.amplifai.phone;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.function.Supplier;

import static ai.satoris.amplifai.phone.NativeSetupContract.*;

/** One prepared approval, one explicit Start and one immutable reviewed payload. Never persisted. */
final class NativeRunAuthority implements LocalBridge.AccountProof, AutoCloseable {
    enum State { READY, START_PENDING, STARTED, BIND_PENDING, BOUND, CLOSED }
    private final NativeSetupSession setup;
    private final NativeAuthorityTransport transport;
    private final Supplier<Instant> now;
    private final Supplier<String> ids;
    private final Set<String> readSources = new HashSet<>();
    private State state = State.READY;
    private String operationId;
    private String startRequest;
    private String bindRequest;
    private String handoffId;
    private String digest;
    private String verifiedIntentId;
    private String verifiedIntentCapability;

    NativeRunAuthority(NativeSetupSession setup, NativeAuthorityTransport transport, Supplier<Instant> now, Supplier<String> ids) {
        this.setup = setup; this.transport = transport; this.now = now; this.ids = ids;
        setup.prepared(now.get());
    }
    synchronized State state() { return state; }
    NativeSetupSession setup() { return setup; }
    synchronized Approval approval() { require(state != State.CLOSED); return setup.prepared(now.get()); }
    @Override public synchronized boolean current() {
        try { approval(); return true; } catch (IllegalArgumentException stopped) { return false; }
    }

    void start() throws NativeAuthorityTransport.Failure {
        String request;
        synchronized (this) {
            require(state == State.READY || state == State.START_PENDING);
            Approval approval = approval();
            if (startRequest == null) {
                operationId = uuid(ids.get());
                Map<String, Object> fields = reference();
                fields.put("expectedApprovalId", approval.approvalId()); fields.put("operationId", operationId);
                startRequest = SetupJson.encode(fields);
            }
            state = State.START_PENDING;
            request = startRequest;
        }
        byte[] response = transport.post(NativeAuthorityTransport.Route.START, bytes(request));
        synchronized (this) {
            require(state == State.START_PENDING && request.equals(startRequest));
            validateStart(response);
            state = State.STARTED;
        }
    }

    synchronized boolean canRead(String category) {
        return state == State.STARTED && current() && approval().scope().categories().contains(category) && !readSources.contains(category);
    }
    synchronized void beginRead(String category) { require(canRead(category)); readSources.add(category); }

    void bind(String handoff, String payload) throws NativeAuthorityTransport.Failure {
        String request;
        synchronized (this) {
            require(state == State.STARTED || state == State.BIND_PENDING || state == State.BOUND);
            Approval approval = approval();
            uuid(handoff); require(payload != null && payload.matches("[0-9a-f]{64}"));
            if (bindRequest == null) {
                handoffId = handoff; digest = payload;
                Map<String, Object> fields = reference();
                fields.put("expectedApprovalId", approval.approvalId()); fields.put("operationId", operationId);
                fields.put("handoffId", handoff); fields.put("payloadSha256", payload);
                bindRequest = SetupJson.encode(fields);
            }
            require(handoff.equals(handoffId) && payload.equals(digest));
            state = State.BIND_PENDING;
            request = bindRequest;
        }
        byte[] response = transport.post(NativeAuthorityTransport.Route.BIND, bytes(request));
        synchronized (this) {
            require(state == State.BIND_PENDING && request.equals(bindRequest));
            Map<String, Object> fields = SetupJson.parse(response, 2048);
            keys(fields, "protocol", "approvalId", "operationId", "enrollmentId", "destinationEmail", "destinationCourse", "categories");
            equal(fields, "protocol", "phone-start-v1"); equal(fields, "operationId", operationId);
            validateDestination(fields);
            state = State.BOUND;
        }
    }

    @Override public LocalBridge.Destination claim(String intent, String capability, String handoff, String payload) {
        try {
            Map<String, Object> request;
            synchronized (this) {
                require(bound(handoff, payload));
                request = connect(intent, capability, handoff, payload);
                verifiedIntentId = verifiedIntentCapability = null;
            }
            // The server verifies a pending *claimed* intent. Claim first, then check
            // its exact run/approval scope before allowing the bridge to approve it.
            Map<String, Object> fields = SetupJson.parse(transport.post(NativeAuthorityTransport.Route.CONNECT,
                    bytes(SetupJson.encode(request))), 1024);
            synchronized (this) {
                require(bound(handoff, payload));
                keys(fields, "destinationEmail", "destinationCourse");
                equal(fields, "destinationEmail", approval().scope().email());
                equal(fields, "destinationCourse", approval().scope().course());
            }
            verify(intent, handoff, payload);
            synchronized (this) {
                require(bound(handoff, payload));
                verifiedIntentId = intent;
                verifiedIntentCapability = capability;
                return new LocalBridge.Destination(intent, approval().scope().email(), approval().scope().course());
            }
        } catch (NativeAuthorityTransport.Failure | IllegalArgumentException unavailable) { return null; }
    }

    @Override public boolean consume(String intent, String capability, String release, String handoff, String payload) {
        try {
            Map<String, Object> request;
            synchronized (this) {
                require(verifiedConnection(intent, capability, handoff, payload));
                request = connect(intent, capability, handoff, payload);
                request.put("releaseCode", NativeSetupContract.capability(release));
            }
            // Pending-intent verification intentionally rejects released intents.
            // The checked consume route now revalidates current server authority,
            // the exact release proof, and idempotent retries after a lost reply.
            Map<String, Object> fields = SetupJson.parse(transport.post(NativeAuthorityTransport.Route.CONSUME,
                    bytes(SetupJson.encode(request))), 1024);
            synchronized (this) {
                require(verifiedConnection(intent, capability, handoff, payload)); keys(fields, "consumed");
                return Boolean.TRUE.equals(fields.get("consumed"));
            }
        } catch (NativeAuthorityTransport.Failure | IllegalArgumentException unavailable) { return false; }
    }
    @Override public boolean approvedRun() { return true; }

    private void verify(String intent, String handoff, String payload) throws NativeAuthorityTransport.Failure {
        String request;
        synchronized (this) {
            require(bound(handoff, payload)); uuid(intent);
            Map<String, Object> fields = reference();
            fields.put("intentId", intent); fields.put("handoffId", handoff); fields.put("payloadSha256", payload);
            fields.put("expectedApprovalId", approval().approvalId()); request = SetupJson.encode(fields);
        }
        byte[] response = transport.post(NativeAuthorityTransport.Route.VERIFY, bytes(request));
        synchronized (this) {
            require(bound(handoff, payload));
            Map<String, Object> fields = SetupJson.parse(response, 2048);
            keys(fields, "approvalId", "enrollmentId", "destinationEmail", "destinationCourse", "categories");
            validateDestination(fields);
        }
    }
    private boolean bound(String handoff, String payload) {
        return state == State.BOUND && current() && handoff.equals(handoffId) && payload.equals(digest);
    }
    private boolean verifiedConnection(String intent, String capability, String handoff, String payload) {
        return bound(handoff, payload) && intent.equals(verifiedIntentId) && capability.equals(verifiedIntentCapability);
    }
    private Map<String, Object> reference() {
        return SetupJson.object("runId", approval().runId(), "capability", setup.runCapability(now.get()));
    }
    private static Map<String, Object> connect(String intent, String capability, String handoff, String payload) {
        return SetupJson.object("intentId", uuid(intent), "capability", NativeSetupContract.capability(capability),
                "handoffId", handoff, "payloadSha256", payload);
    }
    private void validateDestination(Map<String, Object> fields) {
        Approval expected = approval();
        equal(fields, "approvalId", expected.approvalId()); equal(fields, "enrollmentId", expected.scope().enrollmentId());
        equal(fields, "destinationEmail", expected.scope().email()); equal(fields, "destinationCourse", expected.scope().course());
        require(expected.scope().categories().equals(fields.get("categories")));
    }
    private void validateStart(byte[] response) {
        Map<String, Object> fields = SetupJson.parse(response, 2048);
        keys(fields, "protocol", "approvalId", "enrollmentId", "destinationEmail", "destinationCourse", "categories",
                "disclosureVersion", "expiresAt", "state", "operation");
        equal(fields, "protocol", "phone-start-v1"); equal(fields, "state", "current");
        equal(fields, "disclosureVersion", DISCLOSURE_VERSION); validateDestination(fields);
        require(approval().expiresAt().equals(time(fields, "expiresAt")));
        Map<String, Object> operation = object(fields, "operation");
        keys(operation, "operationId", "admittedAt"); equal(operation, "operationId", operationId);
        Instant admitted = time(operation, "admittedAt");
        require(!admitted.isBefore(approval().approvedAt()) && !admitted.isAfter(now.get().plusSeconds(CLOCK_SKEW_SECONDS)));
    }
    private static byte[] bytes(String text) { return text.getBytes(StandardCharsets.UTF_8); }
    @Override public synchronized void close() {
        state = State.CLOSED; setup.close(); readSources.clear();
        operationId = startRequest = bindRequest = handoffId = digest = null;
        verifiedIntentId = verifiedIntentCapability = null;
    }
}
