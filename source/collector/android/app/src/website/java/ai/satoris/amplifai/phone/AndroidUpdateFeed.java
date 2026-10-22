package ai.satoris.amplifai.phone;

import java.io.ByteArrayInputStream;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.Signature;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.Base64;

/** The release signing key authenticates the exact package bytes named by this feed. */
final class AndroidUpdateFeed {
    static final String URL = "https://amplifai-database-engine.vercel.app/phone/android-update-v1.txt";
    static final String PACKAGE = "ai.satoris.amplifai.phone";
    static final String CERT_SHA256 = "9f3fa36a8f3b44b8083080828d772cd5990552275a6ffbc68a097826e4e3c4c2";
    static final int MAX_FEED_BYTES = 4096;
    static final long MAX_APK_BYTES = 32L * 1024L * 1024L;

    record Release(int versionCode, String versionName, int minSdk, long bytes,
                   String sha256, String url) {}

    enum Decision { CURRENT, AVAILABLE, INCOMPATIBLE }

    private AndroidUpdateFeed() {}

    static Release parse(byte[] wire, byte[] pinnedCertificate) {
        if (wire.length == 0 || wire.length > MAX_FEED_BYTES) throw new IllegalArgumentException("feed_size");
        for (byte value : wire) if (value < 0x20 && value != '\n' || value > 0x7e)
            throw new IllegalArgumentException("feed_ascii");
        String text = new String(wire, StandardCharsets.US_ASCII);
        String[] lines = text.split("\n", -1);
        if (lines.length != 11 || !lines[10].isEmpty() ||
                !lines[0].equals("AMPLIFAI_ANDROID_UPDATE_V1")) throw new IllegalArgumentException("feed_shape");
        String pkg = field(lines[1], "package");
        String codeText = field(lines[2], "versionCode");
        String name = field(lines[3], "versionName");
        String minText = field(lines[4], "minSdk");
        String bytesText = field(lines[5], "apkBytes");
        String digest = field(lines[6], "apkSha256");
        String cert = field(lines[7], "signerSha256");
        String url = field(lines[8], "apkUrl");
        String signatureText = field(lines[9], "signature");
        if (!PACKAGE.equals(pkg) || !CERT_SHA256.equals(cert) ||
                !codeText.matches("[1-9][0-9]{0,8}") ||
                !name.matches("20[0-9]{2}\\.[0-9]{2}\\.[0-9]{2}-rc[1-9][0-9]{0,2}") ||
                !minText.matches("[1-9][0-9]{0,2}") || !bytesText.matches("[1-9][0-9]{0,8}") ||
                !digest.matches("[0-9a-f]{64}") || !signatureText.matches("[A-Za-z0-9+/]{1,1024}={0,2}"))
            throw new IllegalArgumentException("feed_fields");
        int code = Integer.parseInt(codeText);
        int minSdk = Integer.parseInt(minText);
        long bytes = Long.parseLong(bytesText);
        if (bytes > MAX_APK_BYTES || minSdk < 26 || !validReleaseUrl(url, name))
            throw new IllegalArgumentException("feed_boundary");
        try {
            if (!CERT_SHA256.equals(hex(MessageDigest.getInstance("SHA-256").digest(pinnedCertificate))))
                throw new IllegalArgumentException("feed_certificate");
            X509Certificate x509 = (X509Certificate) CertificateFactory.getInstance("X.509")
                    .generateCertificate(new ByteArrayInputStream(pinnedCertificate));
            Signature verifier = Signature.getInstance("SHA256withRSA");
            verifier.initVerify(x509.getPublicKey());
            int signedLength = 0;
            for (int index = 0; index < 9; index++) signedLength += lines[index].length() + 1;
            verifier.update(wire, 0, signedLength);
            if (!verifier.verify(Base64.getDecoder().decode(signatureText)))
                throw new IllegalArgumentException("feed_signature");
        } catch (IllegalArgumentException invalid) {
            throw invalid;
        } catch (Exception invalid) {
            throw new IllegalArgumentException("feed_signature", invalid);
        }
        return new Release(code, name, minSdk, bytes, digest, url);
    }

    static Decision decide(Release release, int installedCode, int sdk) {
        if (release.versionCode <= installedCode) return Decision.CURRENT;
        return release.minSdk > sdk ? Decision.INCOMPATIBLE : Decision.AVAILABLE;
    }

    private static String field(String line, String key) {
        String prefix = key + "=";
        if (!line.startsWith(prefix) || line.length() == prefix.length())
            throw new IllegalArgumentException("feed_field_" + key);
        return line.substring(prefix.length());
    }

    private static boolean validReleaseUrl(String value, String version) {
        String expected = "https://github.com/JmooreOsirs/amplifai-phone-companion/releases/download/android-" +
                version + "/AMPLIFai-Phone-Android-" + version + ".apk";
        try {
            URI uri = URI.create(value);
            return expected.equals(value) && uri.getRawUserInfo() == null && uri.getRawQuery() == null &&
                    uri.getRawFragment() == null;
        } catch (IllegalArgumentException invalid) { return false; }
    }

    static String hex(byte[] bytes) {
        char[] digits = "0123456789abcdef".toCharArray();
        char[] text = new char[bytes.length * 2];
        for (int index = 0; index < bytes.length; index++) {
            int value = bytes[index] & 0xff;
            text[index * 2] = digits[value >>> 4];
            text[index * 2 + 1] = digits[value & 15];
        }
        return new String(text);
    }
}
