package ai.satoris.amplifai.phone;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.URL;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.function.BooleanSupplier;

/** Bounded anonymous reads; neither request contains a phone or account identifier. */
final class UpdateTransport {
    private static final int CONNECT_TIMEOUT_MS = 8_000;
    private static final int READ_TIMEOUT_MS = 12_000;
    private static final long TOTAL_TIMEOUT_MS = 120_000;

    private UpdateTransport() {}

    static byte[] feed(BooleanSupplier cancelled) throws IOException {
        HttpURLConnection connection = connection(new URL(AndroidUpdateFeed.URL));
        try {
            if (connection.getResponseCode() != 200 ||
                    connection.getContentLengthLong() > AndroidUpdateFeed.MAX_FEED_BYTES)
                throw new IOException("feed_status_or_size");
            long deadline = System.nanoTime() + 30_000L * 1_000_000L;
            try (InputStream input = connection.getInputStream()) {
            ByteArrayOutputStream result = new ByteArrayOutputStream();
            byte[] block = new byte[1024];
            int count;
            while ((count = input.read(block)) != -1) {
                checkCancelled(cancelled);
                if (System.nanoTime() > deadline) throw new IOException("feed_timeout");
                if (result.size() + count > AndroidUpdateFeed.MAX_FEED_BYTES) throw new IOException("feed_size");
                result.write(block, 0, count);
            }
            return result.toByteArray();
            }
        } finally { connection.disconnect(); }
    }

    static File apk(AndroidUpdateFeed.Release release, File privateDirectory,
                    BooleanSupplier cancelled) throws IOException {
        File file = File.createTempFile("android-update-", ".apk", privateDirectory);
        boolean success = false;
        try {
            URL current = new URL(release.url());
            long deadline = System.nanoTime() + TOTAL_TIMEOUT_MS * 1_000_000;
            for (int hop = 0; hop < 3; hop++) {
                checkCancelled(cancelled);
                HttpURLConnection connection = connection(current);
                try {
                    int status = connection.getResponseCode();
                    if (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
                        URL next = new URL(connection.getHeaderField("Location"));
                        if (!allowedRedirect(current, next, hop)) throw new IOException("apk_redirect");
                        current = next;
                        continue;
                    }
                    if (status != 200) throw new IOException("apk_status");
                    long declared = connection.getContentLengthLong();
                    if (declared >= 0 && declared != release.bytes()) throw new IOException("apk_length");
                    try (InputStream input = connection.getInputStream(); FileOutputStream output = new FileOutputStream(file)) {
                        copyVerified(input, output, release.bytes(), release.sha256(), cancelled, deadline);
                        output.getFD().sync();
                    }
                    success = true;
                    return file;
                } finally { connection.disconnect(); }
            }
            throw new IOException("apk_redirect_limit");
        } finally {
            // The archive contains only public release bytes. Startup cleanup retries an
            // unlink failure; it must not conceal the integrity or cancellation error.
            if (!success) file.delete();
        }
    }

    static void copyVerified(InputStream input, OutputStream output, long expectedBytes, String expectedSha256,
                             BooleanSupplier cancelled, long deadlineNanos) throws IOException {
        MessageDigest digest = sha256();
        long received = 0;
        byte[] block = new byte[8192];
        int count;
        while ((count = input.read(block)) != -1) {
            checkCancelled(cancelled);
            if (System.nanoTime() > deadlineNanos) throw new IOException("apk_timeout");
            received += count;
            if (received > expectedBytes) throw new IOException("apk_length");
            digest.update(block, 0, count);
            output.write(block, 0, count);
        }
        if (received != expectedBytes || !AndroidUpdateFeed.hex(digest.digest()).equals(expectedSha256))
            throw new IOException("apk_integrity");
    }

    private static HttpURLConnection connection(URL url) throws IOException {
        if (!"https".equals(url.getProtocol()) || url.getUserInfo() != null) throw new IOException("update_url");
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(CONNECT_TIMEOUT_MS);
        connection.setReadTimeout(READ_TIMEOUT_MS);
        connection.setRequestProperty("Accept-Encoding", "identity");
        connection.setUseCaches(false);
        return connection;
    }

    static boolean allowedRedirect(URL from, URL to, int hop) {
        try {
            URI target = to.toURI();
            return hop == 0 && "https".equals(to.getProtocol()) && to.getPort() == -1 &&
                    "github.com".equals(from.getHost()) &&
                    "release-assets.githubusercontent.com".equals(to.getHost()) &&
                    target.getRawUserInfo() == null && target.getRawFragment() == null &&
                    target.getRawPath() != null && target.getRawPath().length() <= 1024 &&
                    (target.getRawQuery() == null || target.getRawQuery().length() <= 4096);
        } catch (Exception invalid) { return false; }
    }

    private static MessageDigest sha256() {
        try { return MessageDigest.getInstance("SHA-256"); }
        catch (NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
    }

    private static void checkCancelled(BooleanSupplier cancelled) throws IOException {
        if (cancelled.getAsBoolean() || Thread.currentThread().isInterrupted()) throw new IOException("update_cancelled");
    }
}
