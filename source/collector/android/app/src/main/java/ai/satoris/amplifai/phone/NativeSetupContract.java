package ai.satoris.amplifai.phone;

import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Map;

/** Frozen phone-native-setup-v2 Android boundary. Parsing does not grant Start or collection. */
final class NativeSetupContract {
    static final String PROTOCOL = "phone-native-setup-v2";
    static final String PLATFORM = "android";
    static final String CHANNEL = "android-website";
    static final String PACKAGE = "ai.satoris.amplifai.phone";
    static final String DISCLOSURE_VERSION = "phone-run-android-v1";
    static final String DISCLOSURE_SHA256 = "cce897f226ab97045058dc1ddf68a1fa8633a40071429bddf5f21fbf1b79bdba";
    static final String SENTENCE = "AMPLIFai Phone reads contact names and phone numbers, plus available call and SMS metadata, locally on this Android phone with your Android permissions. After you choose contacts and continue, their available metadata is saved to the account and course shown. Message text and attachments are never read. Android or installer restrictions can leave sources unavailable.";
    static final String AGREEMENT = "I approve this one phone collection and automatic saving of the contacts I choose, with their available call and SMS metadata, to this account and course.";
    static final String POLICY_VERSION = "account-2026-10-v7";
    static final String POLICY_SHA256 = "446be198f41a9c3d335b64f094b3ed2506810c0a5862de1422cd62fa9af5cf89";
    static final String PRIVACY_PATH = "/experience/account-privacy?version=account-2026-10-v7#phone-collection-and-saving";
    static final String SUPPLEMENT_VERSION = "phone-platforms-2026-10-v1";
    static final String SUPPLEMENT_SHA256 = "995c62d8f67ea2ac5e295b50dfaf65f9e6bd0f6d3b962a91cfe3555bd1ad1667";
    static final String SUPPLEMENT_PATH = "/experience/phone-platform-privacy?version=phone-platforms-2026-10-v1";
    static final long SETUP_SECONDS = 600;
    static final long RUN_SECONDS = 43200;
    static final long CLOCK_SKEW_SECONDS = 60;
    private static final List<String> CATEGORIES = Collections.unmodifiableList(Arrays.asList("contacts", "calls", "messages"));

    record Client(String version, String build) {
        Client {
            require(version != null && version.matches("[0-9A-Za-z][0-9A-Za-z.+-]{0,63}") &&
                    build != null && build.matches("[0-9]{1,12}"));
        }
        Map<String, Object> fields() {
            return SetupJson.object("platform", PLATFORM, "channel", CHANNEL, "architecture", "universal",
                    "bundleId", PACKAGE, "version", version, "build", build);
        }
    }
    record Link(String setupId, String bootstrap) {
        Link { uuid(setupId); capability(bootstrap); }
        @Override public String toString() { return "NativeSetupLink[redacted]"; }
    }
    record Scope(String ownerId, String enrollmentId, String courseId, String email, String course, List<String> categories) {
        Scope { categories = Collections.unmodifiableList(new ArrayList<>(categories)); }
        @Override public String toString() { return "NativeSetupScope[redacted]"; }
    }
    record Claim(String setupId, String requestId, Client client, Scope scope, String snapshot,
                 Instant expiresAt) {
        @Override public String toString() { return "NativeSetupClaim[redacted]"; }
    }
    record Approval(String setupId, String requestId, String snapshot, String runId, String approvalId,
                    Client client, Scope scope, Instant approvedAt, Instant expiresAt) {
        @Override public String toString() { return "NativeSetupApproval[redacted]"; }
    }

    private NativeSetupContract() {}

    static Link link(String value) {
        require(value != null && value.length() <= 512);
        URI uri;
        try { uri = URI.create(value); } catch (IllegalArgumentException malformed) { throw invalid(); }
        require("amplifai-phone".equals(uri.getScheme()) && "setup".equals(uri.getRawAuthority()) &&
                "".equals(uri.getRawPath()) && uri.getRawFragment() == null && uri.getRawQuery() != null);
        String[] parameters = uri.getRawQuery().split("&", -1);
        require(parameters.length == 3);
        Map<String, Object> fields = SetupJson.object();
        for (String parameter : parameters) {
            String[] pair = parameter.split("=", -1);
            require(pair.length == 2 && !fields.containsKey(pair[0]));
            fields.put(pair[0], pair[1]);
        }
        keys(fields, "protocol", "setup", "bootstrap");
        equal(fields, "protocol", PROTOCOL);
        return new Link(string(fields, "setup"), string(fields, "bootstrap"));
    }

