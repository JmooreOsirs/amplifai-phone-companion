package ai.satoris.amplifai.phone;

import org.junit.Test;

import java.net.URL;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;

import static org.junit.Assert.*;

public final class AndroidUpdateFeedTest {
    private static byte[] signedFeed() throws Exception {
        try (var input = AndroidUpdateFeedTest.class.getResourceAsStream("/android-update-rc7-signed.txt")) {
            assertNotNull(input);
            return input.readAllBytes();
        }
    }

    private static byte[] certificate() throws Exception {
        return Files.readAllBytes(Path.of("src/website/assets/android_update_publisher.der"));
    }

    private static void rejected(byte[] feed, byte[] certificate) {
        assertThrows(IllegalArgumentException.class, () -> AndroidUpdateFeed.parse(feed, certificate));
    }

    @Test public void releasedPublisherFeedAndVersionState() throws Exception {
        AndroidUpdateFeed.Release rc7 = AndroidUpdateFeed.parse(signedFeed(), certificate());
        assertEquals(26100807, rc7.versionCode());
        assertEquals(AndroidUpdateFeed.Decision.CURRENT, AndroidUpdateFeed.decide(rc7, 26100807, 36));
        assertEquals(AndroidUpdateFeed.Decision.AVAILABLE, AndroidUpdateFeed.decide(rc7, 26100806, 36));
        assertEquals(AndroidUpdateFeed.Decision.INCOMPATIBLE, AndroidUpdateFeed.decide(rc7, 26100806, 25));
    }

    @Test public void mutatedDigestUrlVersionSignatureAndShapeFailClosed() throws Exception {
        String original = new String(signedFeed(), StandardCharsets.US_ASCII);
        for (String candidate : new String[]{
                original.replace("e4ce5ad5", "f4ce5ad5"),
                original.replace("github.com", "example.com"),
                original.replace("26100807", "26100808"),
                original.replace("signature=", "signature=A"),
                original + "ignored=1\n",
                original.replace("\n", "\r\n")
        }) rejected(candidate.getBytes(StandardCharsets.US_ASCII), certificate());
        byte[] alteredCertificate = certificate();
        alteredCertificate[10] ^= 1;
        rejected(signedFeed(), alteredCertificate);
    }

    @Test public void updateRedirectOnlyToGithubReleaseAssetHost() throws Exception {
        URL source = new URL("https://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/android-2026.10.08-rc7/app.apk");
        assertTrue(UpdateTransport.allowedRedirect(source,
                new URL("https://release-assets.githubusercontent.com/123/asset?token=bounded"), 0));
        assertFalse(UpdateTransport.allowedRedirect(source, new URL("http://release-assets.githubusercontent.com/123"), 0));
        assertFalse(UpdateTransport.allowedRedirect(source, new URL("https://example.com/123"), 0));
        assertFalse(UpdateTransport.allowedRedirect(source, new URL("https://release-assets.githubusercontent.com/123"), 1));
    }

    @Test public void exactDownloadedBytesRejectTruncationCorruptionOverflowAndCancel() throws Exception {
        byte[] source = "signed synthetic APK bytes".getBytes(StandardCharsets.US_ASCII);
        String digest = AndroidUpdateFeed.hex(MessageDigest.getInstance("SHA-256").digest(source));
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        UpdateTransport.copyVerified(new ByteArrayInputStream(source), output, source.length, digest,
                () -> false, System.nanoTime() + 1_000_000_000L);
        assertArrayEquals(source, output.toByteArray());
        assertThrows(IOException.class, () -> UpdateTransport.copyVerified(
                new ByteArrayInputStream(source), new ByteArrayOutputStream(), source.length + 1, digest,
                () -> false, Long.MAX_VALUE));
        assertThrows(IOException.class, () -> UpdateTransport.copyVerified(
                new ByteArrayInputStream(source), new ByteArrayOutputStream(), source.length - 1, digest,
                () -> false, Long.MAX_VALUE));
        assertThrows(IOException.class, () -> UpdateTransport.copyVerified(
                new ByteArrayInputStream("altered synthetic APK bytes".getBytes(StandardCharsets.US_ASCII)),
                new ByteArrayOutputStream(), source.length, digest, () -> false, Long.MAX_VALUE));
        assertThrows(IOException.class, () -> UpdateTransport.copyVerified(
                new ByteArrayInputStream(source), new ByteArrayOutputStream(), source.length, digest,
                () -> true, Long.MAX_VALUE));
    }
}
