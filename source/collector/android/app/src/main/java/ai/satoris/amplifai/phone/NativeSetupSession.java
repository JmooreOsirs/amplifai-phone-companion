package ai.satoris.amplifai.phone;

import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.UUID;
import java.util.function.LongSupplier;
import java.util.function.Supplier;

/** Process-memory authority only. A prepared run still needs a separate, current Start admission. */
final class NativeSetupSession implements AutoCloseable {
    enum State { CLAIM_PENDING, AWAITING_APPROVAL, APPROVAL_PENDING, READY, CLOSED }
    private static final SecureRandom RANDOM = new SecureRandom();
    private final NativeSetupContract.Client client;
    private final Supplier<String> ids;
    private final Supplier<String> capabilities;
    private final LongSupplier clock;
    private final long claimStarted;
    private long approvalStarted;
    private NativeSetupContract.Link link;
    private String setupCapability;
    private String runCapability;
    private String claimId;
    private String approvalId;
    private String claimRequest;
    private String approvalRequest;
    private NativeSetupContract.Claim claim;
    private NativeSetupContract.Approval approval;
    private long deadline;
    private State state = State.CLAIM_PENDING;

    NativeSetupSession(NativeSetupContract.Link link, NativeSetupContract.Client client) {
        this(link, client, () -> UUID.randomUUID().toString(), NativeSetupSession::randomCapability, System::nanoTime);
    }

    NativeSetupSession(NativeSetupContract.Link link, NativeSetupContract.Client client,
                       Supplier<String> ids, Supplier<String> capabilities, LongSupplier clock) {
        this.link = link;
        this.client = client;
        this.ids = ids;
        this.capabilities = capabilities;
        this.clock = clock;
        claimStarted = clock.getAsLong();
        claimId = NativeSetupContract.uuid(ids.get());
        setupCapability = NativeSetupContract.capability(capabilities.get());
        NativeSetupContract.require(!setupCapability.equals(link.bootstrap()));
        claimRequest = SetupJson.encode(SetupJson.object("protocol", NativeSetupContract.PROTOCOL,
                "setupId", link.setupId(), "bootstrap", link.bootstrap(), "capability", setupCapability,
                "requestId", claimId, "client", client.fields()));
    }

    synchronized State state() { return state; }

    synchronized byte[] claimRequest() {
        NativeSetupContract.require(state == State.CLAIM_PENDING);
        return claimRequest.getBytes(StandardCharsets.UTF_8);
    }

    synchronized NativeSetupContract.Claim acceptClaim(byte[] response, Instant now) {
        NativeSetupContract.require(state == State.CLAIM_PENDING);
        NativeSetupContract.Claim next = NativeSetupContract.claim(response, link, claimId, client, now);
        long expires = Math.min(clock.getAsLong() + Duration.between(now, next.expiresAt()).toNanos(),
                claimStarted + java.util.concurrent.TimeUnit.SECONDS.toNanos(NativeSetupContract.SETUP_SECONDS));
        NativeSetupContract.require(clock.getAsLong() < expires);
        claim = next;
        deadline = expires;
        state = State.AWAITING_APPROVAL;
        return next;
    }

    synchronized byte[] beginApproval(boolean checked, Instant now) {
        NativeSetupContract.require(checked && state == State.AWAITING_APPROVAL &&
                now.isBefore(claim.expiresAt()) && clock.getAsLong() < deadline);
        approvalId = NativeSetupContract.uuid(ids.get());
        approvalStarted = clock.getAsLong();
        runCapability = NativeSetupContract.capability(capabilities.get());
        NativeSetupContract.require(!approvalId.equals(claimId) && !runCapability.equals(setupCapability) &&
                !runCapability.equals(link.bootstrap()));
        approvalRequest = SetupJson.encode(SetupJson.object("protocol", NativeSetupContract.PROTOCOL,
                "setupId", claim.setupId(), "capability", setupCapability, "requestId", approvalId,
                "runCapability", runCapability, "setupSnapshotSha256", claim.snapshot(), "client", client.fields(),
                "disclosure", NativeSetupContract.disclosureReference()));
        state = State.APPROVAL_PENDING;
        return approvalRequest.getBytes(StandardCharsets.UTF_8);
    }

    synchronized byte[] retryApproval() {
        // An already committed approval can be recovered after setup TTL, using only this exact request.
        NativeSetupContract.require(state == State.APPROVAL_PENDING);
        return approvalRequest.getBytes(StandardCharsets.UTF_8);
    }

    synchronized NativeSetupContract.Approval acceptApproval(byte[] response, Instant now) {
        NativeSetupContract.require(state == State.APPROVAL_PENDING);
        NativeSetupContract.Approval next = NativeSetupContract.approval(response, claim, approvalId, now);
        long expires = Math.min(clock.getAsLong() + Duration.between(now, next.expiresAt()).toNanos(),
                approvalStarted + Duration.between(next.approvedAt(), next.expiresAt()).toNanos());
        NativeSetupContract.require(clock.getAsLong() < expires);
        approval = next;
        deadline = expires;
        state = State.READY;
        return next;
    }

    synchronized NativeSetupContract.Approval prepared(Instant now) {
        NativeSetupContract.require(state == State.READY && now.isBefore(approval.expiresAt()) && clock.getAsLong() < deadline);
        return approval;
    }

    synchronized String runCapability(Instant now) {
        prepared(now);
        return runCapability;
    }

    synchronized NativeSetupContract.Claim claimed() {
        NativeSetupContract.require(claim != null && state != State.CLOSED);
        return claim;
    }

    @Override public synchronized void close() {
        state = State.CLOSED;
        link = null;
        setupCapability = null;
        runCapability = null;
        claimId = null;
        approvalId = null;
        claimRequest = null;
        approvalRequest = null;
        claim = null;
        approval = null;
        deadline = 0;
    }

    private static String randomCapability() {
        byte[] bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }
}