    static String capabilities(Client client) {
        Map<String, Object> fields = SetupJson.object("schema", 2, "product", "amplifai-phone");
        fields.putAll(client.fields());
        fields.put("protocols", Collections.singletonList(PROTOCOL));
        return SetupJson.encode(fields);
    }

    static Claim claim(byte[] wire, Link link, String requestId, Client expected, Instant now) {
        Map<String, Object> fields = SetupJson.parse(wire, 8192);
        keys(fields, "protocol", "setupId", "requestId", "state", "ownerId", "enrollmentId", "courseId",
                "destinationEmail", "destinationCourse", "categories", "client", "expiresAt", "setupSnapshotSha256", "disclosure");
        equal(fields, "protocol", PROTOCOL); equal(fields, "state", "pending_approval");
        equal(fields, "setupId", link.setupId); equal(fields, "requestId", requestId);
        require(expected.equals(client(object(fields, "client"))));
        disclosure(object(fields, "disclosure"));
        Instant expires = time(fields, "expiresAt");
        require(expires.isAfter(now) && !expires.isAfter(now.plusSeconds(SETUP_SECONDS + CLOCK_SKEW_SECONDS)));
        return new Claim(link.setupId, requestId, expected, scope(fields), digest(fields, "setupSnapshotSha256"), expires);
    }

    static Approval approval(byte[] wire, Claim claim, String requestId, Instant now) {
        Map<String, Object> fields = SetupJson.parse(wire, 16384);
        keys(fields, "protocol", "setupId", "requestId", "setupSnapshotSha256", "runId", "approvalId",
                "approvalLocation", "disclosureVersion", "disclosureSha256", "supplementVersion", "supplementSha256",
                "ownerId", "enrollmentId", "courseId", "destinationEmail", "destinationCourse", "categories", "client",
                "approvedAt", "expiresAt", "preparation");
        equal(fields, "protocol", PROTOCOL); equal(fields, "setupId", claim.setupId);
        equal(fields, "requestId", requestId); equal(fields, "setupSnapshotSha256", claim.snapshot);
        equal(fields, "approvalLocation", "native"); equal(fields, "disclosureVersion", DISCLOSURE_VERSION);
        equal(fields, "disclosureSha256", DISCLOSURE_SHA256); equal(fields, "supplementVersion", SUPPLEMENT_VERSION);
        equal(fields, "supplementSha256", SUPPLEMENT_SHA256);
        require(claim.client.equals(client(object(fields, "client"))) && claim.scope.equals(scope(fields)));
        String approvalId = uuid(string(fields, "approvalId")), runId = uuid(string(fields, "runId"));
        Instant approved = time(fields, "approvedAt"), expires = time(fields, "expiresAt");
        require(!approved.isAfter(now.plusSeconds(CLOCK_SKEW_SECONDS)) && approved.isBefore(claim.expiresAt) &&
                expires.isAfter(now) && expires.isAfter(approved) &&
                !expires.isAfter(approved.plusSeconds(RUN_SECONDS)));
        Map<String, Object> preparation = object(fields, "preparation");
        keys(preparation, "protocol", "approvalId", "enrollmentId", "destinationEmail", "destinationCourse", "categories",
                "disclosureVersion", "expiresAt", "state", "operation");
        equal(preparation, "protocol", "phone-start-v1"); equal(preparation, "state", "current");
        require(preparation.get("operation") == null);
        for (String field : Arrays.asList("approvalId", "enrollmentId", "destinationEmail", "destinationCourse", "categories",
                "disclosureVersion", "expiresAt")) require(fields.get(field).equals(preparation.get(field)));
        return new Approval(claim.setupId, requestId, claim.snapshot, runId, approvalId, claim.client, claim.scope, approved, expires);
    }

    static String disclosureText() {
        return String.join("\n", DISCLOSURE_VERSION, SENTENCE, AGREEMENT, POLICY_VERSION, POLICY_SHA256, PRIVACY_PATH,
                SUPPLEMENT_VERSION, SUPPLEMENT_SHA256, SUPPLEMENT_PATH, PLATFORM, CHANNEL);
    }

    static Map<String, Object> disclosureReference() {
        return SetupJson.object("version", DISCLOSURE_VERSION, "sha256", DISCLOSURE_SHA256,
                "supplementVersion", SUPPLEMENT_VERSION, "supplementSha256", SUPPLEMENT_SHA256);
    }

