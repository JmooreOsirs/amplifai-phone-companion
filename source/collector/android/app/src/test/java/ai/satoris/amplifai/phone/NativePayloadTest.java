package ai.satoris.amplifai.phone;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
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

    @Test public void threeYearHistoryKeepsAllSelectedDatesAndCountsWithinLocalBound() {
        List<MetadataModel.Contact> contacts = List.of(
                new MetadataModel.Contact(1, "Selected synthetic", List.of("+15550100200")),
                new MetadataModel.Contact(2, "Unselected synthetic", List.of("+15550100300")));
        List<MetadataModel.Interaction> calls = new ArrayList<>();
        List<MetadataModel.Interaction> messages = new ArrayList<>();
        Instant base = Instant.parse("2023-01-01T00:00:00Z");
        for (int index = 0; index < 20_000; index++) {
            long date = base.plus(index % 1_200, ChronoUnit.DAYS).toEpochMilli();
            String number = index % 5 == 0 ? "+15550100300" : "+15550100200";
            calls.add(new MetadataModel.Interaction(index + 1, "call", number, date, "incoming", 42L));
            messages.add(new MetadataModel.Interaction(index + 1, "message", number, date, "outgoing", null));
        }
        long started = System.nanoTime();
        byte[] payload = NativePayload.selected(contacts, calls, messages, Set.of(1L),
                true, true, Instant.parse("2026-09-28T00:00:00Z"));
        String json = new String(payload, StandardCharsets.UTF_8);
        assertEquals(16_000, occurrences(json, "\"kind\":\"call\""));
        assertEquals(16_000, occurrences(json, "\"kind\":\"message\""));
        assertTrue(json.contains("2023-01-02T00:00:00Z"));
        assertTrue(json.contains("2026-04-14T00:00:00Z"));
        assertFalse(json.contains("Unselected synthetic"));
        assertTrue(payload.length < 32 * 1024 * 1024);
        System.out.println("synthetic Android history: 40,000 rows, 32,000 selected, " +
                payload.length + " handoff bytes, " +
                (System.nanoTime() - started) / 1_000_000 + "ms serialization");
    }

    private static int occurrences(String value, String needle) {
        int count = 0;
        for (int position = 0; (position = value.indexOf(needle, position)) >= 0; position += needle.length()) count++;
        return count;
    }
}
