package ai.satoris.amplifai.phone;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;

public final class MetadataModelTest {
    @Test public void reviewIncludesOnlySelectedExactPhoneMatches() {
        MetadataModel.Contact selected = new MetadataModel.Contact(1, "Synthetic One",
                Collections.singletonList("+15550000001"));
        MetadataModel.Contact other = new MetadataModel.Contact(2, "Synthetic Two",
                Collections.singletonList("+15550000002"));
        MetadataModel.Interaction matchedCall = new MetadataModel.Interaction(10, "call", "+15550000001",
                1_700_000_000_000L, "incoming", 45L);
        MetadataModel.Interaction unrelatedCall = new MetadataModel.Interaction(11, "call", "+15550000002",
                1_710_000_000_000L, "outgoing", 2L);
        MetadataModel.Interaction matchedSms = new MetadataModel.Interaction(12, "message", "+15550000001",
                1_705_000_000_000L, "outgoing", null);
        MetadataModel.Review review = MetadataModel.review(Arrays.asList(selected, other),
                Arrays.asList(matchedCall, unrelatedCall), Collections.singletonList(matchedSms),
                new HashSet<>(Collections.singleton(1L)));
        assertEquals(1, review.selectedContacts);
        assertEquals(1, review.matchedCalls);
        assertEquals(1, review.matchedMessages);
        assertEquals(1_700_000_000_000L, review.earliestMs);
        assertEquals(1_705_000_000_000L, review.latestMs);
        assertTrue(selected.phones.contains("+15550000001"));
    }

    @Test public void noSelectionMeansNoObservedRange() {
        MetadataModel.Review review = MetadataModel.review(Collections.emptyList(),
                Collections.emptyList(), Collections.emptyList(), Collections.emptySet());
        assertEquals(0, review.selectedContacts);
        assertEquals(0, review.earliestMs);
        assertEquals(0, review.latestMs);
    }
}