    static String capability(String value) {
        require(value != null && value.matches("[A-Za-z0-9_-]{43}"));
        byte[] decoded;
        try { decoded = Base64.getUrlDecoder().decode(value); } catch (IllegalArgumentException malformed) { throw invalid(); }
        require(decoded.length == 32 && Base64.getUrlEncoder().withoutPadding().encodeToString(decoded).equals(value));
        return value;
    }

    static String uuid(String value) {
        require(value != null && value.matches("[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"));
        return value;
    }

    static String sha256(byte[] value) {
        try {
            StringBuilder text = new StringBuilder(64);
            for (byte part : MessageDigest.getInstance("SHA-256").digest(value))
                text.append("0123456789abcdef".charAt((part & 255) >>> 4)).append("0123456789abcdef".charAt(part & 15));
            return text.toString();
        } catch (NoSuchAlgorithmException unavailable) { throw new IllegalStateException("setup_digest_unavailable"); }
    }

    private static Client client(Map<String, Object> fields) {
        keys(fields, "platform", "channel", "architecture", "bundleId", "version", "build");
        equal(fields, "platform", PLATFORM); equal(fields, "channel", CHANNEL);
        equal(fields, "architecture", "universal"); equal(fields, "bundleId", PACKAGE);
        return new Client(string(fields, "version"), string(fields, "build"));
    }

    private static Scope scope(Map<String, Object> fields) {
        String email = string(fields, "destinationEmail"), course = string(fields, "destinationCourse");
        require(email.length() <= 254 && email.getBytes(StandardCharsets.UTF_8).length <= 254 &&
                email.matches("[^\\s@]+@[^\\s@]+\\.[^\\s@]+") && printable(email));
        require(!course.trim().isEmpty() && course.length() <= 160 && printable(course));
        Object raw = fields.get("categories");
        require(raw instanceof List);
        List<?> values = (List<?>) raw;
        require(!values.isEmpty() && values.size() <= 3 && "contacts".equals(values.get(0)));
        List<String> categories = new ArrayList<>();
        int previous = -1;
        for (Object value : values) {
            int index = CATEGORIES.indexOf(value);
            require(index > previous);
            previous = index;
            categories.add((String) value);
        }
        return new Scope(uuid(string(fields, "ownerId")), uuid(string(fields, "enrollmentId")), uuid(string(fields, "courseId")),
                email, course, categories);
    }

    private static void disclosure(Map<String, Object> fields) {
        Map<String, Object> expected = SetupJson.object("version", DISCLOSURE_VERSION, "sentences", Collections.singletonList(SENTENCE),
                "agreement", AGREEMENT, "policyVersion", POLICY_VERSION, "policySha256", POLICY_SHA256, "privacyHref", PRIVACY_PATH,
                "supplementVersion", SUPPLEMENT_VERSION, "supplementSha256", SUPPLEMENT_SHA256, "supplementHref", SUPPLEMENT_PATH,
                "platform", PLATFORM, "channel", CHANNEL, "sha256", DISCLOSURE_SHA256);
        require(expected.equals(fields) && DISCLOSURE_SHA256.equals(sha256(disclosureText().getBytes(StandardCharsets.UTF_8))));
    }

    private static boolean printable(String value) {
        for (int i = 0; i < value.length(); i++) if (Character.isISOControl(value.charAt(i))) return false;
        return true;
    }
    static Map<String, Object> object(Map<String, Object> fields, String key) {
        require(fields.get(key) instanceof Map);
        @SuppressWarnings("unchecked") Map<String, Object> object = (Map<String, Object>) fields.get(key);
        return object;
    }
    static String string(Map<String, Object> fields, String key) {
        require(fields.get(key) instanceof String);
        return (String) fields.get(key);
    }
    private static String digest(Map<String, Object> fields, String key) {
        String value = string(fields, key);
        require(value.matches("[0-9a-f]{64}"));
        return value;
    }
    static Instant time(Map<String, Object> fields, String key) {
        String value = string(fields, key);
        require(value.matches("[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\\.[0-9]{1,9})?(Z|[+-][0-9]{2}:[0-9]{2})"));
        try { return OffsetDateTime.parse(value).toInstant(); } catch (DateTimeParseException malformed) { throw invalid(); }
    }
    static void equal(Map<String, Object> fields, String key, String value) { require(value.equals(fields.get(key))); }
    static void keys(Map<String, Object> fields, String... keys) {
        require(fields.keySet().equals(new HashSet<>(Arrays.asList(keys))));
    }
    static void require(boolean accepted) { if (!accepted) throw invalid(); }
    private static IllegalArgumentException invalid() { return new IllegalArgumentException("setup_contract_invalid"); }
}
