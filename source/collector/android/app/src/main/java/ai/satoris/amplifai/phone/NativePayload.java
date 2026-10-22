package ai.satoris.amplifai.phone;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Serializes only owner-selected metadata accepted by the browser's strict bridge schema. */
public final class NativePayload {
    private static final int MAX_CONTACTS = 25_000;
    private static final int MAX_INTERACTIONS = 256_000;
    private static final int MAX_BYTES = 32 * 1024 * 1024;

    private NativePayload() {}

    public static byte[] selected(List<MetadataModel.Contact> contacts,
                                  List<MetadataModel.Interaction> calls,
                                  List<MetadataModel.Interaction> messages,
                                  Set<Long> selectedIds, boolean callsAvailable,
                                  boolean messagesAvailable, Instant collectedAt) {
        List<MetadataModel.Contact> chosen = new ArrayList<>();
        Set<String> phones = new HashSet<>();
        for (MetadataModel.Contact contact : contacts) {
            if (!selectedIds.contains(contact.sourceId)) continue;
            if (contact.sourceId < 0 || contact.name.length() > 240 || contact.phones.size() > 20) {
                throw new IllegalArgumentException("Selected contact exceeds the metadata contract.");
            }
            for (String number : contact.phones) {
                if (!number.matches("\\+[1-9][0-9]{5,14}")) {
                    throw new IllegalArgumentException("Selected contact has an invalid phone number.");
                }
            }
            chosen.add(contact);
            phones.addAll(contact.phones);
        }
        if (chosen.isEmpty() || chosen.size() > MAX_CONTACTS) {
            throw new IllegalArgumentException("Select between 1 and 25,000 contacts.");
        }
        List<MetadataModel.Interaction> matched = new ArrayList<>();
        if (callsAvailable) addMatching(matched, calls, phones, "call");
        if (messagesAvailable) addMatching(matched, messages, phones, "message");
        if (matched.size() > MAX_INTERACTIONS) {
            throw new IllegalArgumentException("Selected interactions exceed the local handoff limit.");
        }
        StringBuilder json = new StringBuilder(1024);
        json.append("{\"schema\":1,\"platform\":\"android\",\"since\":\"1970-01-01T00:00:00Z\",\"collectedAt\":");
        quote(json, collectedAt.toString());
        json.append(",\"missingSources\":[");
        if (!callsAvailable) quote(json, "calls");
        if (!messagesAvailable) {
            if (!callsAvailable) json.append(',');
            quote(json, "messages");
        }
        json.append("],\"contacts\":[");
        for (int index = 0; index < chosen.size(); index++) {
            if (index > 0) json.append(',');
            MetadataModel.Contact contact = chosen.get(index);
            json.append("{\"sourceId\":").append(contact.sourceId).append(",\"name\":");
            quote(json, contact.name);
            json.append(",\"phones\":[");
            for (int phoneIndex = 0; phoneIndex < contact.phones.size(); phoneIndex++) {
                if (phoneIndex > 0) json.append(',');
                quote(json, contact.phones.get(phoneIndex));
            }
            json.append("]}");
        }
        json.append("],\"interactions\":[");
        for (int index = 0; index < matched.size(); index++) {
            if (index > 0) json.append(',');
            MetadataModel.Interaction interaction = matched.get(index);
            json.append("{\"sourceId\":").append(interaction.sourceId).append(",\"kind\":");
            quote(json, interaction.kind);
            json.append(",\"transport\":");
            quote(json, interaction.kind.equals("call") ? "call" : "sms");
            json.append(",\"occurredAt\":");
            quote(json, Instant.ofEpochMilli(interaction.occurredAtMs).toString());
            json.append(",\"direction\":");
            quote(json, interaction.direction);
            json.append(",\"participants\":[");
            quote(json, interaction.counterpart);
            json.append("],\"durationSeconds\":");
            json.append(interaction.durationSeconds == null ? "null" : interaction.durationSeconds);
            json.append('}');
        }
        json.append("]}");
        byte[] payload = json.toString().getBytes(StandardCharsets.UTF_8);
        if (payload.length > MAX_BYTES) {
            throw new IllegalArgumentException("Selected metadata exceeds the 32 MB local handoff limit.");
        }
        return payload;
    }

    private static void addMatching(List<MetadataModel.Interaction> target,
                                    List<MetadataModel.Interaction> source,
                                    Set<String> phones, String kind) {
        for (MetadataModel.Interaction interaction : source) {
            if (!phones.contains(interaction.counterpart)) continue;
            if (interaction.sourceId < 0 || interaction.occurredAtMs <= 0 ||
                    !interaction.kind.equals(kind) ||
                    !interaction.direction.matches("incoming|outgoing|missed|unknown") ||
                    (interaction.durationSeconds != null &&
                            (interaction.durationSeconds < 0 || interaction.durationSeconds > 86_400))) {
                throw new IllegalArgumentException("A matching interaction exceeds the metadata contract.");
            }
            target.add(interaction);
        }
    }

    static void quote(StringBuilder output, String value) {
        output.append('"');
        for (int index = 0; index < value.length(); index++) {
            char character = value.charAt(index);
            if (character == '"' || character == '\\') {
                output.append('\\').append(character);
            } else if (character < 0x20 || character > 0x7e) {
                output.append(String.format(java.util.Locale.ROOT, "\\u%04x", (int) character));
            } else {
                output.append(character);
            }
        }
        output.append('"');
    }
}
