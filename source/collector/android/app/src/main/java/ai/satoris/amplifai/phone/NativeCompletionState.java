package ai.satoris.amplifai.phone;

/** Process-local presentation only. Never retains live authority or restores a collection. */
final class NativeCompletionState {
    static final NativeCompletionState PROCESS = new NativeCompletionState();
    private long attempt;
    private NativeAccountReturn completed;

    synchronized long beginAttempt() {
        completed = null;
        return ++attempt;
    }

    synchronized void finished(long observedAttempt, LocalBridge.EndReason reason, boolean cleanupSucceeded,
                               NativeAccountReturn target) {
        if (observedAttempt != attempt) return;
        completed = null;
        if (reason == LocalBridge.EndReason.COMPLETED && cleanupSucceeded && target != null &&
                target.completedHandoffId() != null) completed = target;
    }

    synchronized NativeAccountReturn recover(NativeAccountReturn expected, boolean newSetupOrActiveHandoff) {
        if (newSetupOrActiveHandoff || completed == null) return null;
        if (expected != null && (!expected.runId().equals(completed.runId()) ||
                !expected.enrollmentId().equals(completed.enrollmentId()))) return null;
        if (expected != null && expected.completedHandoffId() != null && !expected.equals(completed)) return null;
        return completed;
    }
}
