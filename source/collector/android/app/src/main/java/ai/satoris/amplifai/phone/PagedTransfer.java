package ai.satoris.amplifai.phone;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import java.util.function.Consumer;

/** Immutable selected-record pages; a browser never queries the live provider or mutable review store. */
final class PagedTransfer implements LocalBridge.PagedSource {
    static final int PAGE_MAX_BYTES = 128 * 1024;
    static final int PAGE_MAX_ROWS = 100;
    static final String EMPTY_CHAIN = "0".repeat(64);
    private final File file;
    private final String manifestJson;
    private final String manifestSha256;
    private final int[] pageCounts = new int[3];

    record BuildProgress(String category, long rows, int pages) {}

    PagedTransfer(Context context, SanitizedStore source, Set<Long> selectedIds) {
        this(context, source, selectedIds, ignored -> {});
    }

    PagedTransfer(Context context, SanitizedStore source, Set<Long> selectedIds,
                  Consumer<BuildProgress> progress) {
        if (selectedIds.isEmpty() || !source.coverage("contacts").available) throw new IllegalArgumentException("unreviewed_selection");
        file = new File(context.getNoBackupFilesDir(), "phone-transfer.sqlite");
        SanitizedStore.deleteFiles(file);
        String built;
        try (SQLiteDatabase db = SQLiteDatabase.openOrCreateDatabase(file, null)) {
            db.execSQL("CREATE TABLE page (category TEXT NOT NULL, cursor INTEGER NOT NULL, chunk TEXT NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(category,cursor))");
            db.beginTransaction();
            try {
                source.select(selectedIds);
                List<String> samples = new ArrayList<>();
                Source[] summaries = new Source[3];
                String[] categories = {"contacts", "calls", "messages"};
                for (int index = 0; index < categories.length; index++) {
                    String category = categories[index];
                    SanitizedStore.Coverage coverage = source.coverage(category);
                    summaries[index] = write(db, source, category, coverage.available, samples, progress);
                    // Contacts are emitted once per contact, while the provider cursor yields phone rows.
                    summaries[index].seen = category.equals("contacts") ? source.count("contacts") : coverage.seen;
                    summaries[index].providerSeen = category.equals("contacts") ? coverage.seen : -1;
                    if (summaries[index].seen < summaries[index].included)
                        throw new IllegalArgumentException("source_coverage_inconsistent");
                    pageCounts[index] = summaries[index].pages;
                }
                if (summaries[0].included == 0) throw new IllegalArgumentException("empty_selected_contacts");
                built = manifest(summaries, samples, source);
                if (built.length() > 8192) throw new IllegalArgumentException("manifest_outside_protocol");
                db.setTransactionSuccessful();
            } finally { db.endTransaction(); }
        } catch (RuntimeException failure) {
            SanitizedStore.deleteFiles(file);
            throw failure;
        }
        manifestJson = built;
        manifestSha256 = sha256(built);
    }

    @Override public String manifestJson() { return manifestJson; }
    @Override public String manifestSha256() { return manifestSha256; }
    @Override public int pageCount(String category) { return pageCounts[index(category)]; }

    static final class Page {
        final String category; final int cursor; final String chunk; final String sha256;
        final Integer nextCursor;
        Page(String category, int cursor, String chunk, String sha256, Integer nextCursor) {
            this.category = category; this.cursor = cursor; this.chunk = chunk;
            this.sha256 = sha256; this.nextCursor = nextCursor;
        }
        String json() {
            StringBuilder result = new StringBuilder(chunk.length() + 190);
            result.append("{\"format\":\"paged-records-v2\",\"category\":"); NativePayload.quote(result, category);
            result.append(",\"cursor\":").append(cursor).append(",\"chunk\":"); NativePayload.quote(result, chunk);
            result.append(",\"pageSha256\":\"").append(sha256).append("\",\"nextCursor\":");
            result.append(nextCursor == null ? "null" : nextCursor).append('}');
            return result.toString();
        }
    }

    Page page(String category, int cursor) {
        int pages = pageCount(category);
        if (cursor < 0 || cursor >= pages) return null;
        try (SQLiteDatabase db = SQLiteDatabase.openDatabase(file.getPath(), null, SQLiteDatabase.OPEN_READONLY);
             Cursor row = db.rawQuery("SELECT chunk,sha256 FROM page WHERE category=? AND cursor=?",
                     new String[]{category, String.valueOf(cursor)})) {
            if (!row.moveToFirst()) return null;
            return new Page(category, cursor, row.getString(0), row.getString(1),
                    cursor + 1 < pages ? cursor + 1 : null);
        }
    }

    @Override public String pageJson(String category, int cursor) {
        Page value = page(category, cursor);
        return value == null ? null : value.json();
    }

    private static final class Source {
        long seen; long providerSeen = -1; final long included; final int pages; final String chain;
        Source(long included, int pages, String chain) { this.included = included; this.pages = pages; this.chain = chain; }
    }

