package ai.satoris.amplifai.phone;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteException;

import java.io.File;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/** App-private, short-lived metadata index. Provider rows and message content never leave this store. */
final class SanitizedStore implements AutoCloseable {
    static final String FILE_NAME = "phone-review.sqlite";
    private final File file;
    private final SQLiteDatabase db;

    SanitizedStore(Context context) {
        file = new File(context.getNoBackupFilesDir(), FILE_NAME);
        // A process restart cannot resume the one-use browser grant. Remove orphaned review data.
        deleteFiles(file);
        SQLiteDatabase opened = null;
        try {
            opened = SQLiteDatabase.openOrCreateDatabase(file, null);
            opened.execSQL("CREATE TABLE contact (id INTEGER PRIMARY KEY, name TEXT NOT NULL)");
            opened.execSQL("CREATE TABLE phone (contact_id INTEGER NOT NULL, number TEXT NOT NULL, PRIMARY KEY(contact_id,number))");
            opened.execSQL("CREATE INDEX phone_number ON phone(number)");
            opened.execSQL("CREATE TABLE interaction (category TEXT NOT NULL, id INTEGER NOT NULL, number TEXT NOT NULL, occurred_ms INTEGER NOT NULL, direction TEXT NOT NULL, duration INTEGER, PRIMARY KEY(category,id))");
            opened.execSQL("CREATE INDEX interaction_number ON interaction(category,number)");
            opened.execSQL("CREATE TABLE selected_phone (number TEXT PRIMARY KEY)");
            opened.execSQL("CREATE TABLE selected_contact (id INTEGER PRIMARY KEY)");
            opened.execSQL("CREATE TABLE source (category TEXT PRIMARY KEY, seen INTEGER NOT NULL, rejected INTEGER NOT NULL, available INTEGER NOT NULL)");
        } catch (RuntimeException failure) {
            if (opened != null) try { opened.close(); } catch (RuntimeException ignored) { /* Preserve the first storage error. */ }
            try { deleteFiles(file); } catch (SQLiteException ignored) { /* Original storage failure remains actionable. */ }
            throw failure;
        }
        db = opened;
    }

    static final class Coverage {
        final long seen;
        final long rejected;
        final boolean available;
        Coverage(long seen, long rejected, boolean available) {
            this.seen = seen; this.rejected = rejected; this.available = available;
        }
    }

    void begin(String category) {
        checkCategory(category);
        db.beginTransaction();
        try {
            db.delete("source", "category=?", new String[]{category});
            if (category.equals("contacts")) {
                db.delete("selected_phone", null, null);
                db.delete("selected_contact", null, null);
                db.delete("phone", null, null);
                db.delete("contact", null, null);
            } else db.delete("interaction", "category=?", new String[]{category});
        } catch (RuntimeException failure) {
            db.endTransaction();
            throw failure;
        }
    }

    void commit(String category, long seen, long rejected) {
        if (seen < 0 || seen > Integer.MAX_VALUE || rejected < 0 || rejected > seen) {
            throw new IllegalArgumentException("source_count_outside_protocol");
        }
        ContentValues source = new ContentValues();
        source.put("category", category); source.put("seen", seen);
        source.put("rejected", rejected); source.put("available", 1);
        db.insertOrThrow("source", null, source);
        db.setTransactionSuccessful();
        db.endTransaction();
    }

    void abort(String category) {
        if (db.inTransaction()) db.endTransaction();
        // Failed or interrupted rereads must never expose old or partial rows.
        db.beginTransaction();
        try {
            db.delete("source", "category=?", new String[]{category});
            if (category.equals("contacts")) {
                db.delete("selected_phone", null, null);
                db.delete("selected_contact", null, null);
                db.delete("phone", null, null);
                db.delete("contact", null, null);
            } else db.delete("interaction", "category=?", new String[]{category});
            db.setTransactionSuccessful();
        } finally { db.endTransaction(); }
    }

