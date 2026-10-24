import java.nio.file.Files;
import java.nio.file.Path;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.interfaces.RSAKey;
import java.util.Arrays;
import java.util.Base64;
import java.util.HexFormat;

/** Trusted control source only; APK bytecode and candidate sources are never loaded. */
class AndroidFeedSigner {
    public static void main(String[] args) throws Exception {
        if (args.length != 2 && args.length != 4) throw new IllegalArgumentException("invalid signing operation");
        char[] password = System.getenv("ANDROID_RELEASE_PASSWORD").toCharArray();
        try {
            KeyStore store = KeyStore.getInstance("PKCS12");
            try (var stream = Files.newInputStream(Path.of(args[1]))) { store.load(stream, password); }
            String alias = "amplifai-android-release";
            if (store.size() != 1 || !store.isKeyEntry(alias) || store.getCertificateChain(alias).length != 1)
                throw new IllegalArgumentException("unexpected signing identity");
            byte[] cert = store.getCertificate(alias).getEncoded();
            String fingerprint = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(cert));
            if (!fingerprint.equals("9f3fa36a8f3b44b8083080828d772cd5990552275a6ffbc68a097826e4e3c4c2"))
                throw new IllegalArgumentException("publisher certificate changed");
            PrivateKey key = (PrivateKey) store.getKey(alias, password);
            if (!(key instanceof RSAKey) || ((RSAKey) key).getModulus().bitLength() != 3072)
                throw new IllegalArgumentException("publisher key algorithm changed");
            if (args[0].equals("check") && args.length == 2) return;
            if (!args[0].equals("feed") || args.length != 4) throw new IllegalArgumentException("invalid operation");
            byte[] body = Files.readAllBytes(Path.of(args[2]));
            if (body.length > 2048) throw new IllegalArgumentException("feed exceeds bound");
            Signature signer = Signature.getInstance("SHA256withRSA");
            signer.initSign(key); signer.update(body);
            byte[] signature = signer.sign();
            signer.initVerify(store.getCertificate(alias)); signer.update(body);
            if (!signer.verify(signature)) throw new IllegalStateException("feed signature did not verify");
            byte[] suffix = ("signature=" + Base64.getEncoder().encodeToString(signature) + "\n").getBytes(java.nio.charset.StandardCharsets.US_ASCII);
            byte[] feed = Arrays.copyOf(body, body.length + suffix.length);
            System.arraycopy(suffix, 0, feed, body.length, suffix.length);
            Files.write(Path.of(args[3]), feed, java.nio.file.StandardOpenOption.CREATE_NEW);
        } finally { Arrays.fill(password, '\0'); }
    }
}
