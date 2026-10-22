package ai.satoris.amplifai.phone;

import org.junit.Test;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import static org.junit.Assert.*;

public final class SetupDiscoveryTest {
    private static String send(SetupDiscovery server, String host, String origin, String method, String path, String extra) throws Exception {
        try (Socket socket = new Socket("127.0.0.1", server.port())) {
            socket.setSoTimeout(3000);
            String request = method + " " + path + " HTTP/1.1\r\nHost: " + host + "\r\nOrigin: " + origin + "\r\n" + extra + "\r\n";
            socket.getOutputStream().write(request.getBytes(StandardCharsets.US_ASCII));
            return new String(socket.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        }
    }
    @Test public void routingOnlyRequiresExactOriginHostPathAndReadOnlyMethod() throws Exception {
        NativeSetupContract.Client client = new NativeSetupContract.Client("2026.10.21-test", "26102101");
        try (SetupDiscovery server = new SetupDiscovery(client, 0)) {
            String host = "127.0.0.1:" + server.port(), origin = LocalBridge.ACCOUNT_ORIGIN;
            String reply = send(server, host, origin, "GET", "/v1/setup-capabilities", "");
            assertTrue(reply.startsWith("HTTP/1.1 200"));
            assertTrue(reply.endsWith(NativeSetupContract.capabilities(client)));
            assertTrue(reply.contains("X-Content-Type-Options: nosniff"));
            assertFalse(reply.contains("capability\"")); assertFalse(reply.contains("ownerId"));
            assertTrue(send(server, host, origin, "OPTIONS", "/v1/setup-capabilities", "Access-Control-Request-Method: GET\r\n")
                    .startsWith("HTTP/1.1 204"));
            assertTrue(send(server, host, origin, "POST", "/v1/setup-capabilities", "").startsWith("HTTP/1.1 403"));
            assertTrue(send(server, host, origin, "GET", "/v1/setup-capabilities?setup=anything", "").startsWith("HTTP/1.1 403"));
            assertTrue(send(server, "localhost:" + server.port(), origin, "GET", "/v1/setup-capabilities", "").startsWith("HTTP/1.1 403"));
            String denied = send(server, host, "https://other.example", "GET", "/v1/setup-capabilities", "");
            assertTrue(denied.startsWith("HTTP/1.1 403")); assertFalse(denied.contains("Access-Control-Allow-Origin"));
            assertEquals("", send(server, host, origin, "GET", "/v1/setup-capabilities", "Origin: " + origin + "\r\n"));
            assertTrue(send(server, host, origin, "GET", "/v1/setup-capabilities", "Content-Length: 1\r\n").startsWith("HTTP/1.1 403"));
        }
    }

    @Test public void dribblingHeaderCannotOccupyWorkerPastAbsoluteDeadline() throws Exception {
        try (SetupDiscovery server = new SetupDiscovery(new NativeSetupContract.Client("test", "1"), 0);
             Socket slow = new Socket("127.0.0.1", server.port())) {
            slow.setSoTimeout(3500);
            Thread sender = new Thread(() -> {
                try {
                    for (int i = 0; i < 12; i++) {
                        slow.getOutputStream().write('G'); slow.getOutputStream().flush(); Thread.sleep(300);
                    }
                } catch (java.io.IOException closed) { /* The bounded server closes this incomplete header. */ }
                catch (InterruptedException stopped) { Thread.currentThread().interrupt(); }
            });
            sender.start();
            try { assertEquals(-1, slow.getInputStream().read()); }
            finally { sender.interrupt(); sender.join(1000); }
            assertTrue(send(server, "127.0.0.1:" + server.port(), LocalBridge.ACCOUNT_ORIGIN,
                    "GET", "/v1/setup-capabilities", "").startsWith("HTTP/1.1 200"));
        }
    }
}