    void contact(long id, String name, String phone) {
        if (id < 0 || id > 9_007_199_254_740_991L) throw new IllegalArgumentException("source_id_outside_protocol");
        ContentValues values = new ContentValues();
        values.put("id", id); values.put("name", name == null ? "" : name);
        db.insertWithOnConflict("contact", null, values, SQLiteDatabase.CONFLICT_IGNORE);
        values.clear(); values.put("contact_id", id); values.put("number", phone);
        db.insertWithOnConflict("phone", null, values, SQLiteDatabase.CONFLICT_IGNORE);
    }

    void interaction(String category, long id, String phone, long date, String direction, Long duration) {
        if (id < 0 || id > 9_007_199_254_740_991L) throw new IllegalArgumentException("source_id_outside_protocol");
        ContentValues values = new ContentValues();
        values.put("category", category); values.put("id", id); values.put("number", phone);
        values.put("occurred_ms", date); values.put("direction", direction);
        if (duration == null) values.putNull("duration"); else values.put("duration", duration);
        db.insertOrThrow("interaction", null, values);
    }

    Coverage coverage(String category) {
        checkCategory(category);
        try (Cursor row = db.rawQuery("SELECT seen,rejected,available FROM source WHERE category=?", new String[]{category})) {
            return row.moveToFirst() ? new Coverage(row.getLong(0), row.getLong(1), row.getInt(2) == 1)
                    : new Coverage(0, 0, false);
        }
    }

    long count(String category) {
        checkCategory(category);
        String table = category.equals("contacts") ? "contact" : "interaction WHERE category='" + category + "'";
        try (Cursor row = db.rawQuery("SELECT COUNT(*) FROM " + table, null)) {
            row.moveToFirst(); return row.getLong(0);
        }
    }

    long contactCount(String search) {
        String term = search == null ? "" : search.trim();
        String query = term.isEmpty() ? "SELECT COUNT(*) FROM contact" :
                "SELECT COUNT(*) FROM contact c WHERE c.name LIKE ? ESCAPE '\\' OR EXISTS " +
                "(SELECT 1 FROM phone p WHERE p.contact_id=c.id AND p.number LIKE ? ESCAPE '\\')";
        String[] args = term.isEmpty() ? null : new String[]{like(term), like(term)};
        try (Cursor row = db.rawQuery(query, args)) { row.moveToFirst(); return row.getLong(0); }
    }

    List<MetadataModel.Contact> contactsPage(String search, int offset, int size) {
        String term = search == null ? "" : search.trim();
        String query = term.isEmpty() ? "SELECT id,name FROM contact ORDER BY id LIMIT ? OFFSET ?" :
                "SELECT c.id,c.name FROM contact c WHERE c.name LIKE ? ESCAPE '\\' OR EXISTS " +
                "(SELECT 1 FROM phone p WHERE p.contact_id=c.id AND p.number LIKE ? ESCAPE '\\') " +
                "ORDER BY c.id LIMIT ? OFFSET ?";
        String[] args = term.isEmpty() ? new String[]{String.valueOf(size), String.valueOf(offset)} :
                new String[]{like(term), like(term), String.valueOf(size), String.valueOf(offset)};
        List<MetadataModel.Contact> page = new ArrayList<>();
        try (Cursor rows = db.rawQuery(query, args)) {
            while (rows.moveToNext()) {
                long id = rows.getLong(0);
                List<String> phones = new ArrayList<>();
                try (Cursor values = db.rawQuery("SELECT number FROM phone WHERE contact_id=? ORDER BY number", new String[]{String.valueOf(id)})) {
                    while (values.moveToNext()) phones.add(values.getString(0));
                }
                page.add(new MetadataModel.Contact(id, rows.getString(1), phones));
            }
        }
        return page;
    }

