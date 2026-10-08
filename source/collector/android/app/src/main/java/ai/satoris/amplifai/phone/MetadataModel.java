package ai.satoris.amplifai.phone;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/** In-memory metadata only. No SMS body, backup, or attachment field exists in this model. */
public final class MetadataModel {
    private MetadataModel() {}

    public static final class Contact {
        public final long sourceId;
        public final String name;
        public final List<String> phones;

        public Contact(long sourceId, String name, List<String> phones) {
            this.sourceId = sourceId;
            this.name = name == null ? "" : name;
            this.phones = Collections.unmodifiableList(new ArrayList<>(phones));
        }
    }

    public static final class Interaction {
        public final long sourceId;
        public final String kind;
        public final String counterpart;
        public final long occurredAtMs;
        public final String direction;
        public final Long durationSeconds;

        public Interaction(long sourceId, String kind, String counterpart, long occurredAtMs,
                           String direction, Long durationSeconds) {
            this.sourceId = sourceId;
            this.kind = kind;
            this.counterpart = counterpart;
            this.occurredAtMs = occurredAtMs;
            this.direction = direction;
            this.durationSeconds = durationSeconds;
        }
    }

    public static final class Source<T> {
        public final List<T> records;
        public final int rejected;

        public Source(List<T> records, int rejected, boolean truncated) {
            if (truncated) throw new SourceLimitExceededException();
            this.records = Collections.unmodifiableList(new ArrayList<>(records));
            this.rejected = rejected;
        }
    }

    public static final class SourceLimitExceededException extends IllegalStateException {
        SourceLimitExceededException() { super("Provider scan limit reached before the source was fully read."); }
    }

    static void addContactPhone(LinkedHashSet<String> phones, String value) {
        if (!phones.contains(value) && phones.size() >= 20) {
            throw new SourceLimitExceededException();
        }
        phones.add(value);
    }

    public static final class Review {
        public final int selectedContacts;
        public final int matchedCalls;
        public final int matchedMessages;
        public final long earliestMs;
        public final long latestMs;

        Review(int selectedContacts, int matchedCalls, int matchedMessages, long earliestMs, long latestMs) {
            this.selectedContacts = selectedContacts;
            this.matchedCalls = matchedCalls;
            this.matchedMessages = matchedMessages;
            this.earliestMs = earliestMs;
            this.latestMs = latestMs;
        }
    }

    public static Review review(List<Contact> contacts, List<Interaction> calls,
                                List<Interaction> messages, Set<Long> selectedIds) {
        Set<String> selectedPhones = new HashSet<>();
        int selectedCount = 0;
        for (Contact contact : contacts) {
            if (selectedIds.contains(contact.sourceId)) {
                selectedCount++;
                selectedPhones.addAll(contact.phones);
            }
        }
        int callCount = 0;
        int messageCount = 0;
        long earliest = Long.MAX_VALUE;
        long latest = Long.MIN_VALUE;
        for (Interaction item : calls) {
            if (!selectedPhones.contains(item.counterpart)) continue;
            callCount++;
            earliest = Math.min(earliest, item.occurredAtMs);
            latest = Math.max(latest, item.occurredAtMs);
        }
        for (Interaction item : messages) {
            if (!selectedPhones.contains(item.counterpart)) continue;
            messageCount++;
            earliest = Math.min(earliest, item.occurredAtMs);
            latest = Math.max(latest, item.occurredAtMs);
        }
        return new Review(selectedCount, callCount, messageCount,
                earliest == Long.MAX_VALUE ? 0 : earliest,
                latest == Long.MIN_VALUE ? 0 : latest);
    }
}
