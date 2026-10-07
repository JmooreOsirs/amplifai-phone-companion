package ai.satoris.amplifai.phone;

import java.io.ByteArrayOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.SocketTimeoutException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import java.util.function.LongSupplier;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** One-use browser handoff bound to this phone's loopback interface only. */
public final class LocalBridge implements AutoCloseable {
    public static final int PORT = 48751;
    public static final String ACCOUNT_ORIGIN = "https://amplifai-database-engine.vercel.app";
    private static final int MAX_HEADER_BYTES = 8192;
    private static final int MAX_REQUEST_BYTES = 1024;
    private static final int PAGE_BYTES = 128 * 1024;
    private static final int PAIR_SECONDS = 5 * 60;
    public static final long LIFETIME_MILLIS = TimeUnit.SECONDS.toMillis(PAIR_SECONDS);
    public enum EndReason { COMPLETED, CANCELLED, EXPIRED, ATTEMPTS_EXHAUSTED, FAILED }
    private static final int MAX_ATTEMPTS = 5;
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final Pattern SAVED_ACKNOWLEDGMENT = Pattern.compile(
            "\\s*\\{\\s*\"handoffId\"\\s*:\\s*\"([0-9a-f-]{36})\"\\s*,\\s*\"payloadSha256\"\\s*:\\s*\"([0-9a-f]{64})\"\\s*,\\s*\"saved\"\\s*:\\s*true\\s*\\}\\s*");

    private final ServerSocket listener;
    private final byte[] payload;
    private final String origin;
    private final String code;
    private final long expiresAt;
    private final Thread thread;
    private final int port;
    private final LongSupplier clock;
    private final Consumer<EndReason> onClosed;
    private final Runnable onReceived;
    private final String handoffId;
    private final String payloadSha256;
    private volatile boolean closed;
    private Socket activeSocket;
    private String token;
    private int attempts;
    private int nextCursor;
    private boolean used;

    LocalBridge(byte[] selectedPayload, String allowedOrigin, int port) throws IOException {
        this(selectedPayload, allowedOrigin, port, System::nanoTime, reason -> {});
    }

    LocalBridge(byte[] selectedPayload, String allowedOrigin, int port,
                LongSupplier clock, Consumer<EndReason> onClosed) throws IOException {
        this(selectedPayload, allowedOrigin, port, clock, onClosed, () -> {});
    }

    LocalBridge(byte[] selectedPayload, String allowedOrigin, int port,
                LongSupplier clock, Consumer<EndReason> onClosed, Runnable onReceived) throws IOException {
        if (selectedPayload.length == 0 || selectedPayload.length > 32 * 1024 * 1024) {
            throw new IllegalArgumentException("Local handoff size is invalid.");
        }
        payload = Arrays.copyOf(selectedPayload, selectedPayload.length);
        origin = allowedOrigin;
        this.clock = clock;
        this.onClosed = onClosed;
        this.onReceived = onReceived;
        handoffId = UUID.randomUUID().toString();
        payloadSha256 = sha256Hex(payload);
        listener = new ServerSocket(port, 8, InetAddress.getByName("127.0.0.1"));
        this.port = listener.getLocalPort();
        listener.setSoTimeout(1000);
        code = String.format(java.util.Locale.ROOT, "%010d", Math.floorMod(RANDOM.nextLong(), 10_000_000_000L));
        expiresAt = clock.getAsLong() + TimeUnit.SECONDS.toNanos(PAIR_SECONDS);
        thread = new Thread(this::serve, "amplifai-local-bridge");
        thread.setDaemon(true);
    }

    public String code() { return code; }
    public int port() { return port; }
    public void start() { thread.start(); }

    private void serve() {
        try {
            while (!closed && clock.getAsLong() < expiresAt) {
                try (Socket socket = listener.accept()) {
                    synchronized (this) {
                        if (closed) break;
                        activeSocket = socket;
                    }
                    socket.setSoTimeout(3000);
                    if (!socket.getInetAddress().isLoopbackAddress()) continue;
                    handle(socket);
                } catch (SocketTimeoutException ignored) {
                    // Recheck the five-minute expiry while idle.
                } catch (IOException ignored) {
                    if (closed) break;
                } finally {
                    synchronized (this) { activeSocket = null; }
                }
            }
        } finally {
            close(clock.getAsLong() >= expiresAt ? EndReason.EXPIRED : EndReason.FAILED);
        }
    }

