package ai.satoris.amplifai.phone;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertThrows;

import org.junit.Test;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayDeque;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Consumer;

public final class NativeSetupContractTest {
    private static final Instant NOW = Instant.parse("2026-10-21T22:00:00Z");
    private static final String SETUP = "44444444-4444-4444-8444-444444444444";
    private static final String CLAIM = "22222222-2222-4222-8222-222222222222";
    private static final String APPROVE = "33333333-3333-4333-8333-333333333333";
    private static final String BOOTSTRAP = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
    private static final String SETUP_CAP = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBA";
    private static final String RUN_CAP = "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCA";
    private static final NativeSetupContract.Client CLIENT = new NativeSetupContract.Client("2026.10.21-test", "26102101");
    private static final String LINK = "amplifai-phone://setup?protocol=phone-native-setup-v2&setup=" + SETUP + "&bootstrap=" + BOOTSTRAP;

    private static byte[] catalogBytes() throws Exception {
        try (InputStream input = NativeSetupContractTest.class.getResourceAsStream("/phone-native-setup-v2.json")) {
            if (input == null) throw new AssertionError("Missing frozen contract fixture");
            return input.readAllBytes();
        }
    }
    private static Map<String, Object> catalog() throws Exception { return SetupJson.parse(catalogBytes(), 65536); }
    private static Map<String, Object> fixture(String name) throws Exception {
        return NativeSetupContract.object(NativeSetupContract.object(NativeSetupContract.object(catalog(), "fixtures"), "android"), name);
    }
    private static byte[] bytes(Object value) { return SetupJson.encode(value).getBytes(StandardCharsets.UTF_8); }
    private static NativeSetupContract.Claim claim() throws Exception {
        return NativeSetupContract.claim(bytes(fixture("claimResponse")), NativeSetupContract.link(LINK), CLAIM, CLIENT, NOW);
    }
    private static NativeSetupSession session(AtomicLong clock) {
        ArrayDeque<String> ids = new ArrayDeque<>(Arrays.asList(CLAIM, APPROVE));
        ArrayDeque<String> capabilities = new ArrayDeque<>(Arrays.asList(SETUP_CAP, RUN_CAP));
        return new NativeSetupSession(NativeSetupContract.link(LINK), CLIENT, ids::remove, capabilities::remove, clock::get);
    }

    @Test public void frozenCatalogDisclosureSupplementAndCapabilitiesMatchExactBytes() throws Exception {
        assertEquals("d2ff30a92fab809960dcfe0520e08585a7437026ab6c422deb66f78012984ac8", NativeSetupContract.sha256(catalogBytes()));
        assertEquals(NativeSetupContract.DISCLOSURE_SHA256,
                NativeSetupContract.sha256(NativeSetupContract.disclosureText().getBytes(StandardCharsets.UTF_8)));
        Map<String, Object> disclosure = NativeSetupContract.object(fixture("claimResponse"), "disclosure");
        assertEquals(NativeSetupContract.SENTENCE, ((List<?>) disclosure.get("sentences")).get(0));
        assertEquals(NativeSetupContract.AGREEMENT, disclosure.get("agreement"));
        Map<String, Object> supplement = NativeSetupContract.object(catalog(), "supplement");
        StringBuilder text = new StringBuilder((String) supplement.get("title"));
        for (Object paragraph : (List<?>) supplement.get("paragraphs")) text.append("\n\n").append(paragraph);
        assertEquals(NativeSetupContract.SUPPLEMENT_SHA256,
                NativeSetupContract.sha256(text.toString().getBytes(StandardCharsets.UTF_8)));
        assertEquals(fixture("capabilities"), SetupJson.parse(NativeSetupContract.capabilities(CLIENT).getBytes(StandardCharsets.UTF_8), 4096));
    }

