package ai.satoris.amplifai.phone;

import java.io.File;
import java.io.IOException;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;

/** Per-Activity review file ownership; a delayed old close cannot remove a replacement's database. */
final class ReviewStoreFiles implements AutoCloseable {
    private static final Set<File> LIVE = new HashSet<>();
    private static final String LEGACY = "phone-review.sqlite";
    private static final String[] SUFFIXES = {"", "-wal", "-shm", "-journal"};
    private final File file;
    private boolean closed;

    private ReviewStoreFiles(File file) { this.file = file; }
    File file() { return file; }

    static synchronized ReviewStoreFiles acquire(File directory) throws IOException {
        File[] existing = directory.listFiles();
        if (existing == null) throw new IOException("private_storage_unavailable");
        for (File candidate : existing) {
            String name = candidate.getName();
            if (!name.matches("phone-review(?:-[0-9a-f-]{36})?\\.sqlite(?:-wal|-shm|-journal)?")) continue;
            boolean live = false;
            for (File owned : LIVE) {
                for (String suffix : SUFFIXES) if (candidate.equals(new File(owned.getPath() + suffix))) live = true;
            }
            if (!live && candidate.exists() && !candidate.delete()) throw new IOException("private_storage_cleanup_failed");
        }
        File file = new File(directory, LEGACY.replace(".sqlite", "-" + UUID.randomUUID() + ".sqlite"));
        if (!file.createNewFile()) throw new IOException("private_storage_unavailable");
        LIVE.add(file);
        return new ReviewStoreFiles(file);
    }

    @Override public void close() throws IOException {
        synchronized (ReviewStoreFiles.class) {
            if (closed) return;
            try {
                for (String suffix : SUFFIXES) {
                    File target = new File(file.getPath() + suffix);
                    if (target.exists() && !target.delete()) throw new IOException("private_storage_cleanup_failed");
                }
                closed = true;
            } finally {
                // Failed cleanup becomes an orphan that the next acquire must remove or fail closed on.
                LIVE.remove(file);
            }
        }
    }
}
