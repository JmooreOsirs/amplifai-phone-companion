package ai.satoris.amplifai.phone;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.Set;

public final class NativePayloadTest {
    @Test public void readableEmptyHistoryIsNotAnUnavailableSource() {
        String json = new String(NativePayload.selected(
                List.of(new MetadataModel.Contact(1, "Synthetic", List.of("+15550100200"))),
                List.of(), List.of(), Set.of(1L), true, true, Instant.parse("2026-09-25T12:00:00Z")),
                StandardCharsets.UTF_8);
        assertTrue(json.contains("\"missingSources\":[]"));
        assertTrue(json.contains("\"interactions\":[]"));
    }

    @Test public void unavailableSourcesCannotExportStaleInteractions() {
        String json = new String(NativePayload.selected(
                List.of(new MetadataModel.Contact(1, "Synthetic", List.of("+15550100200"))),
                List.of(new MetadataModel.Interaction(2, "call", "+15550100200", 1_700_000_000_000L, "incoming", 1L)),
                List.of(new MetadataModel.Interaction(3, "message", "+15550100200", 1_700_000_000_000L, "incoming", null)),
                Set.of(1L), false, false, Instant.parse("2026-09-25T12:00:00Z")), StandardCharsets.UTF_8);
        assertTrue(json.contains("\"missingSources\":[\"calls\",\"messages\"]"));
        assertTrue(json.contains("\"interactions\":[]"));
    }

    @Test public void selectedHandoffContainsOnlyMatchingMetadataAndMissingSources() {
        byte[] payload = NativePayload.selected(
                List.of(new MetadataModel.Contact(1, "Ada \"Synthetic\"", List.of("+15550100200")),
                        new MetadataModel.Contact(2, "Unselected", List.of("+15550100300"))),
                List.of(new MetadataModel.Interaction(3, "call", "+15550100200", 1_700_000_000_000L, "incoming", 22L),
                        new MetadataModel.Interaction(4, "call", "+15550100300", 1_700_000_000_000L, "outgoing", 11L)),
                List.of(), Set.of(1L), true, false, Instant.parse("2026-09-25T12:00:00Z"));
        String json = new String(payload, StandardCharsets.UTF_8);
        assertTrue(json.contains("\"platform\":\"android\""));
        assertTrue(json.contains("\"missingSources\":[\"messages\"]"));
        assertTrue(json.contains("Ada \\\"Synthetic\\\""));
        assertTrue(json.contains("\"sourceId\":3"));
        assertFalse(json.contains("Unselected"));
        assertFalse(json.contains("\"sourceId\":4"));
        assertFalse(json.contains("body"));
    }
}
