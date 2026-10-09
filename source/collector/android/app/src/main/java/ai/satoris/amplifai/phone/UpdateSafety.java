package ai.satoris.amplifai.phone;

/** An update may replace the process only after all private work has a terminal disposition. */
final class UpdateSafety {
    record State(boolean started, boolean disclosure, boolean permission, boolean reading,
                 boolean preparingTransfer, boolean activeHandoff, boolean localSourceHeld,
                 boolean savedAcknowledgmentForCurrentReview) {}

    private UpdateSafety() {}

    static boolean canInstall(State state) {
        return state.started && !state.disclosure && !state.permission && !state.reading &&
                !state.preparingTransfer && !state.activeHandoff &&
                (!state.localSourceHeld || state.savedAcknowledgmentForCurrentReview);
    }
}
