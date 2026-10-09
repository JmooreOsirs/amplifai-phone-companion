package ai.satoris.amplifai.phone;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

import org.junit.Test;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CancellationException;
import java.util.concurrent.atomic.AtomicInteger;

public final class ContactBulkSelectorTest {
    private static ContactBulkSelector.IdPage pages(List<Long> ids) {
        return (after, size) -> {
            List<Long> page = new ArrayList<>();
            for (Long id : ids) if (id > after && page.size() < size) page.add(id);
            return page;
        };
    }

    @Test public void selectsEveryStableIdBeyondVisibleReviewPages() {
        List<Long> ids = new ArrayList<>();
        for (long id = 1; id <= 1_205; id++) ids.add(id * 2);
        Set<Long> selected = ContactBulkSelector.scan(ids.size(), 100, pages(ids), () -> false);
        assertEquals(1_205, selected.size());
        assertEquals(true, selected.contains(2_410L));
    }

    @Test public void refusesMissingReorderedAndNewlyAppendedIds() {
        for (ContactBulkSelector.IdPage invalid : Arrays.asList(
                pages(Arrays.asList(1L, 2L)),
                (after, size) -> after < 0 ? Arrays.asList(1L, 2L) : Arrays.asList(2L, 3L),
                pages(Arrays.asList(1L, 2L, 3L, 4L)))) {
            try {
                ContactBulkSelector.scan(3, 2, invalid, () -> false);
                fail("Changed or incomplete page must not become a selection");
            } catch (IllegalStateException expected) {
                // The caller retains the prior selected IDs.
            }
        }
    }

    @Test public void cancelBeforeCommitPreservesCallerOwnedSelection() {
        AtomicInteger pages = new AtomicInteger();
        try {
            ContactBulkSelector.scan(300, 100, (after, size) -> {
                pages.incrementAndGet();
                List<Long> result = new ArrayList<>();
                for (long id = after + 1; id <= after + 100; id++) result.add(id);
                return result;
            }, () -> pages.get() > 0);
            fail("Cancelled scan must not commit");
        } catch (CancellationException expected) {
            assertEquals(1, pages.get());
        }
    }
}
