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
    interface PagedSource extends AutoCloseable {
        String manifestJson();
        String manifestSha256();
        int pageCount(String category);
        String pageJson(String category, int cursor);
        @Override void close();
    }
    interface AccountProof {
        Destination claim(String intentId, String capability, String handoffId, String payloadSha256);
        boolean consume(String intentId, String capability, String releaseCode,
                        String handoffId, String payloadSha256);
    }
    record Destination(String intentId, String email, String course) {}
    public static final int PORT = 48751;
    public static final String ACCOUNT_ORIGIN = "https://amplifai-database-engine.vercel.app";
    private static final int MAX_HEADER_BYTES = 8192;
    private static final int MAX_REQUEST_BYTES = 1024;
    private static final int PAGE_BYTES = 128 * 1024;
    private static final int PAIR_SECONDS = 5 * 60;
    public static final long LIFETIME_MILLIS = TimeUnit.SECONDS.toMillis(PAIR_SECONDS);
    public static final long REVIEW_LIFETIME_MILLIS = TimeUnit.HOURS.toMillis(2);
    public enum EndReason { COMPLETED, CANCELLED, EXPIRED, ATTEMPTS_EXHAUSTED, FAILED }
    private static final int MAX_ATTEMPTS = 5;
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final Pattern SAVED_ACKNOWLEDGMENT = Pattern.compile(
            "\\s*\\{\\s*\"handoffId\"\\s*:\\s*\"([0-9a-f-]{36})\"\\s*,\\s*\"payloadSha256\"\\s*:\\s*\"([0-9a-f]{64})\"\\s*,\\s*\"saved\"\\s*:\\s*true\\s*\\}\\s*");
    private static final Pattern RECEIVED_ACKNOWLEDGMENT = Pattern.compile(
            "\\s*\\{\\s*\"handoffId\"\\s*:\\s*\"([0-9a-f-]{36})\"\\s*,\\s*\"payloadSha256\"\\s*:\\s*\"([0-9a-f]{64})\"\\s*,\\s*\"received\"\\s*:\\s*true\\s*\\}\\s*");
    private static final Pattern DECLINE = Pattern.compile(
            "\\s*\\{\\s*\"handoffId\"\\s*:\\s*\"([0-9a-f-]{36})\"\\s*,\\s*\"payloadSha256\"\\s*:\\s*\"([0-9a-f]{64})\"\\s*,\\s*\"category\"\\s*:\\s*\"(contacts|calls|messages)\"\\s*\\}\\s*");
    private static final String UUID_PATTERN = "([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})";
    private static final String CAPABILITY_PATTERN = "([A-Za-z0-9_-]{43})";
    private static final Pattern CONNECT = Pattern.compile("\\s*\\{\\s*\"intentId\"\\s*:\\s*\"" + UUID_PATTERN +
            "\"\\s*,\\s*\"capability\"\\s*:\\s*\"" + CAPABILITY_PATTERN +
            "\"\\s*,\\s*\"handoffId\"\\s*:\\s*\"" + UUID_PATTERN +
            "\"\\s*,\\s*\"payloadSha256\"\\s*:\\s*\"([0-9a-f]{64})\"\\s*\\}\\s*");
    private static final Pattern RELEASE = Pattern.compile("\\s*\\{\\s*\"intentId\"\\s*:\\s*\"" + UUID_PATTERN +
            "\"\\s*,\\s*\"capability\"\\s*:\\s*\"" + CAPABILITY_PATTERN +
            "\"\\s*,\\s*\"releaseCode\"\\s*:\\s*\"" + CAPABILITY_PATTERN + "\"\\s*\\}\\s*");

    private final ServerSocket listener;
    private final byte[] payload;
    private final PagedSource transfer;
    private final String origin;
    private final String code;
    private volatile long expiresAt;
    private final Thread thread;
    private final int port;
    private final LongSupplier clock;
    private final Consumer<EndReason> onClosed;
    private final Runnable onReceived;
    private final Consumer<long[]> onPageProgress;
    private final AccountProof accountProof;
    private final Runnable onDestinationPending;
    private final String handoffId;
    private final String payloadSha256;
    private volatile boolean closed;
    private Socket activeSocket;
    private String token;
    private int attempts;
    private int nextCursor;
    private boolean used;
    private boolean savedAcknowledged;
    private String destinationIntentId;
    private String destinationCapability;
    private String destinationEmail;
    private String destinationCourse;
    private volatile String destinationState;
    private final int[] sourceCursors = new int[3];
    private final String[] sourceDispositions = new String[]{"unresolved", "unresolved", "unresolved"};

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
        transfer = null;
        origin = allowedOrigin;
        this.clock = clock;
        this.onClosed = onClosed;
        this.onReceived = onReceived;
        this.onPageProgress = ignored -> {};
        accountProof = null;
        onDestinationPending = () -> {};
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

    LocalBridge(PagedSource reviewed, String allowedOrigin, int port, LongSupplier clock,
                Consumer<EndReason> onClosed, Runnable onReceived) throws IOException {
        this(reviewed, allowedOrigin, port, clock, onClosed, onReceived, ignored -> {});
    }

    LocalBridge(PagedSource reviewed, String allowedOrigin, int port, LongSupplier clock,
                Consumer<EndReason> onClosed, Runnable onReceived, Consumer<long[]> onPageProgress) throws IOException {
        this(reviewed, allowedOrigin, port, clock, onClosed, onReceived, onPageProgress, null, () -> {});
    }

    LocalBridge(PagedSource reviewed, String allowedOrigin, int port, LongSupplier clock,
                Consumer<EndReason> onClosed, Runnable onReceived, Consumer<long[]> onPageProgress,
                AccountProof accountProof, Runnable onDestinationPending) throws IOException {
        transfer = reviewed;
        payload = new byte[0];
        origin = allowedOrigin;
        this.clock = clock;
        this.onClosed = onClosed;
        this.onReceived = onReceived;
        this.onPageProgress = onPageProgress;
        this.accountProof = accountProof;
        this.onDestinationPending = onDestinationPending;
        handoffId = UUID.randomUUID().toString();
        payloadSha256 = reviewed.manifestSha256();
        listener = new ServerSocket(port, 8, InetAddress.getByName("127.0.0.1"));
        this.port = listener.getLocalPort();
        listener.setSoTimeout(1000);
        code = String.format(java.util.Locale.ROOT, "%010d", Math.floorMod(RANDOM.nextLong(), 10_000_000_000L));
        expiresAt = clock.getAsLong() + TimeUnit.SECONDS.toNanos(PAIR_SECONDS);
        for (int index = 0; index < 3; index++) {
            if (reviewed.pageCount(sourceCategory(index)) == 0) sourceDispositions[index] = "delivered";
        }
        thread = new Thread(this::serve, "amplifai-paged-bridge");
        thread.setDaemon(true);
    }

    public String code() { return code; }
    public int port() { return port; }
    long remainingMillis() { return Math.max(0L, TimeUnit.NANOSECONDS.toMillis(expiresAt - clock.getAsLong())); }
    public void start() { thread.start(); }

    synchronized Destination pendingDestination() {
        return "pending".equals(destinationState)
                ? new Destination(destinationIntentId, destinationEmail, destinationCourse) : null;
    }

    synchronized boolean decideDestination(String intentId, boolean approved) {
        if (closed || clock.getAsLong() >= expiresAt || !"pending".equals(destinationState) ||
                !constantTimeEquals(destinationIntentId, intentId)) return false;
        destinationState = approved ? "approved" : "denied";
        return true;
    }

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
                    // Recheck the active pairing or post-delivery expiry while idle.
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
        boolean savedAcknowledgment = (request.path.equals("/v1/acknowledge-save") ||
                request.path.equals("/v1/confirm-ack-received") || (transfer != null && request.path.equals("/v1/complete"))) &&
                (request.method.equals("POST") || request.method.equals("OPTIONS"));
        if (closed || clock.getAsLong() >= expiresAt || (used && !savedAcknowledgment &&
                !(transfer != null && request.path.equals("/v2/keepalive")))) {
            reply(output, 410, true, empty()); return;
        }
        if (request.method.equals("OPTIONS")) { preflight(output, request); return; }
        if (accountProof != null && transfer != null && request.method.equals("GET") && request.path.equals("/v3/discover")) {
            discover(output, request);
        } else if (accountProof != null && transfer != null && request.method.equals("POST") && request.path.equals("/v3/connect")) {
            connectAccount(output, request, socket.getInputStream());
        } else if (accountProof != null && transfer != null && request.method.equals("GET") && request.path.equals("/v3/status")) {
            destinationStatus(output, request);
        } else if (accountProof != null && transfer != null && request.method.equals("POST") && request.path.equals("/v3/release")) {
            releaseAccount(output, request, socket.getInputStream());
        } else if (request.method.equals("POST") && request.path.equals("/v1/pair")) {
            pair(output, request, socket.getInputStream());
        } else if (request.method.equals("GET") && request.path.matches("/v1/metadata(?:\\?cursor=(0|[1-9][0-9]{0,2}))?")) {
            metadata(output, request);
        } else if (request.method.equals("POST") && request.path.equals("/v1/complete")) {
            complete(output, request);
        } else if (request.method.equals("POST") && request.path.equals("/v1/acknowledge-save")) {
            acknowledgeSave(output, request, socket.getInputStream());
        } else if (transfer != null && request.method.equals("POST") && request.path.equals("/v1/confirm-ack-received")) {
            confirmAckReceived(output, request, socket.getInputStream());
        } else if (transfer != null && request.method.equals("POST") && request.path.equals("/v2/keepalive")) {
            keepalive(output, request);
        } else if (transfer != null && request.method.equals("POST") && request.path.equals("/v2/decline")) {
            decline(output, request, socket.getInputStream());
        } else if (transfer != null && request.method.equals("GET") && request.path.matches("/v2/source/(contacts|calls|messages)\\?cursor=(0|[1-9][0-9]{0,9})")) {
            sourcePage(output, request);
        } else {
            reply(output, 403, true, empty());
        }
    }

    private void preflight(OutputStream output, Request request) throws IOException {
        if (request.path.startsWith("/v3/") && (accountProof == null || transfer == null)) {
            reply(output, 403, true, empty()); return;
        }
        String expected = request.path.matches("/v1/metadata(?:\\?cursor=(0|[1-9][0-9]{0,2}))?|/v2/source/(contacts|calls|messages)\\?cursor=(0|[1-9][0-9]{0,9})|/v3/(discover|status)") ? "GET" : "POST";
        if ((!request.path.equals("/v1/pair") && !request.path.equals("/v1/complete") &&
                !request.path.equals("/v1/acknowledge-save") &&
                !(accountProof != null && transfer != null &&
                        (request.path.equals("/v3/connect") || request.path.equals("/v3/release"))) &&
                !(transfer != null && (request.path.equals("/v1/confirm-ack-received") ||
                        request.path.equals("/v2/keepalive") || request.path.equals("/v2/decline"))) && !expected.equals("GET")) ||
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

    private void discover(OutputStream output, Request request) throws IOException {
        if (contentLength(request) != 0) { reply(output, 400, true, empty()); return; }
        if (token != null || destinationState != null) { reply(output, 410, true, empty()); return; }
        long remaining = Math.max(1L, TimeUnit.NANOSECONDS.toSeconds(expiresAt - clock.getAsLong()));
        String body = "{\"format\":\"account-connect-v1\",\"handoffId\":\"" + handoffId +
                "\",\"payloadSha256\":\"" + payloadSha256 + "\",\"expiresInSeconds\":" + remaining + "}";
        reply(output, 200, true, body.getBytes(StandardCharsets.US_ASCII));
    }

    private void connectAccount(OutputStream output, Request request, InputStream input) throws IOException {
        String body = command(request, input);
        Matcher match = body == null ? null : CONNECT.matcher(body);
        if (match == null || !match.matches()) { reply(output, 400, true, empty()); return; }
        if (destinationState != null || token != null || !constantTimeEquals(handoffId, match.group(3)) ||
                !constantTimeEquals(payloadSha256, match.group(4))) {
            reply(output, 409, true, empty()); return;
        }
        Destination destination = accountProof.claim(match.group(1), match.group(2), handoffId, payloadSha256);
        if (closed || clock.getAsLong() >= expiresAt) { reply(output, 410, true, empty()); return; }
        if (destination == null || !constantTimeEquals(match.group(1), destination.intentId()) ||
                !validDestination(destination.email(), 254) || !validDestination(destination.course(), 160) ||
                !destination.email().contains("@")) { reply(output, 409, true, empty()); return; }
        destinationIntentId = match.group(1);
        destinationCapability = match.group(2);
        destinationEmail = destination.email();
        destinationCourse = destination.course();
        destinationState = "pending";
        onDestinationPending.run();
        reply(output, 200, true, "{\"pending\":true}".getBytes(StandardCharsets.US_ASCII));
    }

    private void destinationStatus(OutputStream output, Request request) throws IOException {
        if (destinationState == null || !constantTimeEquals("Bearer " + destinationCapability,
                request.headers.get("authorization"))) { reply(output, 403, true, empty()); return; }
        reply(output, 200, true, ("{\"state\":\"" + destinationState + "\"}").getBytes(StandardCharsets.US_ASCII));
    }

    private void releaseAccount(OutputStream output, Request request, InputStream input) throws IOException {
        String body = command(request, input);
        Matcher match = body == null ? null : RELEASE.matcher(body);
        if (match == null || !match.matches()) { reply(output, 400, true, empty()); return; }
        if (!"approved".equals(destinationState) || token != null ||
                !constantTimeEquals(destinationIntentId, match.group(1)) ||
                !constantTimeEquals(destinationCapability, match.group(2))) {
            reply(output, 403, true, empty()); return;
        }
        if (!accountProof.consume(match.group(1), match.group(2), match.group(3), handoffId, payloadSha256)) {
            reply(output, 409, true, empty()); return;
        }
        if (closed || clock.getAsLong() >= expiresAt) { reply(output, 410, true, empty()); return; }
        issueToken();
        destinationState = "released";
        reply(output, 200, true, pairResponse().getBytes(StandardCharsets.US_ASCII));
        renew();
    }

    private static boolean validDestination(String text, int maxLength) {
        if (text == null || text.isBlank() || text.length() > maxLength) return false;
        for (int index = 0; index < text.length(); index++) if (Character.isISOControl(text.charAt(index))) return false;
        return true;
    }

    private void issueToken() {
        byte[] random = new byte[32];
        RANDOM.nextBytes(random);
        token = java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(random);
    }

    private String pairResponse() {
        long remaining = Math.max(1L, TimeUnit.NANOSECONDS.toSeconds(expiresAt - clock.getAsLong()));
        String result = "{\"token\":\"" + token + "\",\"expiresInSeconds\":" + remaining +
                ",\"handoffId\":\"" + handoffId + "\",\"payloadSha256\":\"" + payloadSha256 + "\"";
        if (transfer == null) return result + "}";
        StringBuilder paged = new StringBuilder(result).append(",\"confirmReceiptRequired\":true,\"format\":\"paged-records-v2\",\"manifestJson\":");
        NativePayload.quote(paged, transfer.manifestJson());
        return paged.append('}').toString();
    }

    private void pair(OutputStream output, Request request, InputStream input) throws IOException {
        if (destinationState != null) { reply(output, 409, true, empty()); return; }
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
        issueToken();
        reply(output, 200, true, pairResponse().getBytes(StandardCharsets.US_ASCII));
        if (transfer != null) renew();
    }

    private void metadata(OutputStream output, Request request) throws IOException {
        if (transfer != null) { reply(output, 403, true, empty()); return; }
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
        if (transfer != null) {
            for (String disposition : sourceDispositions) {
                if (disposition.equals("unresolved")) { reply(output, 409, true, empty()); return; }
            }
            boolean first = !used;
            String response = "{\"completed\":true,\"sources\":{\"contacts\":\"" + sourceDispositions[0] +
                    "\",\"calls\":\"" + sourceDispositions[1] + "\",\"messages\":\"" + sourceDispositions[2] + "\"}}";
            reply(output, 200, true, response.getBytes(StandardCharsets.US_ASCII));
            used = true;
            renew();
            if (first) onReceived.run();
            return;
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
        if (transfer != null) {
            savedAcknowledged = true;
            reply(output, 200, true, "{\"acknowledged\":true}".getBytes(StandardCharsets.US_ASCII));
            renew();
        } else finishReply(output, "{\"acknowledged\":true}".getBytes(StandardCharsets.US_ASCII));
    }

    private void confirmAckReceived(OutputStream output, Request request, InputStream input) throws IOException {
        if (!authorized(request)) { reply(output, 403, true, empty()); return; }
        String command = command(request, input);
        if (command == null) { reply(output, 400, true, empty()); return; }
        Matcher received = RECEIVED_ACKNOWLEDGMENT.matcher(command);
        if (!received.matches()) { reply(output, 400, true, empty()); return; }
        if (!used || !savedAcknowledged || !constantTimeEquals(handoffId, received.group(1)) ||
                !constantTimeEquals(payloadSha256, received.group(2))) {
            reply(output, 409, true, empty()); return;
        }
        // A lost response leaves this exact saved assertion retryable until expiry.
        reply(output, 200, true, "{\"received\":true}".getBytes(StandardCharsets.US_ASCII));
        close(EndReason.COMPLETED);
    }

    private void keepalive(OutputStream output, Request request) throws IOException {
        if (!authorized(request)) { reply(output, 403, true, empty()); return; }
        if (savedAcknowledged || request.headers.containsKey("transfer-encoding") || contentLength(request) != 0) {
            reply(output, 409, true, empty()); return;
        }
        renew();
        reply(output, 200, true, "{\"active\":true}".getBytes(StandardCharsets.US_ASCII));
    }

    private void decline(OutputStream output, Request request, InputStream input) throws IOException {
        if (!authorized(request)) { reply(output, 403, true, empty()); return; }
        String command = command(request, input);
        if (command == null) { reply(output, 400, true, empty()); return; }
        Matcher declined = DECLINE.matcher(command);
        if (!declined.matches()) { reply(output, 400, true, empty()); return; }
        if (!constantTimeEquals(handoffId, declined.group(1)) ||
                !constantTimeEquals(payloadSha256, declined.group(2))) { reply(output, 409, true, empty()); return; }
        int index = sourceIndex(declined.group(3));
        if (sourceDispositions[index].equals("declined")) {
            renew(); reply(output, 200, true, "{\"declined\":true}".getBytes(StandardCharsets.US_ASCII)); return;
        }
        if (used || sourceCursors[index] != 0) { reply(output, 409, true, empty()); return; }
        sourceDispositions[index] = "declined";
        renew();
        reply(output, 200, true, "{\"declined\":true}".getBytes(StandardCharsets.US_ASCII));
    }

    private void sourcePage(OutputStream output, Request request) throws IOException {
        if (!authorized(request)) { reply(output, 403, true, empty()); return; }
        Matcher path = Pattern.compile("/v2/source/(contacts|calls|messages)\\?cursor=(0|[1-9][0-9]{0,9})").matcher(request.path);
        if (!path.matches()) { reply(output, 400, true, empty()); return; }
        String category = path.group(1);
        int cursor;
        try { cursor = Integer.parseInt(path.group(2)); }
        catch (NumberFormatException invalid) { reply(output, 409, true, empty()); return; }
        int index = sourceIndex(category);
        if (used || sourceDispositions[index].equals("declined")) { reply(output, 410, true, empty()); return; }
        if (cursor > sourceCursors[index]) { reply(output, 409, true, empty()); return; }
        String page = transfer.pageJson(category, cursor);
        if (page == null) { reply(output, 409, true, empty()); return; }
        reply(output, 200, true, page.getBytes(StandardCharsets.US_ASCII));
        sourceCursors[index] = Math.max(sourceCursors[index], cursor + 1);
        if (sourceCursors[index] == transfer.pageCount(category)) sourceDispositions[index] = "delivered";
        long delivered = (long) sourceCursors[0] + sourceCursors[1] + sourceCursors[2];
        if (delivered % 25 == 0 || sourceCursors[index] == transfer.pageCount(category)) {
            long total = (long) transfer.pageCount("contacts") + transfer.pageCount("calls") + transfer.pageCount("messages");
            onPageProgress.accept(new long[]{delivered, total});
        }
        renew();
    }

    private String command(Request request, InputStream input) throws IOException {
        if (!"application/json".equals(request.headers.get("content-type")) || request.headers.containsKey("transfer-encoding")) return null;
        int length = contentLength(request);
        if (length < 1 || length > MAX_REQUEST_BYTES) return null;
        try { return new String(readExactBody(input, length), StandardCharsets.US_ASCII); }
        catch (EOFException incomplete) { return null; }
    }

    private void renew() { expiresAt = clock.getAsLong() + TimeUnit.MILLISECONDS.toNanos(REVIEW_LIFETIME_MILLIS); }
    private static int sourceIndex(String category) {
        return switch (category) { case "contacts" -> 0; case "calls" -> 1; case "messages" -> 2;
            default -> throw new IllegalArgumentException("invalid_category"); };
    }
    private static String sourceCategory(int index) { return new String[]{"contacts", "calls", "messages"}[index]; }

    private void deliveredReply(OutputStream output, byte[] response) throws IOException {
        try {
            reply(output, 200, true, response);
            Arrays.fill(payload, (byte) 0);
            expiresAt = clock.getAsLong() + TimeUnit.MILLISECONDS.toNanos(REVIEW_LIFETIME_MILLIS);
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
            destinationState = null;
            destinationCapability = null;
            destinationEmail = null;
            destinationCourse = null;
            Arrays.fill(payload, (byte) 0);
            try { if (transfer != null) transfer.close(); }
            catch (RuntimeException ignored) { /* Release the listener and report closure even if scratch cleanup fails. */ }
        }
        onClosed.accept(reason);
    }
}