    @Test public void deepLinksCannotSupplyAuthorityScopeOrNoncanonicalCapabilities() {
        assertEquals(SETUP, NativeSetupContract.link(LINK).setupId());
        for (String invalid : Arrays.asList(LINK + "&ownerId=" + SETUP, LINK + "&bootstrap=" + BOOTSTRAP,
                LINK + "#fragment", LINK.replace("://setup?", "://setup/?"), LINK.replace("://setup?", "://user@setup?"),
                LINK.replace("://setup?", "://setup:443?"), LINK.replace("phone-native-setup-v2", "phone-native-setup-v1"),
                LINK.replace("bootstrap=", "bootstrap=%41"), LINK.replace(BOOTSTRAP, "B".repeat(43)),
                LINK.replace("://setup?", "://open?"), LINK.replace("amplifai-phone:", "https:")))
            assertThrows(invalid, IllegalArgumentException.class, () -> NativeSetupContract.link(invalid));
        assertFalse(NativeSetupContract.link(LINK).toString().contains(BOOTSTRAP));
    }

    @Test public void strictJsonRejectsDuplicatesMalformedUnicodeAndLenientExtensions() {
        for (String invalid : Arrays.asList("{\"a\":1,\"a\":2}", "{\"a\":1,\"\\u0061\":2}", "{\"a\":null,\"a\":1}",
                "{a:1}", "{'a':1}", "{\"a\":1,}", "{\"a\":[1,]}", "{}{}", "{/*x*/\"a\":1}",
                "{\"a\":01}", "{\"a\":1.0}", "{\"a\":1e2}", "{\"a\":\"\\ud800\"}", "{\"a\":\"\\uＦＦＦＦ\"}", "\ufeff{}"))
            assertThrows(IllegalArgumentException.class, () -> SetupJson.parse(invalid.getBytes(StandardCharsets.UTF_8), 1024));
        assertThrows(IllegalArgumentException.class, () -> SetupJson.parse(new byte[]{'{', '"', (byte) 0xff, '"', ':', '0', '}'}, 1024));
        assertThrows(IllegalArgumentException.class, () -> SetupJson.parse(new byte[8193], 8192));
    }

    @Test public void claimsRejectWrongPlatformIdentityTextScopeOrderAndExpiry() throws Exception {
        assertEquals("fixture@example.test", claim().scope().email());
        List<Consumer<Map<String, Object>>> changes = Arrays.asList(
                fields -> fields.put("extra", true), fields -> fields.put("requestId", APPROVE),
                fields -> fields.put("setupId", CLAIM), fields -> fields.put("state", "approved"),
                fields -> fields.put("categories", Arrays.asList("calls", "contacts")),
                fields -> fields.put("categories", Arrays.asList("contacts", "contacts")),
                fields -> fields.put("categories", Arrays.asList("contacts", "calendar")),
                fields -> fields.put("ownerId", "bad"), fields -> fields.put("destinationCourse", "bad\nlabel"),
                fields -> fields.put("expiresAt", "2026-10-21T22:00:00.000Z"),
                fields -> fields.put("expiresAt", "2026-10-21T22:11:01.000Z"),
                fields -> NativeSetupContract.object(fields, "client").put("channel", "android-play"),
                fields -> NativeSetupContract.object(fields, "client").put("version", "different"),
                fields -> NativeSetupContract.object(fields, "disclosure").put("agreement", "I approve saving"),
                fields -> NativeSetupContract.object(fields, "disclosure").put("privacyHref", "https://other.example"),
                fields -> NativeSetupContract.object(fields, "disclosure").put("supplementSha256", "0".repeat(64)));
        for (Consumer<Map<String, Object>> change : changes) {
            Map<String, Object> fields = fixture("claimResponse"); change.accept(fields);
            assertThrows(IllegalArgumentException.class,
                    () -> NativeSetupContract.claim(bytes(fields), NativeSetupContract.link(LINK), CLAIM, CLIENT, NOW));
        }
        Map<String, Object> windows = NativeSetupContract.object(NativeSetupContract.object(NativeSetupContract.object(catalog(), "fixtures"), "windows"), "claimResponse");
        assertThrows(IllegalArgumentException.class,
                () -> NativeSetupContract.claim(bytes(windows), NativeSetupContract.link(LINK), CLAIM, CLIENT, NOW));
    }

