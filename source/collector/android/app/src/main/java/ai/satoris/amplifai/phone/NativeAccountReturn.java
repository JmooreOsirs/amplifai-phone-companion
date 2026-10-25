package ai.satoris.amplifai.phone;

/** Non-secret browser context only. Never contains a capability or grants collection/saving. */
record NativeAccountReturn(String runId, String enrollmentId, String completedHandoffId, String completedPayloadSha256) {
    NativeAccountReturn {
        NativeSetupContract.uuid(runId);
        NativeSetupContract.uuid(enrollmentId);
        NativeSetupContract.require((completedHandoffId == null) == (completedPayloadSha256 == null));
        if (completedHandoffId != null) {
            NativeSetupContract.uuid(completedHandoffId);
            NativeSetupContract.require(completedPayloadSha256.matches("[0-9a-f]{64}"));
        }
    }

    NativeAccountReturn completed(String handoffId, String payloadSha256) {
        return new NativeAccountReturn(runId, enrollmentId, handoffId, payloadSha256);
    }

    String href(boolean reviewedHandoffActive) {
        if (completedHandoffId != null) return account(enrollmentId) + "&completedHandoffId=" + completedHandoffId +
                "&completedPayloadSha256=" + completedPayloadSha256;
        return LocalBridge.ACCOUNT_ORIGIN + "/phone/account?" +
                (reviewedHandoffActive ? "continue=reviewed&phoneRunId=" + runId : "phoneRunControl=" + runId) +
                "&enrollmentId=" + enrollmentId + "&week=phone";
    }

    static String account(String enrollmentId) {
        return LocalBridge.ACCOUNT_ORIGIN + "/phone/account?enrollmentId=" + NativeSetupContract.uuid(enrollmentId) + "&week=phone";
    }
}
