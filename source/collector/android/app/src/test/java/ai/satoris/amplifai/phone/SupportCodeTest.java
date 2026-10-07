package ai.satoris.amplifai.phone;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public final class SupportCodeTest {
    @Test public void boundedCodeContainsOnlyBuildStageCategoryAndElapsed() {
        SupportCode code = new SupportCode("2026.10.07-rc2", 1_000_000_000L);
        String formatted = code.format("calls", "restricted", 4_000_000_000L);
        assertTrue(formatted.matches("A1\\|2026\\.10\\.07-rc2\\|[0-9a-f]{8}\\|calls\\|restricted\\|3\\|0\\|-"));
        assertFalse(formatted.contains("@"));
    }

    @Test(expected = IllegalArgumentException.class)
    public void rejectsUnboundedProviderText() {
        new SupportCode("2026.10.07-rc2", 0).format("contacts", "private phone 555", 1);
    }
}
