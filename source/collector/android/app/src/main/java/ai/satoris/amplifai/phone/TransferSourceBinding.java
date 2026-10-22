package ai.satoris.amplifai.phone;

import java.util.function.IntPredicate;

/** In-memory sources of one explicitly approved frozen transfer, never an account Save approval. */
record TransferSourceBinding(String sessionId, int capturedSources) {
    static final int CONTACTS = 1;
    static final int CALLS = 2;
    static final int MESSAGES = 4;
    private static final int ALL = CONTACTS | CALLS | MESSAGES;

    TransferSourceBinding {
        if (sessionId == null || sessionId.isEmpty() || (capturedSources & CONTACTS) == 0 ||
                (capturedSources & ~ALL) != 0) throw new IllegalArgumentException("invalid_transfer_sources");
    }

    boolean includes(int source) { return (capturedSources & source) != 0; }

    static boolean permitted(TransferSourceBinding binding, String currentSession, IntPredicate granted) {
        if (binding == null || !binding.sessionId.equals(currentSession)) return false;
        for (int source : new int[]{CONTACTS, CALLS, MESSAGES}) {
            if (binding.includes(source) && !granted.test(source)) return false;
        }
        return true;
    }
}