    List<Long> contactIdsAfter(long previousId, int size) {
        if (size < 1) throw new IllegalArgumentException("invalid_page_size");
        List<Long> ids = new ArrayList<>();
        try (Cursor rows = db.rawQuery("SELECT id FROM contact WHERE id>? ORDER BY id LIMIT ?",
                new String[]{String.valueOf(previousId), String.valueOf(size)})) {
            while (rows.moveToNext()) ids.add(rows.getLong(0));
        }
        return ids;
    }

    private static String like(String term) {
        return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%";
    }

    void select(Set<Long> ids) {
        db.beginTransaction();
        try {
            db.delete("selected_phone", null, null);
            db.delete("selected_contact", null, null);
            for (long id : ids) {
                ContentValues selected = new ContentValues(); selected.put("id", id);
                db.insertOrThrow("selected_contact", null, selected);
                try (Cursor row = db.rawQuery("SELECT number FROM phone WHERE contact_id=?", new String[]{String.valueOf(id)})) {
                    while (row.moveToNext()) {
                        ContentValues value = new ContentValues(); value.put("number", row.getString(0));
                        db.insertWithOnConflict("selected_phone", null, value, SQLiteDatabase.CONFLICT_IGNORE);
                    }
                }
            }
            db.setTransactionSuccessful();
        } finally { db.endTransaction(); }
    }

    MetadataModel.Review review(Set<Long> ids) {
        select(ids);
        long selected;
        try (Cursor row = db.rawQuery("SELECT COUNT(*) FROM contact WHERE id IN (SELECT id FROM selected_contact)", null)) {
            row.moveToFirst(); selected = row.getLong(0);
        }
        long[] counts = new long[2]; long earliest = Long.MAX_VALUE, latest = Long.MIN_VALUE;
        for (int index = 0; index < 2; index++) {
            String category = index == 0 ? "calls" : "messages";
            try (Cursor row = db.rawQuery("SELECT COUNT(*),MIN(occurred_ms),MAX(occurred_ms) FROM interaction i " +
                    "JOIN selected_phone s ON s.number=i.number WHERE i.category=?", new String[]{category})) {
                row.moveToFirst(); counts[index] = row.getLong(0);
                if (!row.isNull(1)) { earliest = Math.min(earliest, row.getLong(1)); latest = Math.max(latest, row.getLong(2)); }
            }
        }
        if (selected != ids.size() || selected > Integer.MAX_VALUE || counts[0] > Integer.MAX_VALUE || counts[1] > Integer.MAX_VALUE) {
            throw new IllegalArgumentException("selection_count_outside_protocol");
        }
        return new MetadataModel.Review((int) selected, (int) counts[0], (int) counts[1],
                earliest == Long.MAX_VALUE ? 0 : earliest, latest == Long.MIN_VALUE ? 0 : latest);
    }

    Cursor selectedRows(String category) {
        checkCategory(category);
        if (category.equals("contacts")) return db.rawQuery(
                "SELECT id,name FROM contact WHERE id IN (SELECT id FROM selected_contact) ORDER BY id", null);
        return db.rawQuery("SELECT i.id,i.number,i.occurred_ms,i.direction,i.duration FROM interaction i " +
                "JOIN selected_phone s ON s.number=i.number WHERE i.category=? ORDER BY i.id", new String[]{category});
    }

    List<String> phones(long id) {
        List<String> values = new ArrayList<>();
        try (Cursor row = db.rawQuery("SELECT number FROM phone WHERE contact_id=? ORDER BY number", new String[]{String.valueOf(id)})) {
            while (row.moveToNext()) values.add(row.getString(0));
        }
        return values;
    }

    private static void checkCategory(String category) {
        if (!category.equals("contacts") && !category.equals("calls") && !category.equals("messages"))
            throw new IllegalArgumentException("invalid_category");
    }

    static void deleteFiles(File file) {
        for (String suffix : new String[]{"", "-wal", "-shm", "-journal"}) {
            File target = new File(file.getPath() + suffix);
            if (target.exists() && !target.delete()) throw new SQLiteException("private_storage_cleanup_failed");
        }
    }

    @Override public void close() {
        db.close(); deleteFiles(file);
    }
}