    @Test public void approvalBindsEveryVisibleAuthorityFieldAndPreparation() throws Exception {
        NativeSetupContract.Claim claim = claim();
        assertEquals("88888888-8888-4888-8888-888888888888",
                NativeSetupContract.approval(bytes(fixture("approveResponse")), claim, APPROVE, NOW).runId());
        for (String field : Arrays.asList("ownerId", "enrollmentId", "courseId", "setupId", "requestId")) {
            Map<String, Object> fields = fixture("approveResponse"); fields.put(field, CLAIM);
            assertThrows(field, IllegalArgumentException.class, () -> NativeSetupContract.approval(bytes(fields), claim, APPROVE, NOW));
        }
        for (String field : Arrays.asList("destinationEmail", "destinationCourse", "setupSnapshotSha256", "approvalLocation",
                "disclosureVersion", "disclosureSha256", "supplementVersion", "supplementSha256")) {
            Map<String, Object> fields = fixture("approveResponse"); fields.put(field, "changed");
            assertThrows(field, IllegalArgumentException.class, () -> NativeSetupContract.approval(bytes(fields), claim, APPROVE, NOW));
        }
        for (String field : Arrays.asList("approvalId", "enrollmentId", "destinationEmail", "destinationCourse", "disclosureVersion", "expiresAt", "state", "operation")) {
            Map<String, Object> fields = fixture("approveResponse"); NativeSetupContract.object(fields, "preparation").put(field, "changed");
            assertThrows(field, IllegalArgumentException.class, () -> NativeSetupContract.approval(bytes(fields), claim, APPROVE, NOW));
        }
        Map<String, Object> extended = fixture("approveResponse"); extended.put("expiresAt", "2026-10-22T10:00:01.000Z");
        NativeSetupContract.object(extended, "preparation").put("expiresAt", extended.get("expiresAt"));
        assertThrows(IllegalArgumentException.class, () -> NativeSetupContract.approval(bytes(extended), claim, APPROVE, NOW));
    }

    @Test public void setupAndApprovalRequestsAreExactIndependentAndNeverImplyStart() throws Exception {
        AtomicLong clock = new AtomicLong();
        try (NativeSetupSession session = session(clock)) {
            byte[] first = session.claimRequest();
            assertArrayEquals(first, session.claimRequest());
            assertEquals(fixture("claimRequest"), SetupJson.parse(first, 4096));
            assertThrows(IllegalArgumentException.class, () -> session.prepared(NOW));
            session.acceptClaim(bytes(fixture("claimResponse")), NOW);
            assertThrows(IllegalArgumentException.class, () -> session.beginApproval(false, NOW));
            assertEquals(NativeSetupSession.State.AWAITING_APPROVAL, session.state());
            byte[] approval = session.beginApproval(true, NOW);
            assertEquals(fixture("approveRequest"), SetupJson.parse(approval, 4096));
            assertArrayEquals(approval, session.retryApproval());
            assertThrows(IllegalArgumentException.class, () -> session.prepared(NOW));
            assertThrows(IllegalArgumentException.class, () -> session.beginApproval(true, NOW));
            session.acceptApproval(bytes(fixture("approveResponse")), NOW);
            assertEquals(RUN_CAP, session.runCapability(NOW));
            assertEquals(NativeSetupSession.State.READY, session.state());
        }
    }

