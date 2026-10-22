package ai.satoris.amplifai.phone;

import android.content.Context;
import android.content.pm.PackageInfo;
import android.content.pm.PackageInstaller;
import android.content.pm.PackageManager;
import android.content.pm.SigningInfo;
import android.content.pm.Signature;
import android.os.Build;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;

/** Verifies the downloaded archive and stages an ordinary Android package update. */
final class UpdateInstaller {
    record Staged(PackageInstaller.Session session, int sessionId) {}

    private UpdateInstaller() {}

    static Staged stage(Context context, AndroidUpdateFeed.Release release, File apk,
                        int installedCode, boolean automatic) throws IOException {
        verifyArchive(context, release, apk, installedCode);
        PackageInstaller installer = context.getPackageManager().getPackageInstaller();
        PackageInstaller.SessionParams params = new PackageInstaller.SessionParams(
                PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        params.setAppPackageName(AndroidUpdateFeed.PACKAGE);
        params.setSize(release.bytes());
        if (Build.VERSION.SDK_INT >= 31) params.setRequireUserAction(automatic
                ? PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED
                : PackageInstaller.SessionParams.USER_ACTION_REQUIRED);
        int id = installer.createSession(params);
        PackageInstaller.Session session = null;
        try {
            session = installer.openSession(id);
            try (FileInputStream input = new FileInputStream(apk);
                 OutputStream output = session.openWrite("base.apk", 0, release.bytes())) {
                byte[] block = new byte[8192];
                int count;
                long copied = 0;
                while ((count = input.read(block)) != -1) {
                    if (Thread.currentThread().isInterrupted()) throw new IOException("update_cancelled");
                    copied += count;
                    if (copied > release.bytes()) throw new IOException("apk_length");
                    output.write(block, 0, count);
                }
                if (copied != release.bytes()) throw new IOException("apk_length");
                session.fsync(output);
            }
            return new Staged(session, id);
        } catch (IOException | RuntimeException failed) {
            if (session != null) {
                try { session.abandon(); } finally { session.close(); }
            } else installer.abandonSession(id);
            throw failed;
        }
    }

    static void commit(Context context, Staged staged, int target) {
        PackageInstaller.Session session = staged.session();
        try {
            session.commit(UpdateInstallReceiver.pendingIntent(context, staged.sessionId(),
                    target).getIntentSender());
        } catch (RuntimeException failed) {
            UpdateInstallReceiver.clear(context, "failed");
            session.abandon();
            throw failed;
        } finally { session.close(); }
    }

    static void discard(Staged staged) {
        try { staged.session().abandon(); } finally { staged.session().close(); }
    }

    static void verifyArchive(Context context, AndroidUpdateFeed.Release release, File apk,
                              int installedCode) throws IOException {
        if (!apk.isFile() || apk.length() != release.bytes() || release.versionCode() <= installedCode)
            throw new IOException("apk_identity");
        MessageDigest fileDigest;
        try { fileDigest = MessageDigest.getInstance("SHA-256"); }
        catch (NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
        try (FileInputStream input = new FileInputStream(apk)) {
            byte[] block = new byte[8192];
            int count;
            while ((count = input.read(block)) != -1) {
                if (Thread.currentThread().isInterrupted()) throw new IOException("update_cancelled");
                fileDigest.update(block, 0, count);
            }
        }
        if (!AndroidUpdateFeed.hex(fileDigest.digest()).equals(release.sha256()))
            throw new IOException("apk_integrity");
        int flags = Build.VERSION.SDK_INT >= 28
                ? PackageManager.GET_SIGNING_CERTIFICATES : PackageManager.GET_SIGNATURES;
        PackageInfo info = context.getPackageManager().getPackageArchiveInfo(apk.getAbsolutePath(), flags);
        if (info == null || !AndroidUpdateFeed.PACKAGE.equals(info.packageName) ||
                info.applicationInfo == null || info.applicationInfo.minSdkVersion > Build.VERSION.SDK_INT ||
                archiveVersionCode(info) != release.versionCode() ||
                !release.versionName().equals(info.versionName)) throw new IOException("apk_identity");
        Signature[] signers;
        if (Build.VERSION.SDK_INT >= 28) {
            SigningInfo signing = info.signingInfo;
            signers = signing == null ? null : signing.getApkContentsSigners();
        } else signers = info.signatures;
        if (signers == null || signers.length != 1 ||
                !AndroidUpdateFeed.CERT_SHA256.equals(AndroidUpdateFeed.hex(sha256(signers[0].toByteArray()))))
            throw new IOException("apk_signer");
    }

    static int archiveVersionCode(PackageInfo info) {
        return Build.VERSION.SDK_INT >= 28 ? (int) info.getLongVersionCode() : info.versionCode;
    }

    private static byte[] sha256(byte[] value) {
        try { return MessageDigest.getInstance("SHA-256").digest(value); }
        catch (NoSuchAlgorithmException impossible) { throw new IllegalStateException(impossible); }
    }
}