    private void handle(Socket socket) throws IOException {
        OutputStream output = socket.getOutputStream();
        Request request;
        try {
            request = readRequest(socket.getInputStream());
        } catch (IOException exception) {
            reply(output, 400, false, "{}".getBytes(StandardCharsets.US_ASCII));
            return;
        }
        boolean allowed = ("127.0.0.1:" + port()).equals(request.headers.get("host")) &&
                origin.equals(request.headers.get("origin"));
        if (!allowed) { reply(output, 403, false, empty()); return; }
        boolean savedAcknowledgment = request.path.equals("/v1/acknowledge-save") &&
                (request.method.equals("POST") || request.method.equals("OPTIONS"));
        if (closed || clock.getAsLong() >= expiresAt || (used && !savedAcknowledgment)) {
            reply(output, 410, true, empty()); return;
        }
        if (request.method.equals("OPTIONS")) { preflight(output, request); return; }
        if (request.method.equals("POST") && request.path.equals("/v1/pair")) {
            pair(output, request, socket.getInputStream());
        } else if (request.method.equals("GET") && request.path.matches("/v1/metadata(?:\\?cursor=(0|[1-9][0-9]{0,2}))?")) {
            metadata(output, request);
        } else if (request.method.equals("POST") && request.path.equals("/v1/complete")) {
            complete(output, request);
        } else if (request.method.equals("POST") && request.path.equals("/v1/acknowledge-save")) {
            acknowledgeSave(output, request, socket.getInputStream());
        } else {
            reply(output, 403, true, empty());
        }
    }

    private void preflight(OutputStream output, Request request) throws IOException {
        String expected = request.path.matches("/v1/metadata(?:\\?cursor=(0|[1-9][0-9]{0,2}))?") ? "GET" : "POST";
        if ((!request.path.equals("/v1/pair") && !request.path.equals("/v1/complete") &&
                !request.path.equals("/v1/acknowledge-save") && !expected.equals("GET")) ||
                !expected.equals(request.headers.get("access-control-request-method"))) {
            reply(output, 403, true, empty());
            return;
        }
        String headers = "HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: " + origin +
                "\r\nAccess-Control-Allow-Methods: GET, POST, OPTIONS\r\n" +
                "Access-Control-Allow-Headers: Authorization, Content-Type\r\n" +
                "Access-Control-Allow-Private-Network: true\r\nAccess-Control-Max-Age: 0\r\n" +
                "Cache-Control: no-store\r\nVary: Origin\r\nContent-Length: 0\r\nConnection: close\r\n\r\n";
        output.write(headers.getBytes(StandardCharsets.US_ASCII));
    }

    private void pair(OutputStream output, Request request, InputStream input) throws IOException {
        if (!"application/json".equals(request.headers.get("content-type")) ||
                request.headers.containsKey("transfer-encoding")) {
            reply(output, 415, true, empty()); return;
        }
        int length = contentLength(request);
        if (length < 1 || length > MAX_REQUEST_BYTES) {
            reply(output, length > MAX_REQUEST_BYTES ? 413 : 400, true, empty()); return;
        }
        byte[] body;
        try { body = readExactBody(input, length); }
        catch (EOFException incomplete) { reply(output, 400, true, empty()); return; }
        String command = new String(body, StandardCharsets.US_ASCII);
        if (closed || clock.getAsLong() >= expiresAt) {
            reply(output, 410, true, empty());
            close(EndReason.EXPIRED);
            return;
        }
        if (!command.matches("\\s*\\{\\s*\"code\"\\s*:\\s*\"[0-9]{10}\"\\s*\\}\\s*")) {
            reply(output, 400, true, empty()); return;
        }
        if (token != null) { reply(output, 403, true, empty()); return; }
        if (!constantTimeEquals(code, command.replaceAll("[^0-9]", ""))) {
            attempts++;
            try { reply(output, 403, true, empty()); }
            finally { if (attempts >= MAX_ATTEMPTS) close(EndReason.ATTEMPTS_EXHAUSTED); }
            return;
        }
        if (attempts >= MAX_ATTEMPTS) {
            reply(output, 403, true, empty()); return;
        }
        byte[] random = new byte[32];
        RANDOM.nextBytes(random);
        token = java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(random);
        long remaining = Math.max(1L, TimeUnit.NANOSECONDS.toSeconds(expiresAt - clock.getAsLong()));
        String result = "{\"token\":\"" + token + "\",\"expiresInSeconds\":" + remaining +
                ",\"handoffId\":\"" + handoffId + "\",\"payloadSha256\":\"" + payloadSha256 + "\"}";
        reply(output, 200, true, result.getBytes(StandardCharsets.US_ASCII));
    }

