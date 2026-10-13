package ai.satoris.amplifai.phone;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.function.Consumer;

/** Best-effort support-code delivery. Its request has no source content, credentials, or raw errors. */
final class SafeDiagnostic {
    private static final String ENDPOINT = "https://amplifai-database-engine.vercel.app/api/v1/companion-diagnostics";
    private SafeDiagnostic() {}

    static void send(String code, String journeySessionId, Consumer<Boolean> result) {
        if (!code.matches("A1\\|[0-9]{4}\\.[0-9]{2}\\.[0-9]{2}-rc[0-9]+\\|[A-F0-9]{8}\\|[a-z_]+\\|[a-z_]+\\|[0-9]+\\|0\\|0\\|-")) {
            result.accept(false); return;
        }
        String journey = journeySessionId != null && journeySessionId.matches(
                "[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}") ? journeySessionId : null;
        Thread sender = new Thread(() -> {
            boolean delivered = false;
            HttpURLConnection connection = null;
            try {
                connection = (HttpURLConnection) new URL(ENDPOINT).openConnection();
                connection.setConnectTimeout(3000);
                connection.setReadTimeout(3000);
                connection.setRequestMethod("POST");
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setDoOutput(true);
                String body = "{\"code\":\"" + code + "\"" +
                        (journey == null ? "" : ",\"journeySessionId\":\"" + journey + "\"") + "}";
                byte[] bytes = body.getBytes(StandardCharsets.US_ASCII);
                connection.setFixedLengthStreamingMode(bytes.length);
                try (OutputStream stream = connection.getOutputStream()) { stream.write(bytes); }
                delivered = connection.getResponseCode() == 204;
            } catch (Exception ignored) {
                // The same bounded code remains visible for manual copying.
            } finally {
                if (connection != null) connection.disconnect();
                result.accept(delivered);
            }
        }, "amplifai-safe-diagnostic");
        sender.setDaemon(true);
        sender.start();
    }
}
