package ai.satoris.amplifai.phone;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.URL;
import java.util.Locale;
import java.util.concurrent.TimeUnit;

import javax.net.ssl.HttpsURLConnection;

/** Only the native capability routes; browser-session status/revoke are deliberately absent. */
interface NativeAuthorityTransport {
    enum Route {
        CLAIM("/api/v1/phone-platform-setups/claim", 8192),
        APPROVE("/api/v1/phone-platform-setups/approve", 16384),
        START("/api/v1/phone-run-approvals/preparation/start", 2048),
        BIND("/api/v1/phone-run-approvals/preparation/bind", 2048),
        VERIFY("/api/v1/phone-run-approvals/verify-intent", 2048),
        CONNECT("/api/v1/phone-connect-intents/claim", 1024),
        CONSUME("/api/v1/phone-connect-intents/consume", 1024);

        final String path;
        final int responseLimit;
        Route(String path, int responseLimit) { this.path = path; this.responseLimit = responseLimit; }
    }

    final class Failure extends Exception {
        final boolean retryable;
        Failure(boolean retryable) { super(retryable ? "authority_retry_required" : "authority_setup_required"); this.retryable = retryable; }
    }

    byte[] post(Route route, byte[] request) throws Failure;

    final class Https implements NativeAuthorityTransport {
        interface Connections { HttpsURLConnection open(URL target) throws IOException; }
        private final Connections connections;
        Https() { this(target -> (HttpsURLConnection) target.openConnection()); }
        Https(Connections connections) { this.connections = connections; }

        @Override public byte[] post(Route route, byte[] request) throws Failure {
            if (request == null || request.length == 0 || request.length > 4096) throw new Failure(false);
            HttpsURLConnection connection = null;
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
            try {
                connection = connections.open(new URL(LocalBridge.ACCOUNT_ORIGIN + route.path));
                connection.setInstanceFollowRedirects(false);
                connection.setConnectTimeout(5000);
                connection.setReadTimeout(5000);
                connection.setUseCaches(false);
                connection.setRequestMethod("POST");
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setRequestProperty("Accept", "application/json");
                connection.setRequestProperty("Cache-Control", "no-store");
                connection.setDoOutput(true);
                connection.setFixedLengthStreamingMode(request.length);
                try (var output = connection.getOutputStream()) { output.write(request); }
                int status = connection.getResponseCode();
                if (status != 200 && !(route == Route.APPROVE && status == 201)) throw new Failure(status == 503);
                String type = connection.getContentType();
                if (type == null || !"application/json".equals(type.split(";", 2)[0].trim().toLowerCase(Locale.ROOT)))
                    throw new Failure(true);
                int declared = connection.getContentLength();
                if (declared > route.responseLimit) throw new Failure(true);
                ByteArrayOutputStream body = new ByteArrayOutputStream();
                try (InputStream input = connection.getInputStream()) {
                    byte[] buffer = new byte[1024];
                    while (true) {
                        long remaining = deadline - System.nanoTime();
                        if (remaining <= 0) throw new Failure(true);
                        connection.setReadTimeout((int) Math.max(1, Math.min(5000, TimeUnit.NANOSECONDS.toMillis(remaining))));
                        int count = input.read(buffer);
                        if (count == -1) break;
                        if (body.size() + count > route.responseLimit) throw new Failure(true);
                        body.write(buffer, 0, count);
                    }
                }
                if (body.size() == 0 || (declared >= 0 && body.size() != declared)) throw new Failure(true);
                return body.toByteArray();
            } catch (IOException unavailable) {
                throw new Failure(true);
            } finally {
                if (connection != null) connection.disconnect();
            }
        }
    }
}