    private void metadata(OutputStream output, Request request) throws IOException {
        if (!authorized(request)) { reply(output, 403, true, empty()); return; }
        int cursor = 0;
        int query = request.path.indexOf("?cursor=");
        if (query >= 0) cursor = Integer.parseInt(request.path.substring(query + 8));
        int pageCount = (payload.length + PAGE_BYTES - 1) / PAGE_BYTES;
        if (cursor >= pageCount || cursor > nextCursor) {
            reply(output, 409, true, empty()); return;
        }
        if (pageCount == 1) {
            used = true;
            deliveredReply(output, payload);
            return;
        }
        int start = cursor * PAGE_BYTES;
        int end = Math.min(start + PAGE_BYTES, payload.length);
        StringBuilder json = new StringBuilder(end - start + 160);
        json.append("{\"format\":\"chunked-json-v1\",\"cursor\":").append(cursor).append(",\"chunk\":");
        NativePayload.quote(json, new String(payload, start, end - start, StandardCharsets.US_ASCII));
        json.append(",\"nextCursor\":");
        if (end == payload.length) json.append("null"); else json.append(cursor + 1);
        json.append(",\"totalBytes\":").append(payload.length).append('}');
        nextCursor = Math.max(nextCursor, cursor + 1);
        reply(output, 200, true, json.toString().getBytes(StandardCharsets.US_ASCII));
    }

    private void complete(OutputStream output, Request request) throws IOException {
        if (!authorized(request)) { reply(output, 403, true, empty()); return; }
        if (request.headers.containsKey("transfer-encoding") || contentLength(request) != 0) {
            reply(output, 400, true, empty()); return;
        }
        if (nextCursor < (payload.length + PAGE_BYTES - 1) / PAGE_BYTES) {
            reply(output, 409, true, empty()); return;
        }
        used = true;
        deliveredReply(output, "{\"completed\":true}".getBytes(StandardCharsets.US_ASCII));
    }

    private void acknowledgeSave(OutputStream output, Request request, InputStream input) throws IOException {
        if (!authorized(request)) { reply(output, 403, true, empty()); return; }
        if (!"application/json".equals(request.headers.get("content-type")) ||
                request.headers.containsKey("transfer-encoding")) {
            reply(output, 415, true, empty()); return;
        }
        int length = contentLength(request);
        if (length < 1 || length > MAX_REQUEST_BYTES) {
            reply(output, length > MAX_REQUEST_BYTES ? 413 : 400, true, empty()); return;
        }
        byte[] body;
        try { body = readExactBody(input, length); }
        catch (EOFException incomplete) { reply(output, 400, true, empty()); return; }
        String command = new String(body, StandardCharsets.US_ASCII);
        Matcher saved = SAVED_ACKNOWLEDGMENT.matcher(command);
        if (!saved.matches()) {
            reply(output, 400, true, empty()); return;
        }
        if (!used || !constantTimeEquals(handoffId, saved.group(1)) ||
                !constantTimeEquals(payloadSha256, saved.group(2))) {
            reply(output, 409, true, empty()); return;
        }
        finishReply(output, "{\"acknowledged\":true}".getBytes(StandardCharsets.US_ASCII));
    }

    private void deliveredReply(OutputStream output, byte[] response) throws IOException {
        try {
            reply(output, 200, true, response);
            Arrays.fill(payload, (byte) 0);
            onReceived.run();
        } catch (IOException interruptedTransfer) {
            close(EndReason.FAILED);
            throw interruptedTransfer;
        }
    }

    private static String sha256Hex(byte[] bytes) {
        byte[] digest;
        try { digest = MessageDigest.getInstance("SHA-256").digest(bytes); }
        catch (NoSuchAlgorithmException missingPlatformPrimitive) { throw new IllegalStateException("SHA-256 is unavailable", missingPlatformPrimitive); }
        char[] hex = new char[digest.length * 2];
        char[] digits = "0123456789abcdef".toCharArray();
        for (int index = 0; index < digest.length; index++) {
            hex[index * 2] = digits[(digest[index] & 0xff) >>> 4];
            hex[index * 2 + 1] = digits[digest[index] & 0x0f];
        }
        return new String(hex);
    }