    @Test public void exactUncertainApprovalRecoversAfterSetupExpiryWithoutExtendingRun() throws Exception {
        AtomicLong clock = new AtomicLong();
        try (NativeSetupSession session = session(clock)) {
            session.acceptClaim(bytes(fixture("claimResponse")), NOW);
            byte[] original = session.beginApproval(true, NOW);
            clock.set(TimeUnit.MINUTES.toNanos(11));
            assertArrayEquals(original, session.retryApproval());
            session.acceptApproval(bytes(fixture("approveResponse")), NOW.plusSeconds(660));
            assertEquals(Instant.parse("2026-10-22T10:00:00Z"), session.prepared(NOW.plusSeconds(660)).expiresAt());
            clock.set(TimeUnit.MINUTES.toNanos(720));
            assertThrows(IllegalArgumentException.class, () -> session.prepared(NOW));
        }
        try (NativeSetupSession unapproved = session(new AtomicLong())) {
            unapproved.acceptClaim(bytes(fixture("claimResponse")), NOW);
            assertThrows(IllegalArgumentException.class, () -> unapproved.beginApproval(true, NOW.plusSeconds(601)));
            assertThrows(IllegalArgumentException.class, unapproved::retryApproval);
        }
    }

    @Test public void originalRequestsBoundRollbackAndShorterRunBeforeDelayedRecovery() throws Exception {
        AtomicLong clock = new AtomicLong();
        try (NativeSetupSession session = session(clock)) {
            session.acceptClaim(bytes(fixture("claimResponse")), NOW.minusSeconds(59));
            session.beginApproval(true, NOW.minusSeconds(59));
            Map<String, Object> response = fixture("approveResponse");
            response.put("expiresAt", "2026-10-21T22:30:00.000Z");
            NativeSetupContract.object(response, "preparation").put("expiresAt", response.get("expiresAt"));
            clock.set(TimeUnit.MINUTES.toNanos(20));
            session.acceptApproval(bytes(response), NOW.minusSeconds(30));
            clock.set(TimeUnit.MINUTES.toNanos(30));
            assertThrows(IllegalArgumentException.class, () -> session.prepared(NOW));
        }
        clock.set(0);
        try (NativeSetupSession session = session(clock)) {
            clock.set(TimeUnit.MINUTES.toNanos(10));
            assertThrows(IllegalArgumentException.class,
                    () -> session.acceptClaim(bytes(fixture("claimResponse")), NOW.minusSeconds(30)));
        }
        NativeSetupContract.Claim claim = claim();
        Map<String, Object> lateApproval = fixture("approveResponse");
        lateApproval.put("approvedAt", claim.expiresAt().toString());
        assertThrows(IllegalArgumentException.class,
                () -> NativeSetupContract.approval(bytes(lateApproval), claim, APPROVE, NOW.plusSeconds(600)));
        assertThrows(IllegalArgumentException.class,
                () -> NativeSetupContract.approval(bytes(fixture("approveResponse")), claim, APPROVE, NOW.minusSeconds(61)));
    }

    @Test public void cancellationRejectsLateResponsesAndDropsRetryAuthority() throws Exception {
        NativeSetupSession claiming = session(new AtomicLong()); claiming.close();
        assertThrows(IllegalArgumentException.class, claiming::claimRequest);
        assertThrows(IllegalArgumentException.class, () -> claiming.acceptClaim(bytes(fixture("claimResponse")), NOW));
        NativeSetupSession approving = session(new AtomicLong());
        approving.acceptClaim(bytes(fixture("claimResponse")), NOW); approving.beginApproval(true, NOW); approving.close();
        assertThrows(IllegalArgumentException.class, approving::retryApproval);
        assertThrows(IllegalArgumentException.class, () -> approving.acceptApproval(bytes(fixture("approveResponse")), NOW));
        assertThrows(IllegalArgumentException.class, () -> approving.runCapability(NOW));
    }

    @Test public void malformedApprovalLeavesUncertaintyWithoutGrantingPreparedRun() throws Exception {
        try (NativeSetupSession session = session(new AtomicLong())) {
            session.acceptClaim(bytes(fixture("claimResponse")), NOW);
            byte[] request = session.beginApproval(true, NOW);
            assertThrows(IllegalArgumentException.class, () -> session.acceptApproval("<html>error</html>".getBytes(StandardCharsets.UTF_8), NOW));
            assertEquals(NativeSetupSession.State.APPROVAL_PENDING, session.state());
            assertArrayEquals(request, session.retryApproval());
            assertThrows(IllegalArgumentException.class, () -> session.prepared(NOW));
        }
    }

}
