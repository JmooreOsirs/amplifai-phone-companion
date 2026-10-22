package ai.satoris.amplifai.phone;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/** Read-only routing information after explicit launch. Never holds account or collection authority. */
final class SetupDiscovery implements AutoCloseable {
    static final int PORT = 48752;
    private final ServerSocket listener;
    private final byte[] capabilities;
    private volatile boolean closed;
    private Socket active;

    SetupDiscovery(NativeSetupContract.Client client, int port) throws IOException {
        capabilities = NativeSetupContract.capabilities(client).getBytes(StandardCharsets.UTF_8);
        listener = new ServerSocket(port, 4, InetAddress.getByName("127.0.0.1"));
        Thread worker = new Thread(this::serve, "amplifai-setup-discovery");
        worker.setDaemon(true); worker.start();
    }
    int port() { return listener.getLocalPort(); }
    private void serve() {
        while (!closed) {
            try (Socket socket = listener.accept()) {
                synchronized (this) { if (closed) return; active = socket; }
                respond(socket);
            } catch (IOException unavailable) {
                // A malformed/timed-out request cannot affect any setup or collection state.
            } finally { synchronized (this) { active = null; } }
        }
    }
    private void respond(Socket socket) throws IOException {
        ByteArrayOutputStream input = new ByteArrayOutputStream();
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(2);
        int end = 0;
        while (input.size() < 4096 && end != 0x0d0a0d0a) {
            long remaining = deadline - System.nanoTime();
            if (remaining <= 0) return;
            socket.setSoTimeout((int) Math.max(1, TimeUnit.NANOSECONDS.toMillis(remaining)));
            int next = socket.getInputStream().read();
            if (next < 0 || next > 127 || next == 0) return;
            input.write(next); end = (end << 8) | next;
        }
        if (end != 0x0d0a0d0a) return;
        String[] lines = input.toString(StandardCharsets.US_ASCII.name()).split("\r\n");
        String[] request = lines[0].split(" ", -1);
        Map<String, String> headers = new HashMap<>();
        for (int i = 1; i < lines.length; i++) {
            int colon = lines[i].indexOf(':');
            if (colon <= 0 || Character.isWhitespace(lines[i].charAt(0))) return;
            String key = lines[i].substring(0, colon).toLowerCase(Locale.ROOT);
            if (!key.matches("[a-z0-9-]+") || headers.put(key, lines[i].substring(colon + 1).trim()) != null) return;
        }
        boolean origin = LocalBridge.ACCOUNT_ORIGIN.equals(headers.get("origin"));
        boolean accepted = request.length == 3 && "HTTP/1.1".equals(request[2]) &&
                "/v1/setup-capabilities".equals(request[1]) && origin &&
                ("127.0.0.1:" + port()).equals(headers.get("host")) &&
                !headers.containsKey("transfer-encoding") &&
                (!headers.containsKey("content-length") || "0".equals(headers.get("content-length")));
        boolean preflight = accepted && "OPTIONS".equals(request[0]) && "GET".equals(headers.get("access-control-request-method")) &&
                !headers.containsKey("access-control-request-headers");
        boolean get = accepted && "GET".equals(request[0]);
        int status = get ? 200 : preflight ? 204 : 403;
        byte[] body = get ? capabilities : new byte[0];
        String response = "HTTP/1.1 " + status + (get ? " OK" : preflight ? " No Content" : " Forbidden") +
                "\r\nContent-Type: application/json\r\nX-Content-Type-Options: nosniff\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: " + body.length +
                (accepted ? "\r\nAccess-Control-Allow-Origin: " + LocalBridge.ACCOUNT_ORIGIN + "\r\nVary: Origin" : "") +
                (preflight ? "\r\nAccess-Control-Allow-Methods: GET\r\nAccess-Control-Allow-Private-Network: true\r\nAccess-Control-Max-Age: 0" : "") + "\r\n\r\n";
        socket.getOutputStream().write(response.getBytes(StandardCharsets.US_ASCII));
        socket.getOutputStream().write(body);
    }
    @Override public synchronized void close() {
        closed = true;
        try { listener.close(); } catch (IOException ignored) { /* Already closed. */ }
        try { if (active != null) active.close(); } catch (IOException ignored) { /* Already closed. */ }
    }
}
