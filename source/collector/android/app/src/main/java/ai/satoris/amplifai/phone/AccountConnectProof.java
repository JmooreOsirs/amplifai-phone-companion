package ai.satoris.amplifai.phone;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;

import javax.net.ssl.HttpsURLConnection;

/** Fixed-publisher server proof; no browser cookie, account label or key enters this client. */
final class AccountConnectProof implements LocalBridge.AccountProof {
    private static final String CLAIM = "/api/v1/phone-connect-intents/claim";
    private static final String CONSUME = "/api/v1/phone-connect-intents/consume";
    private static final int MAX_RESPONSE_BYTES = 1024;

    @Override public LocalBridge.Destination claim(String intentId, String capability,
                                                    String handoffId, String payloadSha256) {
        JSONObject result = request(CLAIM, command(intentId, capability, handoffId, payloadSha256, null));
        if (result == null || result.length() != 2) return null;
        Object rawEmail = result.opt("destinationEmail");
        Object rawCourse = result.opt("destinationCourse");
        if (!(rawEmail instanceof String) || !(rawCourse instanceof String)) return null;
        String email = (String) rawEmail;
        String course = (String) rawCourse;
        if (email.isBlank() || !email.contains("@") || email.length() > 254 ||
                course.isBlank() || course.length() > 160) return null;
        return new LocalBridge.Destination(intentId, email, course);
    }

    @Override public boolean consume(String intentId, String capability, String releaseCode,
                                     String handoffId, String payloadSha256) {
        JSONObject result = request(CONSUME, command(intentId, capability, handoffId, payloadSha256, releaseCode));
        return result != null && result.length() == 1 && Boolean.TRUE.equals(result.opt("consumed"));
    }

    private static JSONObject command(String intentId, String capability, String handoffId,
                                      String payloadSha256, String releaseCode) {
        try {
            JSONObject value = new JSONObject().put("intentId", intentId).put("capability", capability);
            if (releaseCode != null) value.put("releaseCode", releaseCode);
            value.put("handoffId", handoffId).put("payloadSha256", payloadSha256);
            return value;
        } catch (JSONException invalid) { throw new IllegalArgumentException("invalid_account_binding"); }
    }

    private static JSONObject request(String path, JSONObject command) {
        HttpsURLConnection connection = null;
        try {
            URL target = new URL(LocalBridge.ACCOUNT_ORIGIN + path);
            connection = (HttpsURLConnection) target.openConnection();
            connection.setInstanceFollowRedirects(false);
            connection.setConnectTimeout(3000);
            connection.setReadTimeout(3000);
            connection.setUseCaches(false);
            connection.setRequestMethod("POST");
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setRequestProperty("Accept", "application/json");
            connection.setRequestProperty("Cache-Control", "no-store");
            connection.setDoOutput(true);
            byte[] body = command.toString().getBytes(StandardCharsets.UTF_8);
            if (body.length > 1024) return null;
            connection.setFixedLengthStreamingMode(body.length);
            try (var output = connection.getOutputStream()) { output.write(body); }
            if (connection.getResponseCode() != 200) return null;
            String contentType = connection.getContentType();
            if (contentType == null || !contentType.toLowerCase(java.util.Locale.ROOT).startsWith("application/json")) return null;
            int declared = connection.getContentLength();
            if (declared > MAX_RESPONSE_BYTES) return null;
            ByteArrayOutputStream bytes = new ByteArrayOutputStream(Math.max(0, declared));
            try (InputStream input = connection.getInputStream()) {
                byte[] chunk = new byte[512];
                int count;
                while ((count = input.read(chunk)) != -1) {
                    if (bytes.size() + count > MAX_RESPONSE_BYTES) return null;
                    bytes.write(chunk, 0, count);
                }
            }
            if (declared >= 0 && bytes.size() != declared) return null;
            return new JSONObject(new String(bytes.toByteArray(), StandardCharsets.UTF_8));
        } catch (IOException | JSONException failure) { return null; }
        finally { if (connection != null) connection.disconnect(); }
    }
}
