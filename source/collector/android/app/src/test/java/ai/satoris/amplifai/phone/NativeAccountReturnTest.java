package ai.satoris.amplifai.phone;

import org.junit.Test;
import java.net.URI;
import static org.junit.Assert.*;

public final class NativeAccountReturnTest {
    private static final String RUN = "88888888-8888-4888-8888-888888888888";
    private static final String ENROLLMENT = "66666666-6666-4666-8666-666666666666";
    private static final String HANDOFF = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

    @Test public void controlsAndSavedRecoveryCannotRequestReviewedContinuation() {
        NativeAccountReturn target = new NativeAccountReturn(RUN, ENROLLMENT, null, null);
        assertEquals("phoneRunControl=" + RUN + "&enrollmentId=" + ENROLLMENT + "&week=phone", URI.create(target.href(false)).getRawQuery());
        assertEquals("continue=reviewed&phoneRunId=" + RUN + "&enrollmentId=" + ENROLLMENT + "&week=phone", URI.create(target.href(true)).getRawQuery());
        NativeAccountReturn saved = target.completed(HANDOFF, "a".repeat(64));
        for (boolean active : new boolean[]{true, false}) {
            URI uri = URI.create(saved.href(active));
            assertEquals(LocalBridge.ACCOUNT_ORIGIN, uri.getScheme() + "://" + uri.getRawAuthority());
            assertEquals("/phone/account", uri.getPath());
            assertEquals("enrollmentId=" + ENROLLMENT + "&week=phone&completedHandoffId=" + HANDOFF +
                    "&completedPayloadSha256=" + "a".repeat(64), uri.getRawQuery());
            assertFalse(uri.getRawQuery().contains("continue=")); assertFalse(uri.getRawQuery().contains("capability"));
        }
    }

    @Test public void reviewedReturnIdentifiesTheExactRunWithoutSecretsOrRetryChurn() {
        NativeAccountReturn first = new NativeAccountReturn(RUN, ENROLLMENT, null, null);
        NativeAccountReturn next = new NativeAccountReturn("99999999-9999-4999-8999-999999999999", ENROLLMENT, null, null);
        // A new collection in the same account must not reopen the previous run's identical browser URL.
        assertNotEquals(first.href(true), next.href(true));
        assertEquals(first.href(true), first.href(true));
        URI uri = URI.create(first.href(true));
        assertEquals(LocalBridge.ACCOUNT_ORIGIN, uri.getScheme() + "://" + uri.getRawAuthority());
        assertEquals("/phone/account", uri.getPath());
        assertNull(uri.getFragment());
        // This is the website's pending-return route. All values are non-secret routing context;
        // the website must still authorize the run before it discovers or saves a phone selection.
        assertArrayEquals(new String[]{"continue=reviewed", "phoneRunId=" + RUN,
                "enrollmentId=" + ENROLLMENT, "week=phone"}, uri.getRawQuery().split("&"));
        assertFalse(uri.getRawQuery().contains("phoneRunControl="));
        assertFalse(uri.getRawQuery().contains("completedHandoffId="));
    }

    @Test public void partialCompletionAndUntrustedRoutingFieldsAreRejected() {
        assertThrows(IllegalArgumentException.class, () -> new NativeAccountReturn(RUN, ENROLLMENT, HANDOFF, null));
        assertThrows(IllegalArgumentException.class, () -> new NativeAccountReturn(RUN, ENROLLMENT, null, "a".repeat(64)));
        assertThrows(IllegalArgumentException.class, () -> new NativeAccountReturn(RUN, ENROLLMENT, HANDOFF, "a".repeat(63)));
        assertThrows(IllegalArgumentException.class, () -> NativeAccountReturn.account(ENROLLMENT + "&continue=reviewed"));
        assertThrows(IllegalArgumentException.class, () -> new NativeAccountReturn("https://other.example", ENROLLMENT, null, null));
    }
}
