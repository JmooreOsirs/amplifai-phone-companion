package ai.satoris.amplifai.phone;

import android.Manifest;
import android.content.ContentResolver;
import android.content.Context;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.os.CancellationSignal;
import android.provider.CallLog;
import android.provider.ContactsContract;
import android.provider.Telephony;
import android.telephony.PhoneNumberUtils;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.LongConsumer;

/** Explicit, permission-gated provider projections. Never queries message content. */
public final class PhoneMetadataReader {
    private static final int MAX_CONTACT_ROWS = 100_000;
    private static final int MAX_INTERACTION_ROWS = 256_000;
    private final Context context;
    private final ContentResolver resolver;

    public PhoneMetadataReader(Context context) {
        this.context = context.getApplicationContext();
        this.resolver = this.context.getContentResolver();
    }

    private void require(String permission) {
        if (context.checkSelfPermission(permission) != PackageManager.PERMISSION_GRANTED) {
            throw new SecurityException("source_permission_unavailable");
        }
    }

    private static String normalized(String value, String countryIso) {
        if (value == null || value.isBlank()) return null;
        String result = PhoneNumberUtils.formatNumberToE164(value, countryIso.toUpperCase(Locale.ROOT));
        return result != null && result.matches("\\+[1-9][0-9]{5,14}") ? result : null;
    }

    private static final class MutableContact {
        final long id;
        final String name;
        final LinkedHashSet<String> phones = new LinkedHashSet<>();

        MutableContact(long id, String name) { this.id = id; this.name = name; }
    }

