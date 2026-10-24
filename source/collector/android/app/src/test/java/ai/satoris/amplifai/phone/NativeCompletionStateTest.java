package ai.satoris.amplifai.phone;

import org.junit.Test;
import static org.junit.Assert.*;

public final class NativeCompletionStateTest {
    private static final String RUN = "88888888-8888-4888-8888-888888888888";
    private static final String ENROLLMENT = "66666666-6666-4666-8666-666666666666";
    private static final String HANDOFF = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
    private static final NativeAccountReturn PENDING = new NativeAccountReturn(RUN, ENROLLMENT, null, null);
    private static final NativeAccountReturn SAVED = PENDING.completed(HANDOFF, "a".repeat(64));

    @Test public void completedDisplaySurvivesMissingObserverWithoutRestoringAuthority() {
        NativeCompletionState state = new NativeCompletionState();
        long attempt = state.beginAttempt();
        assertNull(state.recover(PENDING, false));
        // The service completes while the Activity is unbound, then a new service/Activity
        // asks for presentation state. No NativeRunAuthority or source binding is retained.
        state.finished(attempt, LocalBridge.EndReason.COMPLETED, true, SAVED);
        assertEquals(SAVED, state.recover(PENDING, false));
        assertEquals(SAVED, state.recover(null, false));
        assertEquals(SAVED.href(false), state.recover(SAVED, false).href(false));
        assertFalse(state.recover(PENDING, false).href(false).contains("continue=reviewed"));
        assertFalse(state.recover(PENDING, false).href(false).contains("capability"));
    }

    @Test public void onlyCompletedAfterSuccessfulCleanupCanRecordConfirmation() {
        NativeCompletionState state = new NativeCompletionState();
        for (LocalBridge.EndReason reason : LocalBridge.EndReason.values()) {
            if (reason == LocalBridge.EndReason.COMPLETED) continue;
            state.finished(state.beginAttempt(), reason, true, SAVED);
            assertNull(reason.name(), state.recover(PENDING, false));
        }
        state.finished(state.beginAttempt(), LocalBridge.EndReason.COMPLETED, false, SAVED);
        assertNull(state.recover(PENDING, false));
        state.finished(state.beginAttempt(), LocalBridge.EndReason.COMPLETED, true, null);
        assertNull(state.recover(PENDING, false));
        state.finished(state.beginAttempt(), LocalBridge.EndReason.COMPLETED, true, PENDING);
        assertNull(state.recover(PENDING, false));
    }

    @Test public void newSetupAndMismatchedRunCourseOrCompletionCannotAdoptOldSuccess() {
        NativeCompletionState state = new NativeCompletionState();
        state.finished(state.beginAttempt(), LocalBridge.EndReason.COMPLETED, true, SAVED);
        assertNull(state.recover(PENDING, true));
        assertNull(state.recover(null, true));
        assertNull(state.recover(new NativeAccountReturn("99999999-9999-4999-8999-999999999999", ENROLLMENT, null, null), false));
        assertNull(state.recover(new NativeAccountReturn(RUN, "77777777-7777-4777-8777-777777777777", null, null), false));
        assertNull(state.recover(PENDING.completed("cccccccc-cccc-4ccc-8ccc-cccccccccccc", "a".repeat(64)), false));
        assertNull(state.recover(PENDING.completed(HANDOFF, "b".repeat(64)), false));
        assertEquals(SAVED, state.recover(PENDING, false));
    }

    @Test public void newAttemptClearsPriorSuccessAndRejectsLateOldCallbacks() {
        NativeCompletionState state = new NativeCompletionState();
        long first = state.beginAttempt();
        state.finished(first, LocalBridge.EndReason.COMPLETED, true, SAVED);
        long next = state.beginAttempt();
        assertNull(state.recover(null, false));
        state.finished(first, LocalBridge.EndReason.COMPLETED, true, SAVED);
        assertNull(state.recover(null, false));
        state.finished(next, LocalBridge.EndReason.CANCELLED, true, SAVED);
        assertNull(state.recover(null, false));
        state.finished(first, LocalBridge.EndReason.COMPLETED, true, SAVED);
        assertNull(state.recover(null, false));
    }

    @Test public void processRestartHasNoPersistedSuccessOrCollectionGrant() {
        NativeCompletionState oldProcess = new NativeCompletionState();
        oldProcess.finished(oldProcess.beginAttempt(), LocalBridge.EndReason.COMPLETED, true, SAVED);
        NativeCompletionState freshProcess = new NativeCompletionState();
        assertNull(freshProcess.recover(PENDING, false));
        assertNull(freshProcess.recover(null, false));
    }
}