    private static byte[] readExactBody(InputStream input, int length) throws IOException {
        byte[] body = new byte[length];
        int offset = 0;
        while (offset < length) {
            int count = input.read(body, offset, length - offset);
            if (count < 0) throw new EOFException("Incomplete request body.");
            offset += count;
        }
        return body;
    }

    private void finishReply(OutputStream output, byte[] response) throws IOException {
        try {
            reply(output, 200, true, response);
            close(EndReason.COMPLETED);
        } catch (IOException interruptedTransfer) {
            close(EndReason.FAILED);
            throw interruptedTransfer;
        }
    }

    private boolean authorized(Request request) {
        return token != null && constantTimeEquals("Bearer " + token, request.headers.get("authorization"));
    }

    private static boolean constantTimeEquals(String expected, String actual) {
        if (actual == null) return false;
        return java.security.MessageDigest.isEqual(expected.getBytes(StandardCharsets.US_ASCII),
                actual.getBytes(StandardCharsets.US_ASCII));
    }

    private static int contentLength(Request request) {
        String value = request.headers.get("content-length");
        if (value == null) return 0;
        if (!value.matches("0|[1-9][0-9]{0,3}")) return -1;
        return Integer.parseInt(value);
    }

    private static byte[] empty() { return "{}".getBytes(StandardCharsets.US_ASCII); }

    private void reply(OutputStream output, int status, boolean cors, byte[] body) throws IOException {
        String label = status == 200 ? "OK" : status == 400 ? "Bad Request" : status == 403 ? "Forbidden"
                : status == 409 ? "Conflict" : status == 410 ? "Gone" : status == 413 ? "Payload Too Large" : "Unsupported Media Type";
        String headers = "HTTP/1.1 " + status + " " + label + "\r\nContent-Type: application/json; charset=utf-8\r\n" +
                "Content-Length: " + body.length + "\r\nCache-Control: no-store\r\n" +
                "X-Content-Type-Options: nosniff\r\nConnection: close\r\n" +
                (cors ? "Access-Control-Allow-Origin: " + origin + "\r\nVary: Origin\r\n" : "") + "\r\n";
        output.write(headers.getBytes(StandardCharsets.US_ASCII));
        output.write(body);
        output.flush();
    }

    private static Request readRequest(InputStream input) throws IOException {
        ByteArrayOutputStream header = new ByteArrayOutputStream();
        int state = 0;
        while (header.size() < MAX_HEADER_BYTES) {
            int next = input.read();
            if (next < 0) throw new IOException("Incomplete request headers.");
            header.write(next);
            state = state == 0 && next == '\r' ? 1 : state == 1 && next == '\n' ? 2
                    : state == 2 && next == '\r' ? 3 : state == 3 && next == '\n' ? 4 : 0;
            if (state == 4) break;
        }
        if (state != 4) throw new IOException("Request headers exceed the limit.");
        String[] lines = new String(header.toByteArray(), StandardCharsets.US_ASCII).split("\\r\\n");
        if (lines.length < 1 || lines.length > 32) throw new IOException("Invalid header count.");
        String[] start = lines[0].split(" ");
        if (start.length != 3 || !start[2].equals("HTTP/1.1")) throw new IOException("Invalid request line.");
        Map<String, String> headers = new HashMap<>();
        for (int index = 1; index < lines.length; index++) {
            int separator = lines[index].indexOf(':');
            if (separator < 1) throw new IOException("Invalid header.");
            String name = lines[index].substring(0, separator).toLowerCase(java.util.Locale.ROOT);
            String value = lines[index].substring(separator + 1).trim();
            if (headers.putIfAbsent(name, value) != null) throw new IOException("Duplicate header.");
        }
        return new Request(start[0], start[1], headers);
    }

    private record Request(String method, String path, Map<String, String> headers) {}

    @Override public void close() { close(EndReason.CANCELLED); }

    public void close(EndReason reason) {
        synchronized (this) {
            if (closed) return;
            closed = true;
            try { listener.close(); } catch (IOException ignored) { /* Already closed. */ }
            if (activeSocket != null) {
                try { activeSocket.close(); } catch (IOException ignored) { /* Already closed. */ }
            }
            token = null;
            Arrays.fill(payload, (byte) 0);
        }
        onClosed.accept(reason);
    }
}
