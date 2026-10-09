package ai.satoris.amplifai.phone;

import org.junit.Test;

import static org.junit.Assert.*;

public final class UpdateSafetyTest {
    @Test public void onlyIdleOrCurrentSavedAcknowledgmentMayInstall() {
        assertTrue(UpdateSafety.canInstall(state(true, false, false, false, false, false, false, false)));
        assertTrue(UpdateSafety.canInstall(state(true, false, false, false, false, false, true, true)));
        assertFalse(UpdateSafety.canInstall(state(true, false, false, false, false, false, true, false)));
        assertFalse(UpdateSafety.canInstall(state(true, false, false, false, false, true, true, true)));
        assertFalse(UpdateSafety.canInstall(state(true, false, false, true, false, false, false, false)));
        assertFalse(UpdateSafety.canInstall(state(true, true, false, false, false, false, false, false)));
        assertFalse(UpdateSafety.canInstall(state(true, false, true, false, false, false, false, false)));
        assertFalse(UpdateSafety.canInstall(state(true, false, false, false, true, false, false, false)));
        assertFalse(UpdateSafety.canInstall(state(false, false, false, false, false, false, false, false)));
    }

    private static UpdateSafety.State state(boolean started, boolean disclosure, boolean permission,
            boolean reading, boolean preparing, boolean handoff, boolean held, boolean saved) {
        return new UpdateSafety.State(started, disclosure, permission, reading, preparing, handoff, held, saved);
    }
}