    private static Source write(SQLiteDatabase db, SanitizedStore source, String category,
                                boolean available, List<String> samples, Consumer<BuildProgress> progress) {
        if (!available) return new Source(0, 0, EMPTY_CHAIN);
        StringBuilder batch = new StringBuilder("[");
        int rows = 0, pages = 0; long included = 0;
        String chain = EMPTY_CHAIN;
        try (Cursor cursor = source.selectedRows(category)) {
            while (cursor.moveToNext()) {
                String record = category.equals("contacts") ? contact(source, cursor, samples) : interaction(category, cursor);
                int recordBytes = record.getBytes(StandardCharsets.US_ASCII).length;
                if (recordBytes + 2 > PAGE_MAX_BYTES) throw new IllegalArgumentException("record_outside_protocol");
                if (rows > 0 && (rows >= PAGE_MAX_ROWS || batch.length() + recordBytes + 2 > PAGE_MAX_BYTES)) {
                    chain = flush(db, category, pages++, batch, chain);
                    if (pages % 25 == 0) progress.accept(new BuildProgress(category, included, pages));
                    batch = new StringBuilder("["); rows = 0;
                }
                if (rows > 0) batch.append(',');
                batch.append(record); rows++; included++;
                if (included > Integer.MAX_VALUE) throw new IllegalArgumentException("source_count_outside_protocol");
            }
        }
        if (rows > 0) chain = flush(db, category, pages++, batch, chain);
        progress.accept(new BuildProgress(category, included, pages));
        return new Source(included, pages, chain);
    }

    private static String flush(SQLiteDatabase db, String category, int cursor, StringBuilder batch, String chain) {
        batch.append(']');
        String chunk = batch.toString();
        String digest = sha256(chunk);
        ContentValues values = new ContentValues(); values.put("category", category);
        values.put("cursor", cursor); values.put("chunk", chunk); values.put("sha256", digest);
        db.insertOrThrow("page", null, values);
        return sha256(chain + digest);
    }

    private static String contact(SanitizedStore store, Cursor row, List<String> samples) {
        String name = row.getString(1);
        if (samples.size() < 3 && name.length() <= 240) samples.add(name.isEmpty() ? "Unnamed contact" : name);
        StringBuilder result = new StringBuilder("{\"sourceId\":").append(row.getLong(0)).append(",\"name\":");
        NativePayload.quote(result, name);
        result.append(",\"phones\":[");
        List<String> phones = store.phones(row.getLong(0));
        for (int index = 0; index < phones.size(); index++) {
            if (index > 0) result.append(','); NativePayload.quote(result, phones.get(index));
        }
        return result.append("]}").toString();
    }

    private static String interaction(String category, Cursor row) {
        StringBuilder result = new StringBuilder("{\"sourceId\":").append(row.getLong(0));
        result.append(",\"kind\":\"").append(category.equals("calls") ? "call" : "message").append("\",\"transport\":\"")
                .append(category.equals("calls") ? "call" : "sms").append("\",\"occurredAt\":");
        NativePayload.quote(result, Instant.ofEpochMilli(row.getLong(2)).toString());
        result.append(",\"direction\":"); NativePayload.quote(result, row.getString(3));
        result.append(",\"participants\":["); NativePayload.quote(result, row.getString(1));
        result.append("],\"durationSeconds\":").append(row.isNull(4) ? "null" : row.getLong(4)).append('}');
        return result.toString();
    }

    private static String manifest(Source[] sources, List<String> samples, SanitizedStore store) {
        StringBuilder result = new StringBuilder("{\"schema\":2,\"platform\":\"android\",\"since\":\"1970-01-01T00:00:00Z\",\"collectedAt\":");
        NativePayload.quote(result, Instant.now().toString());
        result.append(",\"missingSources\":[");
        String[] categories = {"contacts", "calls", "messages"};
        boolean first = true;
        for (String category : categories) {
            if (store.coverage(category).available) continue;
            if (!first) result.append(','); first = false;
            NativePayload.quote(result, category);
        }
        result.append("],\"sampleContactNames\":[");
        for (int index = 0; index < samples.size(); index++) {
            if (index > 0) result.append(','); NativePayload.quote(result, samples.get(index));
        }
        result.append("],\"sources\":{");
        for (int index = 0; index < 3; index++) {
            if (index > 0) result.append(','); NativePayload.quote(result, categories[index]);
            Source item = sources[index];
            result.append(":{\"rowsSeen\":").append(item.seen).append(",\"rowsIncluded\":").append(item.included)
                    .append(item.providerSeen < 0 ? "" : ",\"providerRowsSeen\":" + item.providerSeen)
                    .append(",\"pageCount\":").append(item.pages).append(",\"chainSha256\":\"")
                    .append(item.chain).append("\"}");
        }
        return result.append("}}").toString();
    }

    private static int index(String category) {
        return switch (category) { case "contacts" -> 0; case "calls" -> 1; case "messages" -> 2;
            default -> throw new IllegalArgumentException("invalid_category"); };
    }

    static String sha256(String value) {
        try {
            byte[] hash = MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.US_ASCII));
            char[] result = new char[hash.length * 2];
            char[] alphabet = "0123456789abcdef".toCharArray();
            for (int i = 0; i < hash.length; i++) {
                result[i * 2] = alphabet[(hash[i] & 0xff) >>> 4]; result[i * 2 + 1] = alphabet[hash[i] & 15];
            }
            return new String(result);
        } catch (NoSuchAlgorithmException impossible) { throw new IllegalStateException("sha256_unavailable", impossible); }
    }

    @Override public void close() { SanitizedStore.deleteFiles(file); }
}
