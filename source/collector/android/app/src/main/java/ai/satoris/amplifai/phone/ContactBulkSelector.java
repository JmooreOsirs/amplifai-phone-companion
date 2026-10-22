package ai.satoris.amplifai.phone;

import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CancellationException;
import java.util.function.BooleanSupplier;

/** Builds an exact all-page selection before the owner-visible selection changes. */
final class ContactBulkSelector {
    interface IdPage {
        List<Long> after(long previousId, int size);
    }

    private ContactBulkSelector() {}

    static Set<Long> scan(long expected, int pageSize, IdPage source, BooleanSupplier cancelled) {
        if (expected < 0 || expected > Integer.MAX_VALUE || pageSize < 1)
            throw new IllegalArgumentException("selection_count_outside_protocol");
        Set<Long> complete = new HashSet<>();
        long previous = -1;
        while (complete.size() < expected) {
            if (cancelled.getAsBoolean()) throw new CancellationException("selection_cancelled");
            List<Long> page = source.after(previous, pageSize);
            if (page.isEmpty() || page.size() > pageSize)
                throw new IllegalStateException("selection_incomplete_page");
            for (Long id : page) {
                if (id == null || id < 0 || id <= previous || !complete.add(id) || complete.size() > expected)
                    throw new IllegalStateException("selection_changed_page");
                previous = id;
            }
        }
        if (cancelled.getAsBoolean()) throw new CancellationException("selection_cancelled");
        if (!source.after(previous, 1).isEmpty())
            throw new IllegalStateException("selection_changed_count");
        return complete;
    }
}