    public MetadataModel.Source<MetadataModel.Contact> contacts(String countryIso, CancellationSignal signal) {
        signal.throwIfCanceled();
        require(Manifest.permission.READ_CONTACTS);
        String[] projection = {
                ContactsContract.CommonDataKinds.Phone.CONTACT_ID,
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME_PRIMARY,
                ContactsContract.CommonDataKinds.Phone.NUMBER,
        };
        Map<Long, MutableContact> grouped = new LinkedHashMap<>();
        int rejected = 0;
        boolean truncated = false;
        try (Cursor cursor = resolver.query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                projection, null, null, null, signal)) {
            if (cursor == null) throw new IllegalStateException("contact_provider_unavailable");
            int id = cursor.getColumnIndexOrThrow(projection[0]);
            int name = cursor.getColumnIndexOrThrow(projection[1]);
            int phone = cursor.getColumnIndexOrThrow(projection[2]);
            int scanned = 0;
            while (cursor.moveToNext()) {
                signal.throwIfCanceled();
                if (scanned++ >= MAX_CONTACT_ROWS) { truncated = true; break; }
                String e164 = normalized(cursor.getString(phone), countryIso);
                if (e164 == null) { rejected++; continue; }
                long contactId = cursor.getLong(id);
                MutableContact contact = grouped.computeIfAbsent(contactId,
                        key -> new MutableContact(key, cursor.getString(name)));
                MetadataModel.addContactPhone(contact.phones, e164);
            }
        }
        List<MetadataModel.Contact> records = new ArrayList<>();
        for (MutableContact item : grouped.values()) {
            signal.throwIfCanceled();
            records.add(new MetadataModel.Contact(item.id, item.name, new ArrayList<>(item.phones)));
        }
        return new MetadataModel.Source<>(records, rejected, truncated);
    }

    public MetadataModel.Source<MetadataModel.Interaction> calls(String countryIso, CancellationSignal signal) {
        signal.throwIfCanceled();
        require(Manifest.permission.READ_CALL_LOG);
        String[] projection = {
                CallLog.Calls._ID, CallLog.Calls.NUMBER, CallLog.Calls.DATE,
                CallLog.Calls.TYPE, CallLog.Calls.DURATION,
        };
        List<MetadataModel.Interaction> records = new ArrayList<>();
        int rejected = 0;
        boolean truncated = false;
        try (Cursor cursor = resolver.query(CallLog.Calls.CONTENT_URI, projection,
                null, null, CallLog.Calls.DATE + " DESC", signal)) {
            if (cursor == null) throw new IllegalStateException("call_provider_unavailable");
            int scanned = 0;
            while (cursor.moveToNext()) {
                signal.throwIfCanceled();
                if (scanned++ >= MAX_INTERACTION_ROWS) { truncated = true; break; }
                String e164 = normalized(cursor.getString(1), countryIso);
                long date = cursor.getLong(2);
                if (e164 == null || !validDate(date)) { rejected++; continue; }
                int type = cursor.getInt(3);
                String direction = type == CallLog.Calls.INCOMING_TYPE ? "incoming"
                        : type == CallLog.Calls.OUTGOING_TYPE ? "outgoing"
                        : type == CallLog.Calls.MISSED_TYPE ? "missed" : "unknown";
                long seconds = cursor.getLong(4);
                records.add(new MetadataModel.Interaction(cursor.getLong(0), "call", e164, date,
                        direction, seconds >= 0 && seconds <= 86_400 ? seconds : null));
            }
        }
        return new MetadataModel.Source<>(records, rejected, truncated);
    }

    public MetadataModel.Source<MetadataModel.Interaction> messages(String countryIso, CancellationSignal signal) {
        signal.throwIfCanceled();
        require(Manifest.permission.READ_SMS);
        // BODY, SUBJECT, PERSON, THREAD_ID and MMS/RCS providers are deliberately excluded.
        String[] projection = {
                Telephony.Sms._ID, Telephony.Sms.ADDRESS, Telephony.Sms.DATE, Telephony.Sms.TYPE,
        };
        List<MetadataModel.Interaction> records = new ArrayList<>();
        int rejected = 0;
        boolean truncated = false;
        try (Cursor cursor = resolver.query(Telephony.Sms.CONTENT_URI, projection,
                null, null, Telephony.Sms.DATE + " DESC", signal)) {
            if (cursor == null) throw new IllegalStateException("sms_provider_unavailable");
            int scanned = 0;
            while (cursor.moveToNext()) {
                signal.throwIfCanceled();
                if (scanned++ >= MAX_INTERACTION_ROWS) { truncated = true; break; }
                String e164 = normalized(cursor.getString(1), countryIso);
                long date = cursor.getLong(2);
                int type = cursor.getInt(3);
                if (e164 == null || !validDate(date) ||
                        (type != Telephony.Sms.MESSAGE_TYPE_INBOX && type != Telephony.Sms.MESSAGE_TYPE_SENT)) {
                    rejected++; continue;
                }
                records.add(new MetadataModel.Interaction(cursor.getLong(0), "message", e164, date,
                        type == Telephony.Sms.MESSAGE_TYPE_INBOX ? "incoming" : "outgoing", null));
            }
        }
        return new MetadataModel.Source<>(records, rejected, truncated);
    }

    private static boolean validDate(long epochMs) {
        return epochMs > 0 && epochMs <= System.currentTimeMillis() + 86_400_000L;
    }

    /** Streams one permission-gated source into an atomic private transaction. */
    public SanitizedStore.Coverage readToStore(String category, String countryIso,
                                               CancellationSignal signal, SanitizedStore store,
                                               LongConsumer progress) {
        String permission = category.equals("contacts") ? Manifest.permission.READ_CONTACTS
                : category.equals("calls") ? Manifest.permission.READ_CALL_LOG
                : category.equals("messages") ? Manifest.permission.READ_SMS : null;
        if (permission == null) throw new IllegalArgumentException("invalid_category");
        signal.throwIfCanceled();
        require(permission);
        String[] projection;
        android.net.Uri uri;
        String order = null;
        if (category.equals("contacts")) {
            projection = new String[]{ContactsContract.CommonDataKinds.Phone.CONTACT_ID,
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME_PRIMARY,
                    ContactsContract.CommonDataKinds.Phone.NUMBER};
            uri = ContactsContract.CommonDataKinds.Phone.CONTENT_URI;
        } else if (category.equals("calls")) {
            projection = new String[]{CallLog.Calls._ID, CallLog.Calls.NUMBER, CallLog.Calls.DATE,
                    CallLog.Calls.TYPE, CallLog.Calls.DURATION};
            uri = CallLog.Calls.CONTENT_URI; order = CallLog.Calls.DATE + " DESC";
        } else {
            // BODY, SUBJECT, PERSON, THREAD_ID and MMS/RCS providers are never queried.
            projection = new String[]{Telephony.Sms._ID, Telephony.Sms.ADDRESS, Telephony.Sms.DATE, Telephony.Sms.TYPE};
            uri = Telephony.Sms.CONTENT_URI; order = Telephony.Sms.DATE + " DESC";
        }
        store.begin(category);
        long seen = 0, rejected = 0;
        try (Cursor cursor = resolver.query(uri, projection, null, null, order, signal)) {
            if (cursor == null) throw new IllegalStateException("source_provider_unavailable");
            while (cursor.moveToNext()) {
                signal.throwIfCanceled();
                seen++;
                if (seen > Integer.MAX_VALUE) throw new IllegalArgumentException("source_count_outside_protocol");
                if (seen % 1000 == 0) progress.accept(seen);
                String phone = normalized(cursor.getString(category.equals("contacts") ? 2 : 1), countryIso);
                if (phone == null) { rejected++; continue; }
                if (category.equals("contacts")) {
                    store.contact(cursor.getLong(0), cursor.getString(1), phone);
                    continue;
                }
                long date = cursor.getLong(2);
                if (!validDate(date)) { rejected++; continue; }
                int type = cursor.getInt(3);
                String direction;
                Long seconds = null;
                if (category.equals("calls")) {
                    direction = type == CallLog.Calls.INCOMING_TYPE ? "incoming"
                            : type == CallLog.Calls.OUTGOING_TYPE ? "outgoing"
                            : type == CallLog.Calls.MISSED_TYPE ? "missed" : "unknown";
                    long duration = cursor.getLong(4);
                    if (duration >= 0 && duration <= 86_400) seconds = duration;
                } else {
                    if (type != Telephony.Sms.MESSAGE_TYPE_INBOX && type != Telephony.Sms.MESSAGE_TYPE_SENT) {
                        rejected++; continue;
                    }
                    direction = type == Telephony.Sms.MESSAGE_TYPE_INBOX ? "incoming" : "outgoing";
                }
                store.interaction(category, cursor.getLong(0), phone, date, direction, seconds);
            }
            signal.throwIfCanceled();
            store.commit(category, seen, rejected);
            progress.accept(seen);
            return store.coverage(category);
        } catch (RuntimeException failure) {
            store.abort(category);
            throw failure;
        }
    }
}
