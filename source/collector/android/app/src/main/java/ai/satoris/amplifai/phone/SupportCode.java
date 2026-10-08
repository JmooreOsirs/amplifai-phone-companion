package ai.satoris.amplifai.phone;

import java.util.Locale;
import java.util.UUID;

/** A bounded, metadata-free code the owner can choose to share with support. */
final class SupportCode {
    private final String build;
    private final String reference;
    private final long startedNanos;

    SupportCode(String build, long startedNanos) {
        this.build = build.matches("[0-9]{4}\\.[0-9]{2}\\.[0-9]{2}-rc[0-9]+") ? build : "unknown";
        this.reference = UUID.randomUUID().toString().replace("-", "").substring(0, 8).toUpperCase(Locale.ROOT);
        this.startedNanos = startedNanos;
    }

    String format(String stage, String category, long nowNanos) {
        if (!stage.matches("contacts|calls|sms|handoff|notification|permission") ||
                !category.matches("denied|restricted|cancelled|limit|provider|unavailable|revoked|invalid")) {
            throw new IllegalArgumentException("unsupported_support_category");
        }
        long elapsed = Math.max(0, (nowNanos - startedNanos) / 1_000_000_000L);
        // A1 has nine fields. A dotted version-name build unambiguously denotes Android;
        // the existing numeric Mac builds retain their original A1 wire format.
        return String.format(Locale.ROOT, "A1|%s|%s|%s|%s|%d|0|0|-", build, reference,
                stage, category, elapsed);
    }
}
